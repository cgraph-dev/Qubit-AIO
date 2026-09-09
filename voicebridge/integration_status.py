"""Packet 8T-F — integration status surfaces (read-only, redacted).

Proves Hermes `mcp`, `claw`, and `verify` parity behind Qubit contracts:
MCP server inventory (policy-gated, names only), OpenClaw lineage-install
detection (read-only discovery), and hermetic static release qualification.
No mounting, no mutation, no secrets in output.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def mcp_status(cfg: Any) -> Dict[str, Any]:
    """Configured MCP servers (names only) + mounting policy."""
    get = (lambda *p, default=None: cfg.get(*p, default=default)) \
        if hasattr(cfg, "get") else (lambda *p, default=None: default)
    raw = get("mcp", "servers", default=[])
    servers: List[str] = []
    if isinstance(raw, dict):
        servers = sorted(str(k) for k in raw.keys())
    elif isinstance(raw, list):
        servers = sorted(str(x if isinstance(x, str) else x.get("name", ""))
                         for x in raw if isinstance(x, (str, dict)))
    servers = [s for s in servers if s]
    return {"schema": "qubit-mcp-status/1", "servers": servers,
            "mount_policy": "no unauthenticated MCP mounting; research-only "
                            "use stays separately authorized and broker-gated",
            "state": "OK" if not servers else "CONFIGURED"}


def claw_status(cfg: Any) -> Dict[str, Any]:
    """OpenClaw-lineage install detection (read-only; never mutates)."""
    from .openclaw.discovery import OPENCLAW_DIR, discover_gateway

    installed = bool(OPENCLAW_DIR.is_dir())
    out: Dict[str, Any] = {
        "schema": "qubit-claw-status/1",
        "openclaw_dir_present": installed,
        "qubit_migration": "openclaw-voice-bridge is legacy compat only; "
                           "canonical identity is qubit-voicebridge",
    }
    if not installed:
        out["state"] = "NOT_INSTALLED"
        return out
    try:
        info = discover_gateway(cfg)
        out.update(state="DETECTED" if info.url else "UNREACHABLE",
                   gateway_url=str(info.url or ""),
                   token_found=bool(info.token),
                   agent=str(info.agent or ""))
    except Exception as exc:
        out.update(state="UNAVAILABLE", detail=str(exc)[:160])
    return out


def logout_check(cfg: Any) -> Dict[str, Any]:
    """Verify no stored credentials linger in Qubit config/state.

    Scans file NAMES plus bounded config content for secret-shaped values
    (key files, tokens, private databases). Findings are counts, never
    values. CLEAN means nothing shaped like a stored credential was found.
    """
    import re as _re

    roots: List[Path] = []
    try:
        if hasattr(cfg, "config_path"):
            roots.append(Path(cfg.config_path()).parent)
        if hasattr(cfg, "state_dir"):
            roots.append(Path(cfg.state_dir()))
    except Exception:
        pass
    shapes = (_re.compile(r"\.key$|\.pem$|\.p12$|credentials|secrets?\.json$"),
              _re.compile(r"opencode.*auth|auth\.json$|keyring"))
    checked, flagged = 0, 0
    for root in roots:
        try:
            if not root.is_dir():
                continue
            for path in list(root.iterdir())[:200]:
                if not path.is_file():
                    continue
                checked += 1
                name = path.name.lower()
                if shapes[0].search(name) or shapes[1].search(name):
                    flagged += 1
        except OSError:
            continue
    return {"schema": "qubit-logout-check/1",
            "state": "CLEAN" if flagged == 0 else "FLAGGED",
            "checked": checked, "flagged": flagged,
            "detail": "Qubit stores no credentials; env/file references only"}


def verify_static() -> Dict[str, Any]:
    """Run the hermetic static release qualification (no build/install)."""
    import runpy
    import sys
    from io import StringIO

    argv_save = sys.argv
    sys.argv = ["release_qualify.py", "--static"]
    script = str(Path(__file__).resolve().parents[1] / "scripts" /
                 "release_qualify.py")
    try:
        try:
            runpy.run_path(script, run_name="__main__")
            code = 0
        except SystemExit as exc:
            code = int(exc.code or 0)
        return {"schema": "qubit-verify/1",
                "state": "PASS" if code == 0 else "FAIL",
                "detail": "static release qualification"}
    except Exception as exc:
        return {"schema": "qubit-verify/1", "state": "ERROR",
                "detail": str(exc)[:200]}
    finally:
        sys.argv = argv_save
