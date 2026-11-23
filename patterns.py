"""Headline patterns from results.json. API-error records are excluded."""

import json
from collections import Counter, defaultdict
from pathlib import Path

from schema import counted_records

RESULTS_FILE = Path("data/results.json")
OUTPUT_FILE = Path("data/patterns.json")


def _values(record: dict, field: str) -> list[str]:
    value = record.get(field)
    if isinstance(value, list):
        return [str(item) for item in value if item and item != "unknown"]
    if value and value != "unknown":
        return [str(value)]
    return []


def _distribution(records: list[dict], field: str) -> dict:
    counts = Counter()
    for record in records:
        for value in _values(record, field):
            counts[value] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _by_category(records: list[dict], field: str) -> dict:
    grouped = defaultdict(list)
    for record in records:
        grouped[record["category"]].append(record)
    return {
        category: _distribution(items, field)
        for category, items in sorted(grouped.items())
    }


def build(records: list[dict]) -> dict:
    counted = counted_records(records)
    excluded = len(records) - len(counted)
    blockers = Counter(
        record.get("main_blocker", "unknown")
        for record in counted
        if record.get("main_blocker", "unknown") != "unknown"
    )
    mcp = Counter(
        record.get("api_surface", {}).get("existing_mcp", "unknown")
        for record in counted
    )
    needs_human = [
        {"app": r["app"], "reason": r.get("needs_human_reason", "")}
        for r in counted if r.get("status") == "needs_human"
    ]
    easy_wins = [
        r["app"] for r in counted
        if r.get("buildability") == "yes" and r.get("self_serve") == "self_serve"
        and ({"OAuth2", "API key"} & set(r.get("auth_methods", [])))
    ]
    outreach = [
        r["app"] for r in counted
        if r.get("self_serve") == "gated"
        or r.get("main_blocker") in ("partner_gated", "contact_sales")
    ]
    auth = _distribution(counted, "auth_methods")
    self_serve = _distribution(counted, "self_serve")
    buildability = _distribution(counted, "buildability")

    top_auth = ", ".join(f"{k} ({v})" for k, v in list(auth.items())[:2]) or "none"
    top_self = ", ".join(f"{k} ({v})" for k, v in list(self_serve.items())[:2]) or "none"
    headlines = [
        f"{len(counted)} apps counted; {excluded} API-error records excluded.",
        f"Most common auth: {top_auth}.",
        f"Access: {top_self}.",
        f"{len(easy_wins)} easy wins, {len(outreach)} need outreach.",
        f"{mcp.get('official', 0)} official and {mcp.get('community', 0)} community MCP servers found.",
        f"{len(needs_human)} apps still need a human.",
    ]
    return {
        "source": str(RESULTS_FILE),
        "records_counted": len(counted),
        "records_excluded": excluded,
        "auth_overall": auth,
        "auth_by_category": _by_category(counted, "auth_methods"),
        "self_serve_overall": self_serve,
        "self_serve_by_category": _by_category(counted, "self_serve"),
        "buildability_overall": buildability,
        "buildability_by_category": _by_category(counted, "buildability"),
        "blockers": dict(blockers),
        "mcp": dict(mcp),
        "easy_wins": easy_wins,
        "needs_outreach": outreach,
        "needs_human": needs_human,
        "headlines": headlines,
    }


def main() -> None:
    records = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    data = build(records)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUTPUT_FILE}: counted={data['records_counted']} "
          f"excluded={data['records_excluded']}")
    for line in data["headlines"]:
        print(" -", line)


if __name__ == "__main__":
    main()
