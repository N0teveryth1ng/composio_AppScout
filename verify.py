"""Sample picker, empty ground-truth template, and accuracy check.

Generating the template is all this script does by default. Accuracy is only
computed with --compare, after the AI-drafted ground_truth.json is available.
"""

import argparse
import json
import random
from pathlib import Path

from schema import counted_records

APPS_FILE = Path("apps.json")
PASS1_FILE = Path("data/pass1.json")
RESULTS_FILE = Path("data/results.json")
SAMPLE_FILE = Path("data/sample.json")
TRUTH_FILE = Path("data/ground_truth.json")
OUTPUT_FILE = Path("data/verification.json")
SEED = 42
PER_CATEGORY = 2
FIELDS = ("auth_methods", "self_serve", "api_type", "existing_mcp", "buildability")
# These are developer tools, not hosted APIs, so they are reported separately
# and excluded from the accuracy denominators.
EXCLUDED_FROM_ACCURACY = {
    "Sherlock": "open-source CLI, not a hosted API",
    "Mermaid CLI": "CLI/library, not a hosted API",
}


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


def compare() -> None:
    all_truth = json.loads(TRUTH_FILE.read_text(encoding="utf-8"))
    if any(not entry.get("doc_url") for entry in all_truth):
        raise SystemExit("ground_truth.json still has empty doc_url fields; fill it first")
    excluded = [
        {"app": entry["app"], "reason": EXCLUDED_FROM_ACCURACY[entry["app"]]}
        for entry in all_truth if entry["app"] in EXCLUDED_FROM_ACCURACY
    ]
    truth = [
        entry for entry in all_truth if entry["app"] not in EXCLUDED_FROM_ACCURACY
    ]
    pass1 = {
        r["app"]: r for r in counted_records(
            json.loads(PASS1_FILE.read_text(encoding="utf-8"))
        )
    }
    pass2 = {
        r["app"]: r for r in counted_records(
            json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
        )
    }
    per_field = {field: {"pass1": [0, 0], "pass2": [0, 0]} for field in FIELDS}
    misses = []
    for entry in truth:
        name = entry["app"]
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
    results = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    data = {
        "sample_size": len(all_truth),
        "scored_apps": len(truth),
        "ground_truth_drafted_by": "ai",
        "ground_truth_source": "official docs, pricing and sign-up pages",
        "human_reviewed_count": sum(
            entry.get("human_reviewed") is True for entry in all_truth
        ),
        "human_reviewed_total": len(all_truth),
        "excluded_from_accuracy": excluded,
        "per_field_accuracy": accuracy,
        "overall_accuracy": overall,
        "misses": misses,
        "needs_human_count": sum(r.get("status") == "needs_human" for r in results),
        "api_error_count": sum(r.get("status") == "error" for r in results),
    }
    OUTPUT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUTPUT_FILE}")
    for label in ("pass1", "pass2"):
        print(f"  {label}: {overall[label]}")


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
