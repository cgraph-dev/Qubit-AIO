"""Safe defaults for the local Ollama provider in a fresh runtime config.

The first runtime-config writers (the dashboard model mirror and the ``/chat``
terminal profile write) can create ``<QUBIT_HOME>/config.yaml`` with only
``model: {provider: ollama, default: ...}``. The vendored resolver then refuses
the route with ``provider 'ollama' has no endpoint configured`` because there
is no ``providers.ollama.base_url``.

These helpers fill the missing provider keys for a local Ollama selection.
They never overwrite a value the user already set: every key is applied with
``setdefault`` semantics, and any other provider block is left untouched.
"""

from __future__ import annotations

from typing import Any

OLLAMA_LOCAL_BASE_URL = "http://127.0.0.1:11434/v1"
OLLAMA_LOCAL_API_MODE = "chat_completions"
OLLAMA_LOCAL_DEFAULT_MODEL = "qubit-brain"

#: Only the keys a fresh local config needs. ``key_env`` stays empty because a
#: loopback Ollama server needs no credential.
OLLAMA_PROVIDER_DEFAULTS = {
    "base_url": OLLAMA_LOCAL_BASE_URL,
    "api_mode": OLLAMA_LOCAL_API_MODE,
    "default_model": OLLAMA_LOCAL_DEFAULT_MODEL,
    "key_env": "",
}


def is_ollama_provider(provider: Any) -> bool:
    """True for the built-in local Ollama provider id."""
    return str(provider or "").strip().lower() == "ollama"


def ensure_ollama_provider_defaults(config: dict, *, provider: Any = "ollama") -> bool:
    """Fill missing ``providers.ollama`` defaults in *config* in place.

    Returns ``True`` when at least one key was added. Existing values (for
    example a user's own ``providers.ollama.base_url``) are preserved, and a
    malformed ``providers``/``providers.ollama`` value is left exactly as the
    user wrote it instead of being replaced.
    """
    if not isinstance(config, dict) or not is_ollama_provider(provider):
        return False

    providers = config.get("providers")
    if providers is None:
        providers = {}
        config["providers"] = providers
    if not isinstance(providers, dict):
        return False

    entry = providers.get("ollama")
    if entry is None:
        entry = {}
        providers["ollama"] = entry
    if not isinstance(entry, dict):
        return False

    changed = False
    for key, value in OLLAMA_PROVIDER_DEFAULTS.items():
        if key not in entry:
            entry[key] = value
            changed = True
    return changed
