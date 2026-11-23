"""Pass 2 verification of pass-1 claims.

Three checks, in order:
1. One batched LLM call per app re-reads every cited claim and answers
   supported | contradicted | not_found.
2. Code-only snippet check: each snippet must actually appear in its cited page
   (exact normalized match or high word-overlap fuzzy match).
3. Code-only consistency rules.

Only supported, in-page claims survive into data/results.json.
"""

import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from llm import complete_json_detailed
from schema import validate_record
import tools

INPUT_FILE = Path("data/pass1.json")
OUTPUT_FILE = Path("data/results.json")
PAGE_CHARS = 2000
VERIFIABLE_FIELDS = ("auth_methods", "self_serve", "api_surface", "buildability")

CHECK_SYSTEM = """You verify claims against web-page text.
For each claim, answer ONLY one of: supported, contradicted, not_found.
- supported: the page text explicitly says what the claim says.
- contradicted: the page text says the opposite.
- not_found: the page text does not address the claim.
Use ONLY the supplied PAGE texts. Never guess. Copy each claim's "index" into
your answer. Return ONE JSON object, no markdown, no commentary."""

CHECK_TEMPLATE = {
    "checks": [
        {"index": 0, "verdict": "supported|contradicted|not_found", "reason": "short reason"}
    ]
}


def _write_json(path: Path, data) -> None:
    """Write via a same-directory temp file; avoids partial/truncated artifacts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    for attempt in range(5):
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
            return
        except OSError:
            if attempt == 4:
                raise
    raise AssertionError("unreachable")


def _page_text(url: str) -> str:
    path = tools.PAGES_DIR / (hashlib.sha256(url.encode("utf-8")).hexdigest() + ".txt")
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).lower()).strip()


def _snippet_in_page(snippet: str, page: str) -> bool:
    """Exact normalized match, or >=70% word overlap for near-quotes."""
    if not snippet or not page:
        return False
    needle, haystack = _norm(snippet), _norm(page)
    if needle in haystack:
        return True
    words = set(re.findall(r"\w+", needle))
    if len(words) < 3:
        return False
    found = set(re.findall(r"\w+", haystack))
    return len(words & found) / len(words) >= 0.70


def _value_for(record: dict, field: str):
    if field == "auth_methods":
        return record["auth_methods"]
    if field == "self_serve":
        return record["self_serve"]
    if field == "buildability":
        return record["buildability"]
    if field == "api_surface":
        api = record["api_surface"]
        return {key: api.get(key) for key in ("type", "breadth", "existing_mcp")}
    return "?"


def _claims(record: dict) -> tuple[list[dict], dict[str, str]]:
    claims, pages = [], {}
    for index, item in enumerate(record["evidence"]):
        field = item.get("field")
        if field not in VERIFIABLE_FIELDS:
            continue
        url = item.get("url", "")
        pages[url] = _page_text(url)
        claims.append({
            "index": index,
            "field": field,
            "value": _value_for(record, field),
            "url": url,
            "snippet": item.get("snippet", ""),
        })
    return claims, pages


def _verdicts(result: dict) -> dict[int, str]:
    parsed = {}
    checks = result.get("checks")
    if not isinstance(checks, list):
        return parsed
    for item in checks:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        verdict = str(item.get("verdict", "")).lower().replace(" ", "_")
        if "contrad" in verdict:
            parsed[index] = "contradicted"
        elif "not_found" in verdict or verdict == "notfound":
            parsed[index] = "not_found"
        elif "support" in verdict:
            parsed[index] = "supported"
    return parsed


def _reset_unbacked(record: dict, kept: list[dict]) -> None:
    """Any field without surviving evidence becomes unknown again."""
    fields = {item["field"] for item in kept}
    if "auth_methods" not in fields:
        record["auth_methods"] = ["unknown"]
    if "self_serve" not in fields:
        record["self_serve"] = "unknown"
        record["self_serve_note"] = "unknown"
        record["main_blocker"] = "unknown"
        record["blocker_note"] = "unknown"
    if "api_surface" not in fields:
        record["api_surface"] = {
            "type": ["unknown"], "breadth": "unknown",
            "existing_mcp": "unknown", "mcp_url": None,
        }
    if "buildability" not in fields:
        record["buildability"] = "unknown"


def _consistency_rules(record: dict) -> None:
    if record["self_serve"] == "gated" and record["buildability"] == "yes":
        record["buildability"] = "partial"
    if record["auth_methods"] == ["unknown"]:
        record["buildability"] = "unknown"
    if not record["evidence"]:
        record["main_blocker"] = "unknown"
        record["blocker_note"] = "unknown"


def verify_record(record: dict) -> dict:
    """Verify one pass-1 record; return a pass-2 record."""
    claims, pages = _claims(record)
    if record.get("status") == "error":
        carried = copy.deepcopy(record)
        carried["pass"] = 2
        return carried
    if not claims:
        new = copy.deepcopy(record)
        new["pass"] = 2
        new["evidence"] = []
        new["status"] = "needs_human"
        new["needs_human_reason"] = "no pass-1 evidence to verify"
        new["error_reason"] = "none"
        return new

    page_blocks = "\n\n".join(
        f"--- PAGE\nURL: {url}\nTEXT:\n{text[:PAGE_CHARS]}\n---"
        for url, text in sorted(pages.items())
    )
    user = (
        f"APP: {record['app']}\n\n"
        f"Verify each claim below against its cited page.\n"
        f"Fill exactly these keys:\n{json.dumps(CHECK_TEMPLATE, indent=2)}\n\n"
        f"CLAIMS:\n{json.dumps(claims, indent=2, ensure_ascii=False)}\n\n"
        f"PAGES:\n{page_blocks}"
    )
    result, meta = complete_json_detailed(CHECK_SYSTEM, user)

    new = copy.deepcopy(record)
    new["pass"] = 2
    new["llm_model"] = meta["model"]
    if "_error" in result:
        new["status"] = "error"
        new["error_reason"] = "LLM_API_ERROR: " + str(result["_error"])[:400]
        new["needs_human_reason"] = "not applicable (status=error; retry next run)"
        print(f"[pass2] {new['app']}: verification failed: {str(result['_error'])[:160]}",
              file=sys.stderr, flush=True)
        return new

    judged = _verdicts(result)
    kept, dropped = [], []
    for index, item in enumerate(record["evidence"]):
        verdict = judged.get(index, "not_found")
        in_page = _snippet_in_page(item.get("snippet", ""), pages.get(item.get("url", ""), ""))
        if verdict == "supported" and in_page:
            kept.append(item)
        else:
            dropped.append({
                "field": item.get("field"),
                "verdict": verdict,
                "snippet_in_page": in_page,
            })

    new["evidence"] = kept
    new["error_reason"] = "none"
    _reset_unbacked(new, kept)
    _consistency_rules(new)

    required = {
        "auth_methods": new["auth_methods"] != ["unknown"],
        "api_surface.type": new["api_surface"]["type"] != ["unknown"],
        "self_serve": new["self_serve"] != "unknown",
    }
    missing = [name for name, ok in required.items() if not ok]
    reasons = []
    if dropped:
        reasons.append(f"{len(dropped)} unsupported/out-of-page claims dropped")
    if missing:
        reasons.append("missing evidence-backed: " + ", ".join(missing))
    new["status"] = "needs_human" if missing else "ok"
    new["needs_human_reason"] = " | ".join(reasons) if missing else "none"

    problems = validate_record(new)
    if problems:
        print(f"[pass2] validation issues for {new['app']}: {problems}", file=sys.stderr)
    print(
        f"[pass2] {new['app']}: kept={len(kept)} dropped={len(dropped)} "
        f"status={new['status']} model={meta['model']}",
        flush=True,
    )
    return new


def run(input_path: Path = INPUT_FILE, output_path: Path = OUTPUT_FILE,
        limit: int | None = None, apps: set[str] | None = None) -> list[dict]:
    """Verify pass-1 records, writing results incrementally for resume."""
    source = json.loads(input_path.read_text(encoding="utf-8"))
    if limit is not None:
        source = source[:limit]
    if apps is not None:
        source = [record for record in source if record["app"] in apps]

    existing = {}
    if output_path.exists():
        existing = {
            record["app"]: record
            for record in json.loads(output_path.read_text(encoding="utf-8"))
        }

    for record in source:
        name = record["app"]
        if name in existing and existing[name].get("pass") == 2 \
                and existing[name].get("status") != "error":
            print(f"[pass2] skipping already-verified {name}", flush=True)
            continue
        existing[name] = verify_record(record)
        ordered = sorted(existing.values(), key=lambda item: (item.get("id", 9999), item["app"]))
        _write_json(output_path, ordered)

    ordered = sorted(existing.values(), key=lambda item: (item.get("id", 9999), item["app"]))
    counts = {
        status: sum(item.get("status") == status for item in ordered)
        for status in ("ok", "needs_human", "error")
    }
    print(f"[pass2] wrote {len(ordered)} records to {output_path}; counts={counts}")
    return ordered
