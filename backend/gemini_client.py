import asyncio
import hashlib
import json
import logging
import os
from typing import Any

from google import genai
from google.genai import types

logger = logging.getLogger("fixproof.gemini")

_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LIMIT = 100

_ALLOWED_OPERATIONS = {"set_attributes", "insert_element"}
_ALLOWED_ATTRIBUTES = {"aria-label", "alt", "lang", "role", "title", "tabindex"}
_ALLOWED_TAGS = {"main", "h1", "h2", "nav", "header", "footer", "section", "p"}
_ALLOWED_POSITIONS = {"append", "prepend"}

SYSTEM = """
You are FixProof, an accessibility remediation assistant.

Your job is to:
1. Group related axe-core violations into 1–5 root-cause groups.
2. Explain each group clearly for a junior developer.
3. Propose safe, minimal, declarative DOM patches when the supplied evidence
   supports a concrete fix.

Rules:
- Analyze ONLY the axe-core data supplied by FixProof.
- Never invent axe rule IDs, selectors, affected elements, or page content.
- Every rule_id in a patch must appear in that group's rule_ids.
- Every patch selector must exactly match a selector supplied in the input
  examples for that same rule.
- Use only these operations:
  - set_attributes: add or update safe accessibility attributes.
  - insert_element: insert a simple semantic element relative to the supplied
    selector, using only an allowed tag.
- For insert_element, choose a tag and short text only when the evidence
  supports it. Do not invent meaningful page copy. Empty text is acceptable
  for semantic landmarks such as main.
- For missing main landmarks, consider inserting a <main> element.
- For a missing level-one heading, insert an <h1> only if a suitable concise
  heading can be derived from the supplied page/violation evidence. Otherwise
  explain why a safe automatic fix cannot be inferred.
- For attribute-related violations, use set_attributes only when the supplied
  HTML and failure summary justify the exact attribute and value.
- Do not propose JavaScript, event handlers, CSS, arbitrary HTML, outerHTML
  replacement, navigation, network requests, or destructive changes.
- Do not claim that a patch is verified. Verification is performed separately.
- Prefer one minimal patch per safely remediable issue. Do not emit speculative
  patches merely to satisfy a count.
- If a group has no safe patch, return an empty patches array and explain the
  limitation in recommendation.
- Populate why_it_matters, affected_users, and recommendation with useful,
  concise content. Do not leave them blank.

Return JSON only, matching the requested structure.
""".strip()


def _compact(violations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the Gemini request focused while preserving usable evidence."""
    top = sorted(
        violations,
        key=lambda item: {
            "critical": 0,
            "serious": 1,
            "moderate": 2,
            "minor": 3,
        }.get(item.get("impact", "minor"), 4),
    )[:8]

    compact: list[dict[str, Any]] = []
    for violation in top:
        examples: list[dict[str, str]] = []

        for node in violation.get("nodes", [])[:3]:
            selectors = node.get("selectors") or (
                [node.get("selector")] if node.get("selector") else []
            )

            for selector in selectors[:2]:
                if not isinstance(selector, str) or not selector.strip():
                    continue

                examples.append(
                    {
                        "selector": selector[:500],
                        "html": str(node.get("html", ""))[:300],
                        "failure_summary": str(
                            node.get("failure_summary", "")
                        )[:300],
                    }
                )

        compact.append(
            {
                "id": str(violation.get("id", "")),
                "impact": str(violation.get("impact", "minor")),
                "description": str(violation.get("description", ""))[:500],
                "help": str(violation.get("help", ""))[:500],
                "wcag": violation.get("tags", [])[:8],
                "total_nodes": violation.get(
                    "total_nodes", len(violation.get("nodes", []))
                ),
                "examples": examples[:5],
            }
        )

    return compact


def _validate(data: Any, compact: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Validate model output against the scan evidence and the verifier's
    deliberately small patch allowlist. Invalid patches are discarded.
    """
    if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
        raise ValueError("Gemini response must contain a groups array.")

    allowed_selectors: dict[str, set[str]] = {}
    for item in compact:
        rule_id = item.get("id")
        if not rule_id:
            continue
        allowed_selectors[rule_id] = {
            example["selector"]
            for example in item.get("examples", [])
            if isinstance(example.get("selector"), str)
        }

    clean_groups: list[dict[str, Any]] = []

    for group in data["groups"][:5]:
        if not isinstance(group, dict):
            continue

        raw_rule_ids = group.get("rule_ids", [])
        if not isinstance(raw_rule_ids, list):
            continue

        rule_ids = [
            rule_id
            for rule_id in raw_rule_ids
            if isinstance(rule_id, str) and rule_id in allowed_selectors
        ]
        if not rule_ids:
            continue

        clean_patches: list[dict[str, Any]] = []
        raw_patches = group.get("patches", [])
        if isinstance(raw_patches, list):
            for patch in raw_patches[:4]:
                if not isinstance(patch, dict):
                    continue

                rule_id = patch.get("rule_id")
                selector = patch.get("selector")
                operation = patch.get("op")

                if rule_id not in rule_ids:
                    continue
                if selector not in allowed_selectors.get(rule_id, set()):
                    continue
                if operation not in _ALLOWED_OPERATIONS:
                    continue

                clean_patch: dict[str, Any] = {
                    "rule_id": rule_id,
                    "selector": selector,
                    "op": operation,
                    "note": str(patch.get("note", ""))[:180],
                }

                if operation == "set_attributes":
                    attributes = patch.get("attributes")
                    if not isinstance(attributes, dict) or not attributes:
                        continue
                    if len(attributes) > 4:
                        continue

                    safe_attributes = {
                        str(key): str(value)[:200]
                        for key, value in attributes.items()
                        if key in _ALLOWED_ATTRIBUTES
                        and isinstance(value, (str, int, float, bool))
                        and str(value).strip()
                    }
                    if not safe_attributes:
                        continue

                    clean_patch["attributes"] = safe_attributes

                elif operation == "insert_element":
                    tag = patch.get("tag")
                    position = patch.get("position", "append")
                    if tag not in _ALLOWED_TAGS:
                        continue
                    if position not in _ALLOWED_POSITIONS:
                        continue

                    text = patch.get("text", "")
                    if not isinstance(text, str):
                        continue

                    clean_patch.update(
                        {
                            "tag": tag,
                            "text": text[:200],
                            "position": position,
                            "attributes": {},
                        }
                    )

                clean_patches.append(clean_patch)

        clean_groups.append(
            {
                "title": str(group.get("title", "Accessibility issue"))[:100],
                "rule_ids": rule_ids,
                "explanation": str(group.get("explanation", ""))[:500],
                "why_it_matters": str(group.get("why_it_matters", ""))[:500],
                "affected_users": [
                    str(value)[:80]
                    for value in (
                        group.get("affected_users", [])
                        if isinstance(group.get("affected_users", []), list)
                        else []
                    )[:5]
                ],
                "recommendation": str(group.get("recommendation", ""))[:500],
                "patches": clean_patches,
            }
        )

    return {"groups": clean_groups}


async def analyze_violations(
    url: str, violations: list[dict[str, Any]]
) -> dict[str, Any]:
    """Analyze axe violations with Gemini and return validated patch proposals."""
    if not violations:
        return {"groups": []}

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "replace_with_your_key":
        raise RuntimeError(
            "GEMINI_API_KEY is missing. Add it to backend/.env."
        )

    compact = _compact(violations)
    cache_key = hashlib.sha256(
        json.dumps([url, compact], sort_keys=True).encode("utf-8")
    ).hexdigest()

    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    model_name = os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip()
    timeout_s = float(os.getenv("GEMINI_TIMEOUT", "75"))

    prompt = f"""
Analyze the accessibility scan below.

Patch-generation requirements:
- For each group, inspect whether the supplied evidence supports a safe fix.
- Return at least one patch for a group when a safe, concrete fix is supported.
- Do not force a patch when the evidence is insufficient.
- Use exact selector strings from the examples. Never use a selector that is
  absent from the input.
- For a missing landmark, an insert_element patch may use tag "main".
- For a missing level-one heading, use tag "h1" only when the heading text
  can be responsibly inferred; otherwise explain the limitation.
- For attribute violations, use set_attributes with only the specific safe
  attribute supported by the evidence.
- Every patch must include rule_id, selector, op, and note.
- insert_element must also include tag, text, and position ("append" or
  "prepend"). set_attributes must include an attributes object.
- Keep each patch minimal. Do not claim it has been applied or verified.

Page URL:
{url}

Axe scan evidence:
{json.dumps(compact, ensure_ascii=False)}
""".strip()

    client = genai.Client(api_key=api_key)
    try:
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM,
                    response_mime_type="application/json",
                    temperature=0.1,
                    max_output_tokens=4000,
                ),
            ),
            timeout=timeout_s,
        )

        response_text = response.text
        if not response_text:
            raise RuntimeError("Gemini returned an empty response.")

        try:
            raw_data = json.loads(response_text)
        except json.JSONDecodeError as exc:
            logger.warning("Gemini returned malformed JSON: %s", exc)
            raise RuntimeError(
                "Gemini returned invalid JSON. Please retry."
            ) from exc

        result = _validate(raw_data, compact)

        raw_patch_count = sum(
            len(group.get("patches", []))
            for group in raw_data.get("groups", [])
            if isinstance(group, dict)
            and isinstance(group.get("patches", []), list)
        )
        valid_patch_count = sum(
            len(group["patches"]) for group in result["groups"]
        )

        logger.info(
            "Gemini analysis complete: %d groups, %d raw patches, "
            "%d validated patches",
            len(result["groups"]),
            raw_patch_count,
            valid_patch_count,
        )

        if raw_patch_count and not valid_patch_count:
            logger.warning(
                "Gemini proposed patches, but all were rejected by validation. "
                "Check rule IDs, selectors, and allowed patch operations."
            )

        if len(_CACHE) >= _CACHE_LIMIT:
            _CACHE.clear()
        _CACHE[cache_key] = result
        return result

    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            f"Gemini timed out after {timeout_s:g} seconds. "
            "Try again or check API capacity/quota."
        ) from exc
    except RuntimeError:
        raise
    except Exception as exc:
        message = str(exc).replace(api_key, "[REDACTED]")
        raise RuntimeError(
            f"Gemini request failed: {type(exc).__name__}: {message[:220]}"
        ) from exc
    finally:
        await client.aio.aclose()

