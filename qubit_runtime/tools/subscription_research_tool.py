"""Account-backed research through the local OmniRoute gateway.

This tool lets Qubit use the user's own Perplexity Pro / Gemini Web sessions
without a paid search API. OmniRoute holds the provider session and exposes an
OpenAI-compatible endpoint on loopback; this tool only talks to that local
gateway and never reads a browser cookie or stores a credential itself.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional

from tools.registry import registry, tool_error, tool_result

_MAX_ANSWER_CHARS = 20_000
_DEFAULT_BASE_URL = "http://127.0.0.1:20128/v1"
_PERPLEXITY_MODEL = "perplexity-web/pplx-auto"
_SYSTEM_PROMPT = (
    "Use your built-in web search for this request. Answer the question "
    "directly and include a short Sources section with the URLs you relied "
    "on. If you cannot search, say that explicitly instead of guessing."
)


def _config() -> Any:
    try:
        from qubit_cli.config import load_config
        return load_config()
    except Exception:
        return {}


def _omni_settings() -> tuple[str, str]:
    cfg = _config()
    provider = {}
    if isinstance(cfg, dict):
        providers = cfg.get("providers")
        if isinstance(providers, dict):
            provider = providers.get("omniroute") or {}
    base = ""
    key_env = "OMNIROUTE_API_KEY"
    if isinstance(provider, dict):
        base = str(provider.get("base_url") or "").strip()
        key_env = str(provider.get("key_env") or key_env).strip() or key_env
    if key_env.startswith("env:"):
        key_env = key_env[4:]
    key = os.environ.get(key_env, "").strip()
    if not key:
        try:
            from agent.secret_scope import get_secret
            key = str(get_secret(key_env, "") or "").strip()
        except Exception:
            key = ""
    if not key:
        # The dashboard/gateway loader normally populates the environment; a
        # direct CLI/tool call may not have run it yet. Read the profile .env
        # without logging the value.
        try:
            from qubit_constants import get_qubit_home
            env_path = get_qubit_home() / ".env"
            for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith(f"{key_env}="):
                    key = line.split("=", 1)[1].strip().strip("'\"")
                    break
        except Exception:
            pass
    return (base or _DEFAULT_BASE_URL).rstrip("/"), key


def _configured() -> bool:
    _base, key = _omni_settings()
    return bool(key)


def _request(base: str, key: str, path: str, payload: Optional[dict] = None,
             timeout: float = 120.0) -> Any:
    data = None
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{base}{path}", data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read(2_000_000)
    return json.loads(body.decode("utf-8", "replace"))


def _gemini_model(base: str, key: str) -> tuple[Optional[str], str]:
    """First connected Gemini Web model, or a typed reason."""
    try:
        data = _request(base, key, "/models", timeout=10.0)
    except Exception as exc:  # noqa: BLE001 - typed refusal below
        return None, f"could not list OmniRoute models: {type(exc).__name__}"
    rows = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return None, "OmniRoute returned an unexpected model list"
    ids = [str(r.get("id") or "") for r in rows if isinstance(r, dict)]
    preferred = [i for i in ids if i.startswith("gemini-web/") or i.startswith("gemini/")]
    if not preferred:
        return None, ("Gemini Web is not connected in OmniRoute. Connect it in "
                      "OmniRoute → Providers → Gemini Web, then retry.")
    preferred.sort(key=lambda s: (
        0 if "pro" in s.lower() else 1,
        0 if "argon" in s.lower() else 1,
        len(s),
    ))
    return preferred[0], ""


def _sources(text: str) -> list[str]:
    import re
    seen: list[str] = []
    for url in re.findall(r'https?://[^\s)>\]"\']+', text or ""):
        url = url.rstrip(".,;:!?")
        if url not in seen:
            seen.append(url)
    return seen[:20]


def _handle_subscription_research(args: dict[str, Any]) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return tool_error("query is required", reason="invalid_request")
    if len(query) > 8_000:
        return tool_error("query is too long (max 8000 characters)", reason="invalid_request")
    engine = str(args.get("engine") or "auto").strip().lower()
    if engine not in {"auto", "perplexity", "gemini"}:
        return tool_error("engine must be auto, perplexity or gemini", reason="invalid_request")

    base, key = _omni_settings()
    if not key:
        return tool_error(
            "OmniRoute is not configured: OMNIROUTE_API_KEY is missing",
            reason="provider_not_configured")

    chosen = engine
    model = _PERPLEXITY_MODEL
    if engine in {"auto", "gemini"}:
        gmodel, why = _gemini_model(base, key)
        if engine == "gemini":
            if not gmodel:
                return tool_error(why, reason="provider_not_connected")
            chosen, model = "gemini", gmodel
        elif gmodel:
            chosen, model = "gemini", gmodel
    if chosen in {"auto", "perplexity"}:
        chosen, model = "perplexity", _PERPLEXITY_MODEL

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        "max_tokens": int(args.get("max_tokens") or 1600),
        "temperature": 0.2,
        "stream": False,
    }
    def _post(payload: dict) -> Any:
        return _request(base, key, "/chat/completions", payload, timeout=150.0)

    try:
        data = _post(payload)
    except Exception as exc:  # noqa: BLE001 - auto may fall back below
        if engine == "auto" and chosen == "gemini":
            # A connected Gemini Web can still be temporarily unhealthy; auto
            # must not fail when Perplexity is healthy.
            chosen, model = "perplexity", _PERPLEXITY_MODEL
            payload["model"] = model
            try:
                data = _post(payload)
            except Exception as exc2:  # noqa: BLE001
                return tool_error(
                    "Both Gemini Web and Perplexity failed",
                    reason="provider_error", engine="auto", model=model,
                    detail=f"gemini={type(exc).__name__}; perplexity={type(exc2).__name__}")
        else:
            detail = ""
            if isinstance(exc, urllib.error.HTTPError):
                try:
                    detail = exc.read(4000).decode("utf-8", "replace")
                except Exception:
                    pass
            return tool_error(
                f"OmniRoute request failed: {type(exc).__name__}",
                reason="provider_error", engine=chosen, model=model,
                detail=detail[:1000])

    choices = data.get("choices") if isinstance(data, dict) else None
    try:
        answer = str((choices[0].get("message") or {}).get("content") or "")
    except Exception:
        answer = ""
    answer = answer.strip()
    if not answer:
        if engine == "auto" and chosen == "gemini":
            chosen, model = "perplexity", _PERPLEXITY_MODEL
            payload["model"] = model
            try:
                data = _post(payload)
                choices = data.get("choices") if isinstance(data, dict) else None
                answer = str((choices[0].get("message") or {}).get("content") or "").strip()
            except Exception:
                answer = ""
        if not answer:
            return tool_error("OmniRoute returned an empty answer", reason="empty_answer",
                              engine=chosen, model=model)
    return tool_result({
        "source": "subscription_research",
        "engine": chosen,
        "model": str(data.get("model") or model),
        "answer": answer[:_MAX_ANSWER_CHARS],
        "sources": _sources(answer),
        "usage": data.get("usage") if isinstance(data, dict) else None,
        "truncated": len(answer) > _MAX_ANSWER_CHARS,
    })


SUBSCRIPTION_RESEARCH_SCHEMA = {
    "name": "subscription_research",
    "description": (
        "Research the web through the user's own OmniRoute-connected accounts. "
        "Use engine='perplexity' for Perplexity Pro, engine='gemini' for the "
        "connected Gemini Web account, or engine='auto' to prefer Gemini when "
        "connected and otherwise Perplexity. Returns a concise answer plus the "
        "source URLs. This is account-backed research, not a paid API; if the "
        "selected provider is not connected, it returns a typed error instead "
        "of silently using another provider."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The research question."},
            "engine": {"type": "string", "enum": ["auto", "perplexity", "gemini"],
                       "default": "auto"},
            "max_tokens": {"type": "integer", "minimum": 100, "maximum": 4000,
                           "default": 1600},
        },
        "required": ["query"],
    },
}


registry.register(
    name="subscription_research",
    toolset="web",
    schema=SUBSCRIPTION_RESEARCH_SCHEMA,
    handler=_handle_subscription_research,
    check_fn=_configured,
    emoji="🔎",
    description="Research via the user's OmniRoute Perplexity/Gemini accounts",
    max_result_size_chars=60_000,
)
