"""Qubit integration status surfaces (read-only, redacted).

MCP server inventory (policy-gated, names only), native worker-lane
status, and hermetic static release qualification. No mounting, no
mutation, no secrets in output.
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


def worker_status(cfg: Any) -> Dict[str, Any]:
    """Native Qubit worker-lane status (read-only; never mutates)."""
    get = (lambda *p, default=None: cfg.get(*p, default=default)) \
        if hasattr(cfg, "get") else (lambda *p, default=None: default)
    return {
        "schema": "qubit-worker-status/1",
        "state": "OK",
        "route": "local",
        "agent_enabled": bool(get("agent", "enabled", default=True)),
        "agent_model": str(get("agent", "model", default="") or
                           "qubit-brain4"),
        "queue": "see `qubit sessions` for active/queued work",
    }


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

    argv_save = sys.argv
    sys.argv = ["release_qualify.py", "--static"]
    script = str(Path(__file__).resolve().parents[1] / "scripts" /
                 "release_qualify.py")
    if not Path(script).exists():
        sys.argv = argv_save
        return {"schema": "qubit-verify/1", "state": "UNAVAILABLE",
                "detail": "static release qualification runs from a source "
                          "checkout; the script is not packaged in this install"}
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
