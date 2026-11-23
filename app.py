"""Minimal FastAPI dashboard for the app-research results.

Reads only the committed JSON artifacts in data/ (results, patterns, tool usage
and optional verification). Raw pass-1 files and the page cache are never read
here, so the deployed app has no dependence on the research pipeline or keys.
"""

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

app = FastAPI(title="Composio app research")
templates = Jinja2Templates(directory=str(ROOT / "templates"))


def _data_path(name: str) -> Path:
    """Resolve a data file inside the repo, independent of the working dir."""
    return DATA_DIR / name


def _load(name: str, default):
    file = _data_path(name)
    if not file.exists():
        return default
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _usage_rows(usage: dict) -> list[dict]:
    """Turn counted backend keys into one row per operation/backend pair."""
    grouped: dict[tuple[str, str], dict] = {}
    for key, count in usage.items():
        parts = str(key).split(".")
        if len(parts) < 3:
            continue
        operation, backend, outcome = parts[0], parts[1], parts[-1]
        row = grouped.setdefault(
            (operation, backend),
            {"operation": operation, "backend": backend, "ok": 0, "failed": 0},
        )
        if outcome in ("ok", "failed"):
            try:
                row[outcome] += int(count)
            except (TypeError, ValueError):
                pass
    return sorted(grouped.values(), key=lambda r: (r["operation"], r["backend"]))


def _json_file(name: str):
    file = _data_path(name)
    if not file.exists():
        return JSONResponse({"detail": f"{name} is not available yet"}, status_code=404)
    return FileResponse(file, media_type="application/json")


@app.get("/")
def index(request: Request):
    patterns = _load("patterns.json", {})
    verification = _load("verification.json", None)
    results = _load("results.json", [])
    usage = _load("tool_usage.json", {})
    return templates.TemplateResponse(request, "index.html", {
        "patterns": patterns,
        "verification": verification,
        "results_count": len(results),
        "needs_human": [r for r in results if r.get("status") == "needs_human"],
        "error_count": sum(r.get("status") == "error" for r in results),
        "usage_rows": _usage_rows(usage),
    })


@app.get("/results")
def results_page(
    request: Request,
    category: str | None = None,
    auth: str | None = None,
    self_serve: str | None = None,
    buildability: str | None = None,
    status: str | None = None,
):
    rows = _load("results.json", [])
    if category:
        rows = [r for r in rows if r.get("category") == category]
    if auth:
        rows = [r for r in rows if auth in r.get("auth_methods", [])]
    if self_serve:
        rows = [r for r in rows if r.get("self_serve") == self_serve]
    if buildability:
        rows = [r for r in rows if r.get("buildability") == buildability]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    categories = sorted({r.get("category", "") for r in _load("results.json", [])})
    return templates.TemplateResponse(request, "results.html", {
        "rows": rows,
        "categories": categories,
        "filters": {
            "category": category or "",
            "auth": auth or "",
            "self_serve": self_serve or "",
            "buildability": buildability or "",
            "status": status or "",
        },
    })


@app.get("/results.json")
def results_json():
    return _json_file("results.json")


@app.get("/patterns.json")
def patterns_json():
    return _json_file("patterns.json")


@app.get("/verification.json")
def verification_json():
    return _json_file("verification.json")
