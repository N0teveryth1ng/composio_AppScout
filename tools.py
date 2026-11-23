"""Search and fetch with Composio first, plain free fallback second.

Composio path uses Instant tools (no third-party keys):
  - search: TAVILY_SEARCH
  - fetch:  EXA_GET_CONTENTS_ACTION
Both require COMPOSIO_API_KEY, Instant enabled, and free credits.

Fallback path uses ddgs + requests/trafilatura and needs no keys.
Every call is cached on disk and logged to data/tool_usage.json.
"""

import hashlib
import json
import os
import re
from pathlib import Path

import requests
import trafilatura
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

CACHE_DIR = Path("data/cache")
PAGES_DIR = CACHE_DIR / "pages"
SEARCH_DIR = CACHE_DIR / "search"
USAGE_FILE = Path("data/tool_usage.json")
MAX_TEXT_CHARS = 20000
USER_AGENT = "Mozilla/5.0 (compatible; AppResearchAgent/1.0)"

_composio = None
_composio_disabled = os.getenv("COMPOSIO_API_KEY", "") == ""


def _cache_path(folder: Path, key: str) -> Path:
    return folder / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".txt")


def _log_usage(kind: str, backend: str, ok: bool) -> None:
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if USAGE_FILE.exists():
        counts = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    else:
        counts = {}
    key = f"{kind}.{backend}.{'ok' if ok else 'failed'}"
    counts[key] = counts.get(key, 0) + 1
    USAGE_FILE.write_text(json.dumps(counts, indent=2), encoding="utf-8")


def backend_counts() -> dict:
    """Counts of composio vs fallback calls, for the web page."""
    if not USAGE_FILE.exists():
        return {}
    return json.loads(USAGE_FILE.read_text(encoding="utf-8"))


def _client():
    global _composio
    if _composio is None:
        from composio import Composio

        _composio = Composio(
            api_key=os.getenv("COMPOSIO_API_KEY"),
            toolkit_versions={"tavily": "latest", "exa": "latest"},
        )
    return _composio


def _unwrap(response) -> object:
    """Return the `data` payload of a Composio execute response.

    `composio.tools.execute()` returns a TypedDict, i.e. a plain dict at runtime:
      {"data": ..., "error": ..., "successful": bool, ...}
    Attribute access (`response.data`) silently returns None, so read by key.
    A failed execution is raised so the caller falls back.
    """
    if isinstance(response, dict):
        if not response.get("successful", True):
            raise RuntimeError(f"Composio execution failed: {response.get('error')}")
        value = response.get("data")
    else:
        value = getattr(response, "data", response)
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _iter_payloads(value, seen=None):
    """Yield dict/list/string values from nested API output, decoding JSON strings."""
    if seen is None:
        seen = set()
    if isinstance(value, (dict, list)):
        marker = id(value)
        if marker in seen:
            return
        seen.add(marker)
        yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_payloads(item, seen)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_payloads(item, seen)
    elif isinstance(value, str):
        if value.strip():
            yield value
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                yield from _iter_payloads(json.loads(stripped), seen)
            except json.JSONDecodeError:
                pass


def _parse_search(payload) -> list[dict]:
    """Find a list of {title, url, snippet} records in arbitrary API output."""
    payloads = list(_iter_payloads(payload))
    for value in payloads:
        if isinstance(value, list):
            items = [i for i in value if isinstance(i, dict) and ("url" in i or "href" in i)]
            if items:
                return [_normalize_result(i) for i in items]
    for value in payloads:
        if isinstance(value, dict) and ("url" in value or "href" in value):
            return [_normalize_result(value)]
    return []


def _normalize_result(item: dict) -> dict:
    return {
        "title": str(item.get("title") or item.get("name") or "")[:200],
        "url": str(item.get("url") or item.get("href") or ""),
        "snippet": str(item.get("content") or item.get("snippet") or item.get("body") or "")[:500],
    }


def _parse_page(payload) -> str:
    """Pick the longest plain text block out of a scrape response."""
    candidates = []
    for value in _iter_payloads(payload):
        if isinstance(value, dict):
            for key in ("text", "markdown", "content", "raw"):
                candidate = value.get(key)
                if isinstance(candidate, str) and len(candidate) > 100:
                    candidates.append(candidate)
        elif isinstance(value, str) and len(value) > 100:
            candidates.append(value)
    candidates = [c for c in candidates if not c.lstrip().startswith(("{", "["))]
    best = max(candidates, key=len) if candidates else ""
    return best.strip()[:MAX_TEXT_CHARS]


def _disable_on_error(exc: Exception) -> None:
    global _composio_disabled
    message = str(exc).lower()
    if any(word in message for word in ("402", "401", "403", "balance", "credit", "quota", "auth", "instant")):
        _composio_disabled = True


def search(query: str) -> list[dict]:
    """Search the web. Returns [{title, url, snippet}], cached on disk."""
    path = _cache_path(SEARCH_DIR, query)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    results = []
    if not _composio_disabled:
        try:
            response = _client().tools.execute(
                "TAVILY_SEARCH",
                arguments={"query": query, "max_results": 5, "search_depth": "basic"},
                connected_account_id="instant_account",
                dangerously_skip_version_check=True,
            )
            results = _parse_search(_unwrap(response))
            _log_usage("search", "composio", bool(results))
        except Exception as exc:
            _log_usage("search", "composio", False)
            _disable_on_error(exc)
            print(f"[tools] Composio search failed ({query[:50]}): {exc}")

    if not results:
        from ddgs import DDGS

        last_error = None
        for backend in ("lite", "auto", "duckduckgo", "brave", "mojeek"):
            try:
                with DDGS() as ddg:
                    raw = ddg.text(query, max_results=5, backend=backend)
                results = [_normalize_result(item) for item in raw]
                if results:
                    break
            except Exception as exc:
                last_error = exc
        if results:
            _log_usage("search", "fallback", True)
        else:
            _log_usage("search", "fallback", False)
            print(f"[tools] fallback search failed ({query[:50]}): {last_error}")

    results = [r for r in results if r["url"].startswith("http")]
    SEARCH_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def fetch(url: str) -> str:
    """Fetch clean text of a page, truncated and cached on disk."""
    path = _cache_path(PAGES_DIR, url)
    if path.exists():
        return path.read_text(encoding="utf-8")

    text = ""
    if not _composio_disabled:
        try:
            response = _client().tools.execute(
                "EXA_GET_CONTENTS_ACTION",
                arguments={"urls": [url], "text": True},
                connected_account_id="instant_account",
                dangerously_skip_version_check=True,
            )
            text = _parse_page(_unwrap(response))
            _log_usage("fetch", "composio", bool(text))
        except Exception as exc:
            _log_usage("fetch", "composio", False)
            _disable_on_error(exc)
            print(f"[tools] Composio fetch failed ({url[:60]}): {exc}")

    if not text:
        try:
            response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
            response.raise_for_status()
            text = trafilatura.extract(response.text, url=url) or ""
            if not text:
                text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
            text = re.sub(r"\s+", " ", text).strip()[:MAX_TEXT_CHARS]
            _log_usage("fetch", "fallback", bool(text))
        except Exception as exc:
            _log_usage("fetch", "fallback", False)
            print(f"[tools] fallback fetch failed ({url[:60]}): {exc}")

    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text
