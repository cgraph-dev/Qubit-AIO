"""Qubit proxy inventory + typed start refusal.

A local forwarding proxy server would bypass Qubit egress policy and
audit, so Qubit
ships no proxy server. The Qubit-native surface is:

* ``status`` — the proxy server state (always ``NOT_RUNNING``) plus the
  policy that keeps it that way (read-only);
* ``providers`` — per-provider proxy eligibility from the frontier config
  (reference kind, allowlist state; names only, never values);
* ``start`` — typed ``PROXY_NO_SERVER`` refusal (a forwarder would bypass
  minimum-necessary egress and audit; frontier use flows through the
  brokered gateway instead).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

NO_SERVER_CODE = "PROXY_NO_SERVER"


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def status() -> Dict[str, Any]:
    """Proxy server state (read-only; no server exists to inspect)."""
    return {"state": "NOT_RUNNING", "schema": "qubit-proxy-status/1",
            "bind": "none", "forwards": 0,
            "policy": "no forwarding server runs in Qubit; provider use "
                      "flows through the brokered frontier gateway with "
                      "minimum-necessary egress, budgets, and audit"}


def providers(cfg: Any) -> Dict[str, Any]:
    """Per-provider proxy eligibility (references only, never values)."""
    from .providers.policy import FrontierPolicy

    policy = FrontierPolicy.from_config(cfg)
    allowed = {a.split("/")[0] for a in policy.allowlist}
    rows: List[Dict[str, Any]] = []
    for name in sorted(policy.providers.keys()):
        ref = str((policy.providers.get(name) or {}).get("key_ref", "") or "")
        if ref.startswith("env:"):
            kind = "env"
        elif ref.startswith("file:"):
            kind = "file"
        else:
            kind = "missing"
        rows.append({"provider": name, "key_ref": kind,
                     "allowlisted": name in allowed,
                     "proxy_eligible": False,
                     "why": "forwarding is disabled; use the brokered "
                            "frontier gateway"})
    return {"state": "OK", "schema": "qubit-proxy-providers/1",
            "frontier_enabled": policy.enabled, "providers": rows}


def refuse_start() -> Dict[str, Any]:
    """Typed refusal for starting a forwarding server (egress invariant)."""
    return {"state": "REFUSED", "code": NO_SERVER_CODE,
            "explanation": "Qubit Agent cannot do this: starting a local "
                           "forwarding proxy would bypass minimum-necessary "
                           "egress policy and audit; provider use flows "
                           "through the brokered frontier gateway instead.",
            "owner": "qubit-policy",
            "rollback": "no server started; nothing to roll back"}
