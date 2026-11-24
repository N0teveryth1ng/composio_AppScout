"""Sample picker, empty ground-truth template, and accuracy check.

Generating the template is all this script does by default. Accuracy is only
computed with --compare, after the AI-drafted ground_truth.json is available.
"""

import argparse
import json
import random
from pathlib import Path

from schema import (
    API_TYPE_VALUES,
    AUTH_VALUES,
    BUILDABILITY_VALUES,
    COUNTED_STATUSES,
    MCP_VALUES,
    SELF_SERVE_VALUES,
    counted_records,
)

APPS_FILE = Path("apps.json")
PASS1_FILE = Path("data/pass1.json")
RESULTS_FILE = Path("data/results.json")
SAMPLE_FILE = Path("data/sample.json")
TRUTH_FILE = Path("data/ground_truth.json")
BASELINE_FILE = Path("data/ai_draft_baseline.json")
OUTPUT_FILE = Path("data/verification.json")
SEED = 42
PER_CATEGORY = 2
FIELDS = ("auth_methods", "self_serve", "api_type", "existing_mcp", "buildability")
TRUTH_REQUIRED_FIELDS = set(FIELDS) | {
    "id", "app", "category", "doc_url", "passage", "urls",
    "drafted_by", "human_reviewed",
}
# These are developer tools, not hosted APIs, so they are reported separately
# and excluded from the accuracy denominators.
EXCLUDED_FROM_ACCURACY = {
    "Sherlock": "not hosted APIs, excluded",
    "Mermaid CLI": "not hosted APIs, excluded",
}
FIXED_PASS2_VERSION = "subfield-fix-1"
V1_PASS2_VERSION = "v1-pre-fix"
# Set after the user confirms the ground-truth review is final.
GROUND_TRUTH_FINAL = True


def pick_sample() -> list[dict]:
    apps = json.loads(APPS_FILE.read_text(encoding="utf-8"))
    grouped = {}
    for app in apps:
        grouped.setdefault(app["category"], []).append(app)
    rng = random.Random(SEED)
    sample = []
    for category in sorted(grouped):
        items = grouped[category][:]
        rng.shuffle(items)
        sample.extend(items[:PER_CATEGORY])
    sample.sort(key=lambda app: app["id"])
    SAMPLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_FILE.write_text(json.dumps(sample, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {SAMPLE_FILE}: {len(sample)} apps ({PER_CATEGORY} per category)")
    return sample


def make_template(sample: list[dict]) -> None:
    if TRUTH_FILE.exists():
        print(f"{TRUTH_FILE} already exists; left untouched")
        return
    template = [
        {
            "id": app["id"],
            "app": app["app"],
            "category": app["category"],
            "auth_methods": [],
            "self_serve": "",
            "api_type": [],
            "existing_mcp": "",
            "buildability": "",
            "doc_url": "",
        }
        for app in sample
    ]
    TRUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRUTH_FILE.write_text(json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote empty {TRUTH_FILE}: fill it by hand from real docs")


def _normal(value):
    if isinstance(value, list):
        return sorted(str(item).lower() for item in value)
    return str(value or "").lower()


def _field_value(record: dict, field: str):
    if field == "api_type":
        return record.get("api_surface", {}).get("type", [])
    if field == "existing_mcp":
        return record.get("api_surface", {}).get("existing_mcp", "unknown")
    return record.get(field)


def _validate_truth(entries: list[dict]) -> None:
    """Validate the whole AI-drafted answer key before any comparison."""
    problems = []
    if len(entries) != 20:
        problems.append(f"expected 20 entries, got {len(entries)}")
    if len({entry.get("id") for entry in entries}) != len(entries):
        problems.append("duplicate ids")
    if len({entry.get("app") for entry in entries}) != len(entries):
        problems.append("duplicate apps")

    sample = {app["app"]: app for app in json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))}
    allowed = {
        "auth_methods": AUTH_VALUES,
        "self_serve": SELF_SERVE_VALUES,
        "api_type": API_TYPE_VALUES,
        "existing_mcp": MCP_VALUES,
        "buildability": BUILDABILITY_VALUES,
    }
    for entry in entries:
        name = entry.get("app", "?")
        missing = TRUTH_REQUIRED_FIELDS - set(entry)
        if missing:
            problems.append(f"{name}: missing {sorted(missing)}")
        meta = sample.get(name)
        if not meta:
            problems.append(f"{name}: not in data/sample.json")
        else:
            for key in ("id", "category"):
                if entry.get(key) != meta[key]:
                    problems.append(f"{name}: {key} mismatch")
        if not isinstance(entry.get("doc_url"), str) or not entry["doc_url"].startswith("https://"):
            problems.append(f"{name}: bad doc_url")
        if not isinstance(entry.get("passage"), str) or not entry["passage"].strip():
            problems.append(f"{name}: bad passage")
        if not isinstance(entry.get("human_reviewed"), bool):
            problems.append(f"{name}: human_reviewed is not bool")
        if entry.get("drafted_by") != "ai":
            problems.append(f"{name}: drafted_by is not 'ai'")
        urls = entry.get("urls")
        if not isinstance(urls, list) or not urls or not all(
            isinstance(url, str) and url.startswith("https://") for url in urls
        ):
            problems.append(f"{name}: bad urls")
        for key, values in allowed.items():
            value = entry.get(key)
            items = value if isinstance(value, list) else [value]
            if not items or any(item not in values for item in items):
                problems.append(f"{name}: bad {key} {value!r}")
            if isinstance(value, list) and len(set(value)) != len(value):
                problems.append(f"{name}: duplicate {key} values")
    if problems:
        raise SystemExit("ground_truth.json validation failed:\n  " + "\n  ".join(problems))


def _draft_diff_counts(entries: list[dict]) -> tuple[int, int]:
    """Count values changed from the original AI draft, and answer-only changes."""
    if not BASELINE_FILE.exists():
        return 0, 0
    baseline = {item["app"]: item for item in json.loads(BASELINE_FILE.read_text(encoding="utf-8"))}
    total = 0
    answers = 0
    for entry in entries:
        original = baseline.get(entry["app"], {})
        for key, old in original.items():
            if key == "app" or entry.get(key) == old:
                continue
            total += 1
            if key in FIELDS:
                answers += 1
    return total, answers


def _as_set(value) -> set[str]:
    if isinstance(value, list):
        return {str(item) for item in value}
    return {str(value)} if value else set()


def _likely_cause(field: str, label: str, actual, expected) -> str:
    """A concise, evidence-based guess for why this field differed."""
    actual_set = _as_set(actual)
    expected_set = _as_set(expected)
    missed = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    bits = []
    if "unknown" in actual_set:
        bits.append("agent returned unknown, so no supporting value was extracted")
    if missed:
        bits.append("missed " + ", ".join(missed))
    if extra:
        bits.append("over-claimed " + ", ".join(extra))
    detail = "; ".join(bits) if bits else "different value than the answer key"

    if field == "existing_mcp":
        if "official" in extra:
            detail += "; official MCP claim lacked vendor-domain or GitHub-org evidence"
        elif "official" in missed:
            detail += "; official MCP server was not found in the cited sources"
        elif "community" in extra:
            detail += "; only third-party MCP evidence was present"
    elif field == "auth_methods":
        detail += "; cited docs did not expose every supported auth method"
    elif field == "api_type":
        detail += "; cited docs describe a different API style"
    elif field == "self_serve":
        detail += "; sign-up or pricing evidence differed from the reviewed source"
    elif field == "buildability":
        detail += "; buildability judgment differed from the reviewed source"

    prefix = "Pass 1" if label == "pass1" else "Pass 2"
    if label == "pass1":
        return f"{prefix} likely cause: {detail}; no claim-level re-check at pass 1."
    return f"{prefix} likely cause: {detail}; pass 2 kept only claims supported by the cited snippet."


def _unscored_reason(entry: dict, record: dict | None) -> str | None:
    """Why a sample app cannot be scored; None means it is eligible."""
    name = entry["app"]
    if name in EXCLUDED_FROM_ACCURACY:
        return EXCLUDED_FROM_ACCURACY[name]
    if record is None:
        return "no pass-2 record"
    if record.get("pass2_version") == V1_PASS2_VERSION:
        return "v1-pre-fix record; only fixed pass 2 is scored"
    if record.get("pass2_version") != FIXED_PASS2_VERSION:
        return "pass-2 record is not on the fixed version"
    if record.get("status") == "error":
        return "pass-2 ended on an API error"
    if record.get("status") not in COUNTED_STATUSES:
        return f"status {record.get('status')!r} is not eligible for scoring"
    return None


def compare() -> None:
    all_truth = json.loads(TRUTH_FILE.read_text(encoding="utf-8"))
    _validate_truth(all_truth)
    if any(not entry.get("doc_url") for entry in all_truth):
        raise SystemExit("ground_truth.json still has empty doc_url fields; fill it first")
    corrected_values, answer_corrections = _draft_diff_counts(all_truth)

    results = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    result_by_app = {r["app"]: r for r in results}
    pass1_by_app = {
        r["app"]: r for r in counted_records(
            json.loads(PASS1_FILE.read_text(encoding="utf-8"))
        )
    }
    truth_by_app = {entry["app"]: entry for entry in all_truth}
    eligible = [
        entry for entry in all_truth if entry["app"] not in EXCLUDED_FROM_ACCURACY
    ]

    scored_names = []
    unscored_apps = []
    for entry in eligible:
        name = entry["app"]
        reason = _unscored_reason(entry, result_by_app.get(name))
        if reason:
            unscored_apps.append({"app": name, "reason": reason})
        else:
            scored_names.append(name)

    # Only fixed-version, countable pass-2 records enter the comparison.
    pass1 = {name: pass1_by_app[name] for name in scored_names if name in pass1_by_app}
    pass2 = {name: result_by_app[name] for name in scored_names}

    per_field = {field: {"pass1": [0, 0], "pass2": [0, 0]} for field in FIELDS}
    misses = []
    for name in scored_names:
        entry = truth_by_app[name]
        for field in FIELDS:
            expected = _normal(entry.get(field))
            if not expected or expected == [""]:
                continue
            for label, source in (("pass1", pass1), ("pass2", pass2)):
                record = source.get(name)
                if not record:
                    continue
                actual = _normal(_field_value(record, field))
                per_field[field][label][1] += 1
                if actual == expected:
                    per_field[field][label][0] += 1
                else:
                    misses.append({
                        "app": name, "field": field, "pass": label,
                        "agent_value": actual, "true_value": expected,
                        "likely_cause": _likely_cause(field, label, actual, expected),
                        "doc_url": entry.get("doc_url", ""),
                    })

    accuracy = {
        field: {
            label: {
                "correct": values[0], "compared": values[1],
                "accuracy": values[0] / values[1] if values[1] else None,
            }
            for label, values in scores.items()
        }
        for field, scores in per_field.items()
    }
    overall = {}
    for label in ("pass1", "pass2"):
        correct = sum(scores[label][0] for scores in per_field.values())
        compared = sum(scores[label][1] for scores in per_field.values())
        overall[label] = {
            "correct": correct, "compared": compared,
            "accuracy": correct / compared if compared else None,
        }

    reviewed = sum(entry.get("human_reviewed") is True for entry in all_truth)
    withheld = not GROUND_TRUTH_FINAL
    excluded = [
        {"app": entry["app"], "reason": EXCLUDED_FROM_ACCURACY[entry["app"]]}
        for entry in all_truth if entry["app"] in EXCLUDED_FROM_ACCURACY
    ]
    data = {
        "sample_size": len(all_truth),
        "eligible_size": len(eligible),
        "scored_apps": len(scored_names),
        "scored_app_names": scored_names,
        "unscored_apps": unscored_apps,
        "comparison_computed": True,
        "ground_truth_drafted_by": "ai",
        "ground_truth_source": "official docs, pricing and sign-up pages",
        "human_reviewed_count": reviewed,
        "human_reviewed_total": len(all_truth),
        "corrected_values_count": corrected_values,
        "answer_value_corrections": answer_corrections,
        "ground_truth_validation": "passed",
        "accuracy_withheld": withheld,
        "excluded_from_accuracy": excluded,
        # Numbers stay out of the artifact until the human-review count is confirmed.
        "per_field_accuracy": None if withheld else accuracy,
        "overall_accuracy": None if withheld else overall,
        "misses": [] if withheld else misses,
        "needs_human_count": sum(r.get("status") == "needs_human" for r in results),
        "api_error_count": sum(r.get("status") == "error" for r in results),
    }
    OUTPUT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT_FILE}")
    print(f"  scored {len(scored_names)} of {len(eligible)} eligible apps")
    print(f"  human-reviewed {reviewed} of {len(all_truth)} entries")
    print(f"  corrected values vs AI draft: {corrected_values} "
          f"({answer_corrections} answer-value corrections)")
    if unscored_apps:
        for item in unscored_apps:
            print(f"  unscored: {item['app']} - {item['reason']}")
    if withheld:
        print("  accuracy withheld until the confirmed human-review count is provided")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="store_true", help="write data/sample.json")
    parser.add_argument("--template", action="store_true", help="write empty ground truth")
    parser.add_argument("--compare", action="store_true", help="compute accuracy (needs filled truth)")
    args = parser.parse_args()
    if args.compare:
        compare()
        return
    sample = pick_sample()
    if args.template or not (args.sample):
        make_template(sample)


if __name__ == "__main__":
    main()
