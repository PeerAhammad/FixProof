import asyncio
import threading
import urllib.request

from playwright.async_api import async_playwright, Page

from security import validate_url_async, validate_request_url, UnsafeURL

AXE_CDN = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.0/axe.min.js"
_axe_source = None
_axe_lock = threading.Lock()


def _download_axe() -> str:
    global _axe_source
    if _axe_source is not None:
        return _axe_source
    with _axe_lock:
        if _axe_source is None:
            request = urllib.request.Request(
                AXE_CDN, headers={"User-Agent": "FixProof/1.0"}
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                source = response.read(2_500_000).decode("utf-8")
            if "axe" not in source[:10000].lower():
                raise RuntimeError("Could not load axe-core from its CDN.")
            _axe_source = source
    return _axe_source


async def get_axe_source() -> str:
    return await asyncio.to_thread(_download_axe)


async def guard_route(route):
    """Revalidate every network request, including redirects and subresources."""
    try:
        await validate_request_url(route.request.url)
        await route.continue_()
    except Exception:
        await route.abort()


IMPACT_WEIGHT = {"critical": 10, "serious": 6, "moderate": 3, "minor": 1}


def compute_score(violations: list, passes: int = 0) -> int:
    """Custom demo heuristic; this is not an official WCAG conformance score."""
    penalty = 0
    for violation in violations:
        nodes = int(violation.get("total_nodes", len(violation.get("nodes", []))))
        penalty += IMPACT_WEIGHT.get(violation.get("impact"), 1) * (
            1 + min(nodes, 10) * 0.2
        )
    return max(0, min(100, round(100 - penalty)))


def _extract_violations(results: dict) -> list:
    output = []
    for violation in results.get("violations", []):
        all_nodes = violation.get("nodes", [])
        nodes = []
        for node in all_nodes[:30]:
            targets = node.get("target") or []
            selectors = [str(target)[:500] for target in targets if isinstance(target, str)]
            nodes.append({
                "html": str(node.get("html", ""))[:500],
                "selector": selectors[0] if selectors else "",
                "selectors": selectors,
                "failure_summary": str(node.get("failureSummary") or "")[:700],
            })
        all_selectors = []
        for node in all_nodes:
            all_selectors.extend(
                str(target)[:500]
                for target in (node.get("target") or [])
                if isinstance(target, str)
            )
        output.append({
            "id": violation["id"],
            "impact": violation.get("impact") or "minor",
            "description": violation.get("description", ""),
            "help": violation.get("help", ""),
            "helpUrl": violation.get("helpUrl", ""),
            "tags": [tag for tag in violation.get("tags", []) if tag.startswith("wcag")],
            "nodes": nodes,
            "all_selectors": sorted(set(all_selectors)),
            "total_nodes": len(all_nodes),
        })
    return output


async def _open_page(url: str):
    await validate_url_async(url)
    playwright = await async_playwright().start()
    browser = None
    try:
        browser = await playwright.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            ignore_https_errors=False,
            service_workers="block",
        )
        page: Page = await context.new_page()
        await page.route("**/*", guard_route)
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        try:
            await page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
        await page.add_script_tag(content=await get_axe_source())
        return playwright, browser, context, page
    except Exception as exc:
        if browser:
            await browser.close()
        await playwright.stop()
        if isinstance(exc, UnsafeURL):
            raise
        raise RuntimeError(f"Could not load the page: {type(exc).__name__}: {str(exc)[:180]}")


async def scan_url(url: str) -> dict:
    playwright, browser, context, page = await _open_page(url)
    try:
        results = await page.evaluate(
            """async () => await axe.run(document, {
              runOnly: { type: 'tag', values: [
                'wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa','best-practice'
              ]}
            })"""
        )
        violations = _extract_violations(results)
        counts = {key: 0 for key in IMPACT_WEIGHT}
        for violation in violations:
            counts[violation["impact"]] += 1

        return {
            "url": url,
            "score": compute_score(violations, len(results.get("passes", []))),
            "score_label": "Estimated accessibility health score (custom heuristic)",
            "counts": counts,
            "passed": len(results.get("passes", [])),
            "violations": violations,
        }
    except Exception as exc:
        raise RuntimeError(f"Accessibility scan failed: {type(exc).__name__}: {str(exc)[:180]}")
    finally:
        await context.close()
        await browser.close()
        await playwright.stop()
