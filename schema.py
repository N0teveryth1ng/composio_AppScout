"""Fixed record shape and simple validation for the research agent."""

AUTH_VALUES = {"OAuth2", "API key", "Basic", "Token", "Other", "unknown"}
SELF_SERVE_VALUES = {"self_serve", "trial_or_paid", "gated", "unknown"}
API_TYPE_VALUES = {"REST", "GraphQL", "SOAP", "SDK only", "none", "unknown"}
BREADTH_VALUES = {"broad", "medium", "narrow", "unknown"}
MCP_VALUES = {"official", "community", "none_found", "unknown"}
BUILDABILITY_VALUES = {"yes", "partial", "no", "unknown"}
BLOCKER_VALUES = {
    "none", "partner_gated", "paid_only", "no_public_api", "admin_approval",
    "contact_sales", "auth_complexity", "other", "unknown",
}
TOOLKIT_VALUES = {"yes", "no", "unknown"}
STATUS_VALUES = {"ok", "needs_human", "error"}
# Only finished research counts toward accuracy and pattern statistics.
# "error" means an LLM API failure; the next run retries those apps.
COUNTED_STATUSES = {"ok", "needs_human"}

CORE_FIELDS = ("auth_methods", "self_serve", "api_surface", "buildability")
EVIDENCE_FIELDS = {"auth_methods", "self_serve", "api_surface", "buildability"}


def empty_record(app: dict) -> dict:
    """A record with every field present and defaulted to 'unknown'."""
    return {
        "id": app["id"],
        "app": app["app"],
        "category": app["category"],
        "one_liner": "unknown",
        "auth_methods": ["unknown"],
        "self_serve": "unknown",
        "self_serve_note": "unknown",
        "api_surface": {
            "type": ["unknown"],
            "breadth": "unknown",
            "existing_mcp": "unknown",
            "mcp_url": None,
        },
        "buildability": "unknown",
        "main_blocker": "unknown",
        "blocker_note": "unknown",
        "evidence": [],
        "has_composio_toolkit": "unknown",
        "status": "needs_human",
        "needs_human_reason": "no research performed",
        "error_reason": "none",
        "llm_model": "unknown",
        "pass": 1,
    }


def _is_known(value) -> bool:
    if isinstance(value, list):
        return any(_is_known(item) for item in value)
    return value not in (None, "", "unknown")


def validate_record(record: dict) -> list[str]:
    """Return a list of human-readable problems. Empty list means valid."""
    errors = []
    for key in (
        "id", "app", "category", "one_liner", "auth_methods", "self_serve",
        "self_serve_note", "api_surface", "buildability", "main_blocker",
        "blocker_note", "evidence", "has_composio_toolkit", "status",
        "needs_human_reason", "pass",
    ):
        if key not in record:
            errors.append(f"missing field: {key}")
    if errors:
        return errors

    if record["pass"] not in (1, 2):
        errors.append("pass must be 1 or 2")
    if record["status"] not in STATUS_VALUES:
        errors.append(f"bad status: {record['status']}")
    # Older records predate these fields; only validate them when present.
    if "llm_model" in record and not isinstance(record["llm_model"], str):
        errors.append("llm_model must be a string")
    if record["has_composio_toolkit"] not in TOOLKIT_VALUES:
        errors.append(f"bad has_composio_toolkit: {record['has_composio_toolkit']}")

    auth = record["auth_methods"]
    if not isinstance(auth, list) or not auth:
        errors.append("auth_methods must be a non-empty list")
    else:
        for value in auth:
            if value not in AUTH_VALUES:
                errors.append(f"bad auth method: {value}")

    if record["self_serve"] not in SELF_SERVE_VALUES:
        errors.append(f"bad self_serve: {record['self_serve']}")
    if record["buildability"] not in BUILDABILITY_VALUES:
        errors.append(f"bad buildability: {record['buildability']}")
    if record["main_blocker"] not in BLOCKER_VALUES:
        errors.append(f"bad main_blocker: {record['main_blocker']}")

    api = record["api_surface"]
    if not isinstance(api, dict):
        errors.append("api_surface must be an object")
    else:
        for key in ("type", "breadth", "existing_mcp", "mcp_url"):
            if key not in api:
                errors.append(f"api_surface missing: {key}")
        if not isinstance(api.get("type"), list) or not api["type"]:
            errors.append("api_surface.type must be a non-empty list")
        else:
            for value in api["type"]:
                if value not in API_TYPE_VALUES:
                    errors.append(f"bad API type: {value}")
        if api.get("breadth") not in BREADTH_VALUES:
            errors.append(f"bad breadth: {api.get('breadth')}")
        if api.get("existing_mcp") not in MCP_VALUES:
            errors.append(f"bad existing_mcp: {api.get('existing_mcp')}")
        if api.get("mcp_url") is not None and not isinstance(api["mcp_url"], str):
            errors.append("mcp_url must be a string or null")

    evidence = record["evidence"]
    if not isinstance(evidence, list):
        errors.append("evidence must be a list")
    else:
        for i, item in enumerate(evidence):
            if not isinstance(item, dict) or not all(k in item for k in ("field", "url", "snippet")):
                errors.append(f"evidence[{i}] needs field, url and snippet")
                continue
            if item["field"] not in EVIDENCE_FIELDS:
                errors.append(f"evidence[{i}] has unknown field: {item['field']}")
            if not str(item["url"]).startswith("http"):
                errors.append(f"evidence[{i}] url is not http(s)")
            if not str(item["snippet"]).strip():
                errors.append(f"evidence[{i}] snippet is empty")

        # Every non-unknown core claim needs at least one evidence entry.
        claims = {
            "auth_methods": auth,
            "self_serve": record["self_serve"],
            "buildability": record["buildability"],
        }
        if isinstance(api, dict):
            claims["api_surface"] = [
                api.get("type"), api.get("breadth"), api.get("existing_mcp")
            ]
        for field, values in claims.items():
            if _is_known(values) and not any(
                isinstance(e, dict) and e.get("field") == field for e in evidence
            ):
                errors.append(f"{field} is non-unknown but has no evidence")

    if record["status"] == "ok" and any(
        not _is_known(v)
        for v in [
            auth,
            record["self_serve"],
            api.get("type") if isinstance(api, dict) else None,
        ]
    ):
        errors.append("status=ok but auth, API type or self_serve is unknown")
    if record["status"] == "needs_human" and not record.get("needs_human_reason"):
        errors.append("needs_human requires a reason")
    if record["status"] == "error" and not record.get("error_reason"):
        errors.append("error requires an error_reason")
    return errors


def counted_records(records: list[dict]) -> list[dict]:
    """Records eligible for accuracy and pattern stats (no API-error records)."""
    return [record for record in records if record.get("status") in COUNTED_STATUSES]


def validate_all(records: list[dict]) -> list[str]:
    """Validate a list of records. Returns 'id: problem' strings."""
    problems = []
    for record in records:
        for error in validate_record(record):
            problems.append(f"{record.get('id', '?')} ({record.get('app', '?')}): {error}")
    return problems
