import time

from playwright.async_api import async_playwright

from scanner import get_axe_source, guard_route, compute_score
from security import validate_url_async


AXE_JS = """async () => await axe.run(document, {
    runOnly: {
        type: 'tag',
        values: [
            'wcag2a', 'wcag2aa',
            'wcag21a', 'wcag21aa',
            'wcag22aa', 'best-practice'
        ]
    }
})"""


APPLY_JS = r"""([selector, patch]) => {
    const el = document.querySelector(selector);

    if (!el) {
        return {ok: false, reason: "selector_not_found"};
    }

    window.__fixproofUndo = null;

    if (patch.op === "set_attributes") {
        const allowed = new Set([
            "aria-label", "alt", "lang",
            "role", "title", "tabindex"
        ]);

        const attrs = patch.attributes || {};
        const entries = Object.entries(attrs);

        if (!entries.length || entries.some(([key, value]) =>
            !allowed.has(key) ||
            typeof value !== "string" ||
            value.length > 200
        )) {
            return {ok: false, reason: "unsafe_attributes"};
        }

        const previous = {};

        for (const [key, value] of entries) {
            previous[key] = el.getAttribute(key);
            el.setAttribute(key, value);
        }

        window.__fixproofUndo = () => {
            for (const [key, value] of Object.entries(previous)) {
                if (value === null) {
                    el.removeAttribute(key);
                } else {
                    el.setAttribute(key, value);
                }
            }
        };

        return {ok: true};
    }

    if (patch.op === "insert_element") {
        const allowedTags = new Set([
            "main", "h1", "h2", "nav",
            "header", "footer", "section", "p"
        ]);

        if (!allowedTags.has(patch.tag)) {
            return {ok: false, reason: "unsafe_tag"};
        }

        const fresh = document.createElement(patch.tag);
        fresh.textContent = String(patch.text || "").slice(0, 200);

        // A main landmark should contain existing page content.
        if (patch.tag === "main") {
            const body = document.body;

            if (!body) {
                return {ok: false, reason: "body_not_found"};
            }

            if (body.querySelector("main")) {
                return {ok: false, reason: "main_already_exists"};
            }

            const originalChildren = Array.from(body.childNodes);

            const contentNodes = originalChildren.filter(node => {
                if (node.nodeType === Node.TEXT_NODE) {
                    return node.textContent.trim().length > 0;
                }

                if (node.nodeType !== Node.ELEMENT_NODE) {
                    return false;
                }

                return ![
                    "HEADER", "NAV", "FOOTER"
                ].includes(node.tagName);
            });

            if (!contentNodes.length) {
                return {ok: false, reason: "no_content_to_wrap"};
            }

            body.appendChild(fresh);

            for (const node of contentNodes) {
                fresh.appendChild(node);
            }

            window.__fixproofUndo = () => {
                body.replaceChildren(...originalChildren);
            };

            return {ok: true};
        }

        let parent = el;

        if (el === document.documentElement) {
            parent = document.body;
        }

        if (!parent) {
            return {ok: false, reason: "parent_not_found"};
        }

        const position = patch.position === "prepend"
            ? "prepend"
            : "append";

        parent[position](fresh);

        window.__fixproofUndo = () => fresh.remove();

        return {ok: true};
    }

    return {ok: false, reason: "unsupported_operation"};
}"""


UNDO_JS = """() => {
    if (window.__fixproofUndo) {
        window.__fixproofUndo();
        window.__fixproofUndo = null;
    }
}"""


def _baseline_counts(violations: list) -> dict:
    """Get the original number of affected nodes per axe rule."""
    counts = {}

    for violation in violations:
        rule_id = violation.get("id")
        if not rule_id:
            continue

        counts[rule_id] = int(
            violation.get(
                "total_nodes",
                len(violation.get("nodes", []))
            )
        )

    return counts


def _current_counts(results: dict) -> dict:
    """Get current affected-node counts per axe rule."""
    counts = {}

    for violation in results.get("violations", []):
        rule_id = violation.get("id")

        if not rule_id:
            continue

        counts[rule_id] = len(violation.get("nodes", []))

    return counts


async def _start_page(url: str):
    await validate_url_async(url)

    playwright = await async_playwright().start()
    browser = None
    context = None

    try:
        browser = await playwright.chromium.launch(
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage"
            ]
        )

        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            service_workers="block",
        )

        page = await context.new_page()

        await page.route("**/*", guard_route)

        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=22000
        )

        try:
            await page.wait_for_load_state(
                "networkidle",
                timeout=3000
            )
        except Exception:
            pass

        await page.add_script_tag(
            content=await get_axe_source()
        )

        return playwright, browser, context, page

    except Exception:
        if context:
            await context.close()

        if browser:
            await browser.close()

        await playwright.stop()
        raise


async def verify_groups(
    url: str,
    groups: list,
    baseline_violations: list,
    max_patches: int = 10,
    budget_s: int = 45,
) -> dict:

    await validate_url_async(url)

    baseline = _baseline_counts(baseline_violations)

    deadline = time.monotonic() + budget_s
    patch_count = 0
    verified_patches = []

    playwright, browser, context, page = await _start_page(url)

    try:
        # Re-scan the original page before attempting remediation.
        initial_results = await page.evaluate(AXE_JS)
        initial_counts = _current_counts(initial_results)

        if not initial_counts and baseline:
            raise RuntimeError(
                "Could not reproduce the baseline axe scan."
            )

        # Detect major baseline changes before applying patches.
        for rule_id, original_count in baseline.items():
            current_count = initial_counts.get(rule_id, 0)

            if current_count > original_count:
                raise RuntimeError(
                    f"Baseline changed for {rule_id}. "
                    "Please run a fresh scan."
                )

        # Verify every patch independently.
        for group in groups:
            for patch in group.get("patches", []):

                if (
                    patch_count >= max_patches
                    or time.monotonic() >= deadline
                ):
                    patch["status"] = "skipped"
                    patch["detail"] = (
                        "Verification time or patch limit reached."
                    )
                    continue

                patch_count += 1

                rule_id = patch.get("rule_id")
                selector = patch.get("selector")

                # Only allow selectors reported by the original scan.
                allowed_selectors = set()

                for violation in baseline_violations:
                    if violation.get("id") != rule_id:
                        continue

                    for node in violation.get("nodes", []):
                        selectors = node.get("selectors") or (
                            [node.get("selector")]
                            if node.get("selector")
                            else []
                        )

                        allowed_selectors.update(
                            value for value in selectors if value
                        )

                    allowed_selectors.update(
                        violation.get("all_selectors", [])
                    )

                if not selector or selector not in allowed_selectors:
                    patch["status"] = "rejected"
                    patch["detail"] = (
                        "Selector was not present in the original "
                        "affected nodes."
                    )
                    continue

                try:
                    applied = await page.evaluate(
                        APPLY_JS,
                        [selector, patch]
                    )

                    if not applied.get("ok"):
                        patch["status"] = "failed"
                        patch["detail"] = (
                            "Patch was not applied: "
                            f"{applied.get('reason')}."
                        )
                        continue

                    try:
                        results = await page.evaluate(AXE_JS)
                        current = _current_counts(results)

                        baseline_rule_count = baseline.get(rule_id, 0)
                        current_rule_count = current.get(rule_id, 0)

                        # The targeted rule must actually improve.
                        if current_rule_count >= baseline_rule_count:
                            patch["status"] = "failed"
                            patch["detail"] = (
                                "The targeted axe violation did not "
                                "decrease after applying the patch."
                            )
                            continue

                        # No rule should gain additional affected nodes.
                        regressions = []

                        for rid, current_count in current.items():
                            original_count = baseline.get(rid, 0)

                            if current_count > original_count:
                                regressions.append(
                                    f"{rid} "
                                    f"(+{current_count - original_count})"
                                )

                        if regressions:
                            patch["status"] = "failed"
                            patch["detail"] = (
                                "New affected nodes detected: "
                                + ", ".join(regressions[:4])
                            )
                            continue

                        patch["status"] = "verified"
                        patch["detail"] = (
                            "Targeted axe violation count decreased "
                            "and no new affected-node regressions "
                            "were detected in the isolated test."
                        )

                        verified_patches.append(dict(patch))

                    finally:
                        # Restore the page before testing the next patch.
                        await page.evaluate(UNDO_JS)

                except Exception as exc:
                    patch["status"] = "failed"
                    patch["detail"] = (
                        f"{type(exc).__name__}: {str(exc)[:150]}"
                    )

                    try:
                        await page.evaluate(UNDO_JS)
                    except Exception:
                        pass

        # Apply independently verified patches together.
        after_score = None
        final_counts = None

        if verified_patches and time.monotonic() < deadline:

            for patch in verified_patches:
                applied = await page.evaluate(
                    APPLY_JS,
                    [patch["selector"], patch]
                )

                if not applied.get("ok"):
                    patch["status"] = "failed"
                    patch["detail"] = (
                        "Patch could not be reapplied for "
                        "the final combined scan."
                    )
                    continue

                # Keep this successful change in the final DOM.
                await page.evaluate(
                    "() => { window.__fixproofUndo = null; }"
                )

            final_results = await page.evaluate(AXE_JS)
            final_counts = _current_counts(final_results)

            final_violations = []

            for violation in final_results.get("violations", []):
                final_violations.append({
                    "impact": violation.get("impact") or "minor",
                    "nodes": violation.get("nodes", []),
                    "total_nodes": len(
                        violation.get("nodes", [])
                    ),
                })

            after_score = compute_score(
                final_violations,
                len(final_results.get("passes", []))
            )

        return {
            "groups": groups,
            "after_score": after_score,
        }

    finally:
        await context.close()
        await browser.close()
        await playwright.stop()