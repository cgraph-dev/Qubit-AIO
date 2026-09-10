"""Qubit auth login/logout via manual-handoff enrollment.

Qubit ``login`` guides interactive OAuth/vendor-portal flows, ``logout``
clears stored credentials, and ``portal`` opens the vendor subscription.
Qubit never automates those flows: the user personally completes login,
2FA, CAPTCHA, and recovery challenges in their own browser (Brain
credential design: manual handoff, never bypassed, guessed, or captured).
Instead this module provides the Qubit-native workflow:

* ``auth login --provider NAME`` prints ordered manual steps tailored to
  the provider's configured key-reference kind (``env:``/``file:``) plus
  the vendor portal URL when known. Guidance only: nothing is written,
  nothing is read beyond reference-kind metadata, nothing is opened
  unless ``--open`` is passed, and no value is ever captured;
* ``auth verify --provider NAME`` re-probes the reference (env presence /
  file existence + size) and reports ``CONFIGURED``/``MISSING`` — the
  machine-readable close of the loop the steps describe;
* ``auth logout`` (existing residue scan) stays the cleanup proof.

Portal URLs are static documentation strings, never fetched. Unknown
providers are typed ``AUTH_UNKNOWN_PROVIDER`` (available names are
non-secret and listed). Steps name variables and paths, never values;
every echo passes through :func:`redact_text`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

UNKNOWN_CODE = "AUTH_UNKNOWN_PROVIDER"

# Static vendor-console documentation (never fetched, never trusted as
# authority — the user verifies the URL in their own browser).
KNOWN_PORTALS: Dict[str, str] = {
    "openai": "https://platform.openai.com/api-keys",
    "anthropic": "https://console.anthropic.com/settings/keys",
    "xai": "https://console.x.ai/team/api-keys",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "openrouter": "https://openrouter.ai/keys",
    "groq": "https://console.groq.com/keys",
    "together": "https://api.together.xyz/settings/api-keys",
    "mistral": "https://console.mistral.ai/api-keys",
    "google": "https://aistudio.google.com/apikey",
    "gemini": "https://aistudio.google.com/apikey",
    "nous": "https://portal.nousresearch.com/",
}


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def _providers(cfg: Any) -> Dict[str, Dict[str, Any]]:
    from .providers.policy import FrontierPolicy

    return FrontierPolicy.from_config(cfg).providers


def _ref_kind(ref: str) -> str:
    if ref.startswith("env:"):
        return "env"
    if ref.startswith("file:"):
        return "file"
    return "missing"


def portal_url(provider: str) -> Optional[str]:
    """Static vendor-console URL for a provider, if documented."""
    return KNOWN_PORTALS.get((provider or "").strip().lower())


def login_plan(cfg: Any, provider: str, *,
               requested_by: str = "cli:user",
               open_browser: bool = False) -> Dict[str, Any]:
    """Build the manual-handoff enrollment plan for one provider.

    Read-only guidance: the returned steps tell the user exactly what to do
    in their own browser and shell. With ``open_browser`` the documented
    portal page is opened in the default browser (the user still performs
    every step); otherwise only the URL text is returned.
    """
    name = (provider or "").strip()
    providers = _providers(cfg)
    if name not in providers:
        return {"state": "UNKNOWN", "code": UNKNOWN_CODE,
                "provider": _redact(name)[:80],
                "detail": f"unknown provider {name!r}; configure it under "
                          f"intent.frontier.providers first",
                "known_providers": sorted(providers),
                "owner": "qubit-policy",
                "rollback": "nothing changed; nothing to roll back"}
    ref = str((providers.get(name) or {}).get("key_ref", "") or "")
    kind = _ref_kind(ref)
    url = portal_url(name)
    steps: List[str] = []
    if url:
        steps.append(f"1. Open your vendor console yourself: {url} "
                     f"(verify the address in your browser)")
    else:
        steps.append("1. Open your provider's console/API-keys page yourself "
                     "(verify the address in your browser)")
    steps.append("2. Complete the provider login there yourself, including "
                 "any 2FA, CAPTCHA, SMS/email code, or recovery challenge — "
                 "Qubit never performs these steps and never asks for codes")
    steps.append("3. Create or retrieve an API key with the smallest scope "
                 "the work needs")
    if kind == "env":
        var = ref[len("env:"):].strip()
        steps.append(f"4. Put the key in your shell environment only, e.g. "
                     f"export {_redact(var)[:80]}='…' in a private shell "
                     f"profile (never paste it here)")
        steps.append(f"5. Close the loop with "
                     f"`voicebridge auth verify --provider {_redact(name)[:80]}`")
    elif kind == "file":
        where = ref[len("file:"):].strip()
        steps.append(f"4. Save the key to {_redact(where)[:120]} with owner-only "
                     f"permissions (chmod 600); never commit it or paste it here")
        steps.append(f"5. Close the loop with "
                     f"`voicebridge auth verify --provider {_redact(name)[:80]}`")
    else:
        steps.append("4. Decide how Qubit should reference the key "
                     "(intent.frontier.providers.<name>.key_ref as "
                     "`env:VAR_NAME` or `file:/path/to/key`), then store it "
                     "outside Qubit with owner-only permissions")
        steps.append(f"5. Close the loop with "
                     f"`voicebridge auth verify --provider {_redact(name)[:80]}`")
    steps.append("6. Ask the owner to allowlist provider/model pairs before "
                 "any frontier use (frontier allowlist; local stays default)")
    opened = False
    if open_browser and url:
        try:
            import webbrowser

            opened = bool(webbrowser.open(url))
        except Exception:
            opened = False
    return {"state": "OK", "schema": "qubit-auth-login/1", "provider": name,
            "key_ref": kind, "portal_url": url,
            "handoff": "manual: 2FA/CAPTCHA/recovery stay with the user",
            "steps": steps, "opened": opened,
            "values_captured": False}


def verify_provider(cfg: Any, provider: str) -> Dict[str, Any]:
    """Re-probe one provider's credential reference (read-only check)."""
    from .providers.policy import FrontierPolicy

    name = (provider or "").strip()
    policy = FrontierPolicy.from_config(cfg)
    if name not in policy.providers:
        return {"state": "UNKNOWN", "code": UNKNOWN_CODE,
                "provider": _redact(name)[:80],
                "known_providers": sorted(policy.providers),
                "owner": "qubit-policy",
                "rollback": "nothing changed; nothing to roll back"}
    ref = str((policy.providers.get(name) or {}).get("key_ref", "") or "")
    kind = _ref_kind(ref)
    configured = False
    if kind == "env":
        var = ref[len("env:"):].strip()
        configured = bool(var and os.environ.get(var, ""))
    elif kind == "file":
        try:
            p = Path(ref[len("file:"):].strip()).expanduser()
            configured = p.is_file() and p.stat().st_size > 0
        except OSError:
            configured = False
    allowed = {a.split("/")[0] for a in policy.allowlist}
    return {"state": "CONFIGURED" if configured else "MISSING",
            "schema": "qubit-auth-verify/1", "provider": name,
            "key_ref": kind, "frontier_enabled": policy.enabled,
            "allowlisted": name in allowed}
