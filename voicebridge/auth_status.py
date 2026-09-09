"""Packet 8T-F — credential-reference inventory + session listing.

Read-only surfaces proving Hermes `auth`/`sessions` parity behind Qubit
contracts: credential REFERENCES (never values) per configured provider,
and recent action sessions from the receipt store (redacted). No keys,
tokens, cookies, or private bodies ever leave this module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List


def auth_status(cfg: Any) -> Dict[str, Any]:
    """Inventory credential references for every configured provider.

    Reports reference KIND (env/file/missing) and configured-ness only.
    Values are never read into output; file checks use existence + size.
    """
    from .providers.policy import FrontierPolicy

    policy = FrontierPolicy.from_config(cfg)
    providers = []
    for name in sorted(policy.providers.keys()):
        ref = str((policy.providers.get(name) or {}).get("key_ref", "") or "")
        kind, configured = "missing", False
        if ref.startswith("env:"):
            kind = "env"
            var = ref[len("env:"):].strip()
            configured = bool(var and os.environ.get(var, ""))
        elif ref.startswith("file:"):
            kind = "file"
            try:
                p = Path(ref[len("file:"):].strip()).expanduser()
                configured = p.is_file() and p.stat().st_size > 0
            except OSError:
                configured = False
        allowed = {a.split("/")[0] for a in policy.allowlist}
        providers.append({"provider": name, "key_ref": kind,
                          "configured": configured,
                          "allowlisted": name in allowed})
    return {"schema": "qubit-auth-status/1", "frontier_enabled": policy.enabled,
            "providers": providers}


def recent_sessions(cfg: Any, limit: int = 20) -> Dict[str, Any]:
    """Recent action sessions (receipts) from the state tree, redacted."""
    from .redaction import redact_text

    state_dir = cfg.state_dir() if hasattr(cfg, "state_dir") else Path(".")
    path = Path(state_dir) / "actions.json"
    items: List[Dict[str, Any]] = []
    try:
        raw = json.loads(path.read_text())
        receipts = raw.get("receipts", {}) if isinstance(raw, dict) else {}
        ordered = sorted(receipts.values(),
                         key=lambda r: float(r.get("created_at", 0) or 0),
                         reverse=True)[:max(1, min(int(limit), 100))]
        for r in ordered:
            if not isinstance(r, dict):
                continue
            items.append({
                "receipt_id": str(r.get("receipt_id", "")),
                "action_id": str(r.get("action_id", "")),
                "state": str(r.get("state", "")),
                "code": str(r.get("code", "")),
                "runtime": str(r.get("runtime", "qubit-broker")),
                "created_at": r.get("created_at", 0),
                "detail": redact_text(str(r.get("detail", "")))[:160],
            })
    except (OSError, ValueError) as exc:
        return {"schema": "qubit-sessions/1", "state": "UNAVAILABLE",
                "detail": f"no readable receipt store: {exc}"[:160],
                "sessions": []}
    return {"schema": "qubit-sessions/1", "state": "OK",
            "count": len(items), "sessions": items}
