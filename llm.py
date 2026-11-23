"""Cached JSON wrapper around free LLM tiers, with a 429 model fallback chain.

Each model gets its own cache key so outputs from different models are never
mixed. Old cache entries (written before model-aware keys) are still readable
for the main model, so changing models does not throw away prior work.
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

CACHE_DIR = Path("data/cache/llm")
DELAY = float(os.getenv("LLM_DELAY_SECONDS", "2.0"))
DEFAULT_FALLBACK_MODELS = "openai/gpt-oss-120b,qwen/qwen3.8-27b"
_last_call = 0.0


def _targets() -> list[dict]:
    """Main model first, then free fallbacks, then optional Gemini free tier."""
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    model = os.getenv("LLM_MODEL", "gemini-2.5-flash-lite")
    api_key = os.getenv("LLM_API_KEY", "")
    targets = [{"provider": provider, "model": model, "api_key": api_key}]

    fallback_env = os.getenv("LLM_FALLBACK_MODELS")
    if fallback_env is None and provider == "groq":
        fallback_env = DEFAULT_FALLBACK_MODELS
    for name in (fallback_env or "").split(","):
        name = name.strip()
        if name:
            targets.append({"provider": "groq", "model": name, "api_key": api_key})

    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if gemini_key:
        targets.append({
            "provider": "gemini",
            "model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
            "api_key": gemini_key,
        })

    unique = []
    seen = set()
    for target in targets:
        key = (target["provider"], target["model"])
        if key not in seen:
            seen.add(key)
            unique.append(target)
    return unique


def _cache_path(system: str, user: str, provider: str, model: str) -> Path:
    digest = hashlib.sha256(
        (f"{provider}|{model}\n---\n{system}\n---\n{user}").encode("utf-8")
    ).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def _legacy_cache_path(system: str, user: str) -> Path:
    """Cache path used before model-aware keys; only the main model may read it."""
    digest = hashlib.sha256((system + "\n---\n" + user).encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def _parse_json(raw: str) -> dict:
    """Accept plain JSON or a fenced ```json block."""
    if not raw:
        raise ValueError("empty response")
    raw = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in response")
    value = json.loads(raw[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("JSON response is not an object")
    return value


def _call(system: str, user: str, target: dict) -> str:
    provider = target["provider"]
    api_key = target["api_key"]
    model = target["model"]
    if not api_key:
        raise RuntimeError(f"{provider} API key is not set")

    if provider == "gemini":
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=system + "\n\n" + user,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        return response.text or ""
    if provider == "groq":
        from groq import Groq

        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return response.choices[0].message.content or ""
    raise RuntimeError(f"unsupported LLM_PROVIDER: {provider}")


def _cached(path: Path, target: dict) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _meta(target: dict, cached: bool, rate_limited: bool = False) -> dict:
    return {
        "model": target["model"],
        "provider": target["provider"],
        "cached": cached,
        "rate_limited": rate_limited,
    }


def complete_json_detailed(system: str, user: str) -> tuple[dict, dict]:
    """Return (JSON object, model metadata). Tries each free model in order."""
    global _last_call
    targets = _targets()
    rate_limited = []
    last_error = "no LLM targets configured"

    for index, target in enumerate(targets):
        path = _cache_path(system, user, target["provider"], target["model"])
        if path.exists():
            return _cached(path, target), _meta(target, cached=True)
        if index == 0:
            legacy = _legacy_cache_path(system, user)
            if legacy.exists():
                return _cached(legacy, target), _meta(target, cached=True)

        for attempt in range(2):
            wait = DELAY - (time.time() - _last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                result = _parse_json(_call(system, user, target))
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                return result, _meta(target, cached=False)
            except Exception as exc:  # includes HTTP 429 and invalid JSON
                message = str(exc)
                last_error = f"{type(exc).__name__}: {message}"
                if "429" in message or "rate" in message.lower():
                    # Do not retry the same model on a rate limit: the next model
                    # has its own daily quota. Never cache failures.
                    rate_limited.append(f"{target['model']}: {message[:160]}")
                    break
                if attempt == 0:
                    time.sleep(1)
            finally:
                _last_call = time.time()

    all_rate_limited = len(rate_limited) == len(targets)
    error = (
        "LLM_RATE_LIMITED: every model returned 429 (" + " | ".join(rate_limited) + ")"
        if all_rate_limited
        else last_error
    )
    return {"_error": error[:1000], "_rate_limited": all_rate_limited}, _meta(
        targets[-1], cached=False, rate_limited=all_rate_limited
    )


def complete_json(system: str, user: str) -> dict:
    """Return a JSON object for the prompt. Cached on disk; one retry on bad JSON."""
    result, _ = complete_json_detailed(system, user)
    return result
