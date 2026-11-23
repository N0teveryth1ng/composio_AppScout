"""Research pipeline. Pass 1 is the baseline; it is tuned before ground-truth measurement."""

import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

import tools
from llm import complete_json_detailed
from schema import (
    API_TYPE_VALUES,
    AUTH_VALUES,
    BREADTH_VALUES,
    BLOCKER_VALUES,
    BUILDABILITY_VALUES,
    EVIDENCE_FIELDS,
    MCP_VALUES,
    SELF_SERVE_VALUES,
    empty_record,
    validate_record,
)

APPS_FILE = Path("apps.json")
OUT_FILE = Path("data/pass1.json")
FROZEN_FILE = Path("data/pass1_frozen.json")  # baseline: written once, never edited
RUN_LOG = Path("data/pass1_run.log")
TOOLKITS_URL = "https://docs.composio.dev/toolkits"
MAX_PAGES_PER_TOPIC = 3
PAGE_CHARS = 4000  # small prompts keep Groq's free-tier token-per-minute limit happy
ACCESS_PAGE_CHARS = 2000  # access gets more pages, so keep each one shorter
DIRECT_PATHS = ("/pricing", "/signup", "/developers", "/docs", "/api", "/free-trial")
DIRECT_SUBDOMAINS = ("developers", "docs", "help")

# One query per topic. There are exactly three, which is the per-app credit cap.
TOPIC_QUERIES = {
    "auth_api": "{app} API authentication OAuth API key developer docs",
    "access": "{app} developer sign up free trial sandbox",
    "mcp": "{app} MCP server model context protocol docs",
}

AUTH_SYSTEM = """You extract facts from web-page text.
Return ONLY these keys: one_liner, auth_methods, api_surface, evidence.
- Use ONLY the supplied PAGE texts. Never guess. Unknown = "unknown" or ["unknown"].
- one_liner: a short description. It does NOT need evidence.
- auth_methods: subset of OAuth2, API key, Basic, Token, Other, unknown.
- api_surface.type: subset of REST, GraphQL, SOAP, SDK only, none, unknown.
- api_surface.breadth: broad, medium, narrow, unknown.
- Every known auth or API-type value needs an evidence entry with field "auth_methods"
  or "api_surface.type", a url copied exactly from a PAGE URL, and a short snippet.
- Return ONE JSON object, no markdown, no commentary."""

ACCESS_SYSTEM = """You extract facts from web-page text.
Return ONLY these keys: self_serve, self_serve_note, buildability, main_blocker, blocker_note, evidence.
- Use ONLY the supplied PAGE texts. Never guess. Unknown = "unknown".
- self_serve: self_serve, trial_or_paid, gated, unknown.
- buildability: yes, partial, no, unknown.
- main_blocker: none, partner_gated, paid_only, no_public_api, admin_approval,
  contact_sales, auth_complexity, other.
- Every known self_serve or buildability value needs an evidence entry with field
  "self_serve" or "buildability", a url copied exactly from a PAGE URL, and a short snippet.
- self_serve_note is MANDATORY whenever self_serve is known: a short reason quoted
  from the page. Never leave it "unknown" if you set self_serve.
- Prefer pages on the app's own domain (see HINT): sign-up, free-trial, sandbox and
  pricing pages. Review blogs, forums and community threads are weak sources.
- Pages marked "SOURCE: official (direct fetch)" are on the app's own domain; rely on
  them over untagged search results when stating access terms.
- main_blocker is ONLY about API access: a paid API plan, no public API, or approval
  needed. A plan-limited feature (e.g. a paid sandbox or UI feature) is NOT a blocker.
  Use "unknown" when only features, not API access, are plan-limited.
- Return ONE JSON object, no markdown, no commentary."""

MCP_SYSTEM = """You extract facts from web-page text.
Return ONLY these keys: api_surface, evidence.
- Use ONLY the supplied PAGE texts. Never guess. Unknown = "unknown".
- api_surface.existing_mcp: official, community, none_found, unknown.
- api_surface.mcp_url: a URL string, or null.
- If no page mentions an MCP server, return "unknown". Only return "none_found"
  if a page explicitly says there is no MCP server.
- A known MCP value needs an evidence entry with field "api_surface.existing_mcp",
  a url copied exactly from a PAGE URL, and a short snippet.
- Return ONE JSON object, no markdown, no commentary."""

AUTH_TEMPLATE = {
    "one_liner": "string or unknown",
    "auth_methods": ["unknown"],
    "api_surface": {"type": ["unknown"], "breadth": "unknown"},
    "evidence": [],
}
ACCESS_TEMPLATE = {
    "self_serve": "unknown",
    "self_serve_note": "unknown",
    "buildability": "unknown",
    "main_blocker": "other",
    "blocker_note": "unknown",
    "evidence": [],
}
MCP_TEMPLATE = {
    "api_surface": {"existing_mcp": "unknown", "mcp_url": None},
    "evidence": [],
}


def _ask(system: str, app: dict, pages: list[tuple[str, str, str]], template: dict,
         page_chars: int = PAGE_CHARS) -> dict:
    """One small LLM call, scoped to just the pages for its topic."""
    blocks = []
    for i, page in enumerate(pages, 1):
        url, text = page[0], page[1]
        tag = page[2] if len(page) > 2 else "search"
        source = "\nSOURCE: official (direct fetch)" if tag == "direct" else ""
        blocks.append(f"--- PAGE {i}\nURL: {url}{source}\nTEXT:\n{text[:page_chars]}\n---")
    page_text = "\n\n".join(blocks)
    user = (
        "Return a JSON object for the app below.\n\n"
        f"APP: {app['app']}\nCATEGORY: {app['category']}\nHINT: {app['hint']}\n\n"
        f"Fill exactly these keys:\n{json.dumps(template, indent=2)}\n\n"
        f"PAGES:\n{page_text}"
    )
    result, meta = complete_json_detailed(system, user)
    result["_model"] = meta["model"]
    if meta.get("rate_limited"):
        result["_rate_limited"] = True
    return result


WEAK_HOST_MARKERS = (
    "reddit.com", "quora.com", "medium.com", "g2.com", "capterra.com",
    "trustpilot.com", "frontal.so", "zoominfo.com", "onegtmlab.dev",
    "n8n.io", "6sense.com", "getknit.dev", "mcp.so", "lobehub.com",
    "zappysys.com", "mindcloud.co", "dev.to",
)
WEAK_PATH_MARKERS = ("/community", "/forum", "/t/", "/m-p/", "/review", "/blog/")


def _official_domains(app: dict) -> set[str]:
    """Own domains from the hint plus name-based subdomains/sibling domains."""
    domains = set()
    for part in app["hint"].split(","):
        host = part.strip().lower().split("/")[0]
        if "." not in host:
            continue
        domains.add(host)
        labels = host.split(".")
        if len(labels) >= 2:
            domains.add(".".join(labels[-2:]))

    # Also accept developers./docs./help./support./api. on the app's own name,
    # even when the hint points at a different host.
    name = re.sub(r"[^a-z0-9]", "", app["app"].lower().split("(")[0])
    if name:
        domains.add(f"{name}.com")
        for prefix in ("developers", "docs", "help", "support", "api", "www"):
            domains.add(f"{prefix}.{name}.com")
    return domains


def _is_weak(url: str, app: dict) -> bool:
    """Review blogs, forums and community threads are weak evidence."""
    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":")[0]
    path = parsed.path.lower()
    if "community." in host or host.startswith("forum."):
        return True
    if any(marker in host for marker in WEAK_HOST_MARKERS):
        return True
    return any(marker in path for marker in WEAK_PATH_MARKERS)


def _is_official(url: str, app: dict) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":")[0]
    if _is_weak(url, app):
        return False
    if any(host == domain or host.endswith("." + domain) for domain in _official_domains(app)):
        return True
    # Open-source vendors publish their own docs under their GitHub org
    # (e.g. twentyhq for Twenty).
    if host == "github.com":
        name = re.sub(r"[^a-z0-9]", "", app["app"].lower().split("(")[0])
        org = parsed.path.lower().strip("/").split("/")[0] if parsed.path.strip("/") else ""
        return len(name) >= 4 and name in org
    return False


def _pick_urls(results: list[dict], app: dict) -> list[str]:
    """Rank search hits: hint domain first, then docs/api/pricing pages."""
    hint_domains = [h.strip().lower().split("/")[0] for h in app["hint"].split(",")]

    def score(item: dict) -> int:
        url = item.get("url", "").lower()
        value = 0
        for domain in hint_domains:
            if domain and domain in url:
                value += 4
        if any(word in url for word in ("developer", "docs", "api", "reference", "pricing", "plan")):
            value += 2
        if _is_official(url, app):
            value += 3
        if _is_weak(url, app):
            value -= 5
        return value

    chosen = []
    domains = set()
    for item in sorted(results, key=score, reverse=True):
        url = item.get("url", "")
        if not url.startswith("http"):
            continue
        domain = urlparse(url).netloc
        if domain in domains:
            continue
        domains.add(domain)
        chosen.append(url)
        if len(chosen) >= MAX_PAGES_PER_TOPIC:
            break
    return chosen


def _direct_urls(app: dict) -> list[str]:
    """Candidate official URLs built from the hint domain's registered base."""
    bases = set()
    for part in app["hint"].split(","):
        host = part.strip().lower().split("/")[0]
        if "." not in host:
            continue
        labels = host.split(".")
        if len(labels) >= 2:
            bases.add(".".join(labels[-2:]))
    urls = []
    for base in sorted(bases):
        for path in DIRECT_PATHS:
            urls.append(f"https://{base}{path}")
        for sub in DIRECT_SUBDOMAINS:
            urls.append(f"https://{sub}.{base}")
    return urls


def _direct_pages(app: dict) -> list[tuple[str, str, str]]:
    """Fetch candidate official URLs and keep the ones with real text."""
    pages = []
    seen = set()
    for url in _direct_urls(app):
        if url in seen:
            continue
        seen.add(url)
        try:
            text = tools.fetch(url)
        except Exception as exc:
            print(f"[agent] direct fetch failed for {url}: {exc}", file=sys.stderr)
            continue
        head = text[:200].lower()
        if len(text) > 200 and "404" not in head and "page not found" not in head:
            pages.append((url, text, "direct"))
    return pages


def gather(app: dict) -> dict[str, list[tuple[str, str, str]]]:
    """Run all three topic queries; access also gets direct official pages."""
    gathered = {}
    for topic, template in TOPIC_QUERIES.items():
        query = template.format(app=app["app"])
        pages = []
        for url in _pick_urls(tools.search(query), app):
            if any(url == seen for seen, _, _ in pages):
                continue
            text = tools.fetch(url)
            if len(text) > 200:
                pages.append((url, text, "search"))
            if len(pages) >= MAX_PAGES_PER_TOPIC:
                break
        if topic == "access":
            direct = _direct_pages(app)
            known = {url for url, _, _ in pages}
            pages = [page for page in direct if page[0] not in known] + pages
        gathered[topic] = pages
    return gathered


def _toolkit_status(app: dict) -> str:
    """Check Composio's public toolkit listing (one cached fetch for all apps)."""
    listing = tools.fetch(TOOLKITS_URL)
    if len(listing) < 1000 or len(listing) >= tools.MAX_TEXT_CHARS:
        return "unknown"  # cached page is truncated, so absence proves nothing
    name = re.escape(app["app"].lower())
    if re.search(r"\|\s*" + name + r"\s*\|", listing, re.IGNORECASE):
        return "yes"
    return "no"


def _merge(*parts: dict) -> dict:
    """Merge partial extractions; api_surface is combined, not overwritten."""
    merged = {}
    for part in parts:
        for key, value in part.items():
            if key.startswith("_"):  # _error, _model, _rate_limited are metadata
                continue
            if key == "api_surface" and isinstance(value, dict) and isinstance(merged.get("api_surface"), dict):
                merged["api_surface"].update(value)
            elif key == "evidence" and isinstance(value, list) and isinstance(merged.get("evidence"), list):
                merged["evidence"].extend(value)
            else:
                merged[key] = value
    return merged


FIELD_KEYS = {
    "auth_methods", "self_serve", "api_surface.type", "api_surface.breadth",
    "api_surface.existing_mcp", "api_surface", "buildability", "main_blocker",
}


def _normalize_field(raw) -> str:
    if isinstance(raw, str) and raw.startswith("api_surface."):
        return "api_surface"
    return raw if isinstance(raw, str) else ""


def _read_evidence(item) -> tuple[str, str, str, str]:
    """Accept the field/url/snippet shape and the looser shapes the LLM emits."""
    if not isinstance(item, dict):
        return "", "", "", ""
    raw_field = item.get("field")
    if raw_field not in FIELD_KEYS:
        raw_field = next((key for key in item if key in FIELD_KEYS), "")
    url = item.get("url") or item.get("mcp_url") or item.get("link") or ""
    if not url:
        candidate = item.get(raw_field)
        if isinstance(candidate, str) and candidate.startswith("http"):
            url = candidate
    snippet = item.get("snippet") or item.get("quote") or item.get("text") or ""
    return raw_field, _normalize_field(raw_field), str(url), str(snippet)


def _sanitize(app: dict, data: dict, valid_urls: set[str]) -> tuple[dict, set[str], set[str]]:
    """Coerce LLM output into the schema; return record, all claims and official claims."""
    record = empty_record(app)

    if isinstance(data.get("one_liner"), str):
        record["one_liner"] = data["one_liner"].strip()[:300] or "unknown"

    auth = data.get("auth_methods")
    if isinstance(auth, list):
        values = [v for v in auth if v in AUTH_VALUES and v != "unknown"]
        record["auth_methods"] = values or ["unknown"]

    if data.get("self_serve") in SELF_SERVE_VALUES and data["self_serve"] != "unknown":
        record["self_serve"] = data["self_serve"]
    for key in ("self_serve_note", "blocker_note"):
        if isinstance(data.get(key), str):
            record[key] = data[key].strip()[:300] or "unknown"

    api = data.get("api_surface") if isinstance(data.get("api_surface"), dict) else {}
    types = api.get("type")
    if isinstance(types, list):
        values = [v for v in types if v in API_TYPE_VALUES and v != "unknown"]
        record["api_surface"]["type"] = values or ["unknown"]
    if api.get("breadth") in BREADTH_VALUES and api["breadth"] != "unknown":
        record["api_surface"]["breadth"] = api["breadth"]
    if api.get("existing_mcp") in MCP_VALUES and api["existing_mcp"] != "unknown":
        record["api_surface"]["existing_mcp"] = api["existing_mcp"]
    if isinstance(api.get("mcp_url"), str) and api["mcp_url"].startswith("http"):
        record["api_surface"]["mcp_url"] = api["mcp_url"]

    if data.get("buildability") in BUILDABILITY_VALUES and data["buildability"] != "unknown":
        record["buildability"] = data["buildability"]
    if data.get("main_blocker") in BLOCKER_VALUES:
        record["main_blocker"] = data["main_blocker"]

    claims = set()
    official_claims = set()
    evidence = data.get("evidence")
    if isinstance(evidence, list):
        clean = []
        for item in evidence:
            raw_field, field, url, snippet = _read_evidence(item)
            if url in valid_urls and snippet.strip():
                official = _is_official(url, app)
                if raw_field:
                    claims.add(raw_field)
                    if official:
                        official_claims.add(raw_field)
                if raw_field == "api_surface":
                    claims.add("api_surface.type")
                    if official:
                        official_claims.add("api_surface.type")
                if field in EVIDENCE_FIELDS:
                    clean.append({
                        "field": field,
                        "url": url,
                        "snippet": snippet.strip()[:400],
                    })
        record["evidence"] = clean
    return record, claims, official_claims


def _finalize(record: dict, claims: set[str], official_claims: set[str],
              errors: list[str], models: list[str] | None = None,
              rate_limited: bool = False) -> dict:
    """Keep only evidence-backed claims, then apply the source-quality downgrade rule."""
    def backed(raw: str, alias: str | None = None) -> bool:
        return raw in claims or (alias is not None and alias in claims)

    if not backed("auth_methods"):
        record["auth_methods"] = ["unknown"]
    if not backed("api_surface.type", "api_surface"):
        record["api_surface"]["type"] = ["unknown"]
    if not backed("api_surface.breadth", "api_surface"):
        record["api_surface"]["breadth"] = "unknown"
    if not backed("self_serve"):
        record["self_serve"] = "unknown"
        record["self_serve_note"] = "unknown"
    if not backed("api_surface.existing_mcp", "api_surface"):
        record["api_surface"]["existing_mcp"] = "unknown"
        record["api_surface"]["mcp_url"] = None
    if not backed("buildability"):
        record["buildability"] = "unknown"

    # Source-quality rule applies only to self_serve/gated and main_blocker:
    # both need official evidence plus a self_serve_note, otherwise -> unknown.
    # A weak main_blocker does NOT change status.
    has_note = record["self_serve_note"] not in ("", "unknown")
    self_serve_downgraded = False
    if record["self_serve"] != "unknown" and (
        "self_serve" not in official_claims or not has_note
    ):
        record["self_serve"] = "unknown"
        record["self_serve_note"] = "unknown"
        self_serve_downgraded = True
    if record["main_blocker"] != "unknown" and (
        "main_blocker" not in official_claims or not has_note
    ):
        record["main_blocker"] = "unknown"
        record["blocker_note"] = "unknown"

    # main_blocker is about API access only: plan-limited features (e.g. a paid
    # sandbox) must not be recorded as paid_only.
    if record["main_blocker"] == "paid_only" and "api" not in record["blocker_note"].lower():
        record["main_blocker"] = "unknown"
        record["blocker_note"] = "unknown"

    if record["self_serve"] == "gated" and record["buildability"] == "yes":
        record["buildability"] = "partial"

    required = {
        "auth_methods": record["auth_methods"] != ["unknown"],
        "api_surface.type": record["api_surface"]["type"] != ["unknown"],
        "self_serve": record["self_serve"] != "unknown",
    }
    missing = [name for name, value in required.items() if not value]

    # An API failure is not a research result. Keep it as a separate status so a
    # later run retries it and accuracy/pattern stats exclude it.
    record["llm_model"] = ", ".join(dict.fromkeys(models or [])) or "unknown"
    if errors:
        record["status"] = "error"
        marker = "LLM_RATE_LIMITED: " if rate_limited else "LLM_API_ERROR: "
        record["error_reason"] = marker + "; ".join(errors)[:400]
        record["needs_human_reason"] = "not applicable (status=error; retry next run)"
    else:
        record["error_reason"] = "none"
        record["status"] = "ok" if not missing else "needs_human"
        reasons = []
        if missing:
            reasons.append("missing evidence-backed: " + ", ".join(missing))
            if self_serve_downgraded:
                reasons.append("self_serve downgraded: weak-only evidence or missing note")
        record["needs_human_reason"] = "none" if not reasons else " | ".join(reasons)

    problems = validate_record(record)
    if problems:
        print(f"[agent] validation issues for {record['app']}: {problems}", file=sys.stderr)
    return record


def research(app: dict) -> dict:
    record = empty_record(app)
    gathered = gather(app)
    try:
        record["has_composio_toolkit"] = _toolkit_status(app)
    except Exception as exc:
        print(f"[agent] toolkit lookup failed for {app['app']}: {exc}", file=sys.stderr)

    parts = [
        _ask(AUTH_SYSTEM, app, gathered["auth_api"], AUTH_TEMPLATE),
        _ask(ACCESS_SYSTEM, app, gathered["access"], ACCESS_TEMPLATE,
             page_chars=ACCESS_PAGE_CHARS),
        _ask(MCP_SYSTEM, app, gathered["mcp"], MCP_TEMPLATE),
    ]
    errors = [part["_error"][:120] for part in parts if "_error" in part]
    models = [part["_model"] for part in parts if part.get("_model")]
    rate_limited = any(part.get("_rate_limited") for part in parts)
    for error in errors:
        print(f"[agent] LLM error for {app['app']}: {error}", file=sys.stderr)
    if models:
        print(f"[agent] models for {app['app']}: {', '.join(dict.fromkeys(models))}")
    data = _merge(*parts)
    valid_urls = {url for pages in gathered.values() for url, _, _ in pages}
    record, claims, official_claims = _sanitize(app, data, valid_urls)
    return _finalize(record, claims, official_claims, errors, models, rate_limited)


MAX_CONSECUTIVE_RATE_LIMITS = 3
RATE_LIMIT_WAIT_SECONDS = int(os.getenv("RATE_LIMIT_WAIT_SECONDS", "900"))


def log_line(message: str) -> None:
    """One timestamped line to stdout and the run log."""
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _load_existing() -> dict[str, dict]:
    """Records from earlier runs, keyed by app name for resume."""
    if not OUT_FILE.exists():
        return {}
    records = json.loads(OUT_FILE.read_text(encoding="utf-8"))
    return {record["app"].lower(): record for record in records}


def _write_existing(records: dict[str, dict]) -> None:
    """Write all records, in apps.json order, after every app (crash safe)."""
    output = sorted(
        records.values(),
        key=lambda record: (record.get("id", 10_000), record["app"]),
    )
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(
        json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="App research agent - pass 1")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--limit", type=int, help="research the first N apps")
    group.add_argument("--apps", help="comma-separated app names")
    group.add_argument("--all", action="store_true", help="research all 100 apps")
    group.add_argument("--pass2-only", action="store_true", help="not implemented yet")
    parser.add_argument(
        "--force", action="store_true", help="re-research apps even if already done"
    )
    args = parser.parse_args()

    if args.pass2_only:
        import pass2

        pass2.run()
        return

    apps = json.loads(APPS_FILE.read_text(encoding="utf-8"))
    if args.limit is not None:
        selected = apps[:args.limit]
    elif args.apps:
        wanted = {name.strip().lower() for name in args.apps.split(",")}
        selected = [app for app in apps if app["app"].lower() in wanted]
    else:
        selected = apps

    records = _load_existing()
    skipped = [
        app for app in selected
        if records.get(app["app"].lower())
        and records[app["app"].lower()].get("status") != "error"
        and not args.force
    ]
    if skipped:
        log_line(f"skipping {len(skipped)} already-done apps; use --force to re-research")

    # On repeated rate limits, wait and retry automatically instead of exiting.
    while True:
        pending = [app for app in selected if app not in skipped]
        pending = [
            app for app in pending
            if args.force or app["app"].lower() not in records
            or records[app["app"].lower()].get("status") == "error"
        ]
        if not pending:
            break

        consecutive_rate_limits = 0
        paused = False
        for index, app in enumerate(pending):
            log_line(f"researching {app['id']}: {app['app']}")
            record = research(app)
            records[app["app"].lower()] = record
            _write_existing(records)
            log_line(
                f"app={app['app']} id={app['id']} status={record['status']} "
                f"model={record.get('llm_model', 'unknown')} "
                f"evidence={len(record['evidence'])}"
            )
            if (record["status"] == "error"
                    and record["error_reason"].startswith("LLM_RATE_LIMITED")):
                consecutive_rate_limits += 1
                if consecutive_rate_limits >= MAX_CONSECUTIVE_RATE_LIMITS:
                    remaining = len(pending) - index - 1
                    log_line(
                        f"rate limit: {consecutive_rate_limits} consecutive errors; "
                        f"waiting {RATE_LIMIT_WAIT_SECONDS}s before retry "
                        f"({remaining} queued, {len(records)} records saved)"
                    )
                    time.sleep(RATE_LIMIT_WAIT_SECONDS)
                    paused = True
                    break
            else:
                consecutive_rate_limits = 0
        if paused:
            continue
        break

    _write_existing(records)
    final = _load_existing()
    all_done = (
        len(final) >= len(apps)
        and all(
            final.get(app["app"].lower(), {}).get("status") != "error"
            for app in apps
        )
    )
    if all_done and not FROZEN_FILE.exists():
        shutil.copyfile(OUT_FILE, FROZEN_FILE)
        log_line(f"pass 1 complete: froze baseline at {FROZEN_FILE} ({len(final)} records)")
    elif FROZEN_FILE.exists():
        log_line(f"baseline already frozen at {FROZEN_FILE}; left untouched")

    counts = {
        status: sum(record.get("status") == status for record in final.values())
        for status in ("ok", "needs_human", "error")
    }
    models = {}
    for record in final.values():
        models[record.get("llm_model", "unknown")] = models.get(record.get("llm_model", "unknown"), 0) + 1
    log_line(f"wrote {len(final)} records to {OUT_FILE}")
    log_line(f"status counts: {json.dumps(counts)}")
    log_line(f"model usage: {json.dumps(models)}")
    log_line(f"backend counts: {json.dumps(tools.backend_counts())}")


if __name__ == "__main__":
    main()
