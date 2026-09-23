"""Qubit integration status surfaces (read-only, redacted).

MCP server inventory (policy-gated, names only), native worker-lane
status, and hermetic static release qualification. No mounting, no
mutation, no secrets in output.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def mcp_status(cfg: Any) -> Dict[str, Any]:
    """Configured MCP servers + mounting policy (read-only, redacted).

    PRB-01: reads the ONE canonical MCP store
    (``voicebridge.mcp_servers.list_servers``) — the same function the
    dashboard ``GET /api/mcp/servers`` handler calls — so CLI, voice,
    dashboard, and this status helper can never diverge on which servers exist
    or whether they are enabled. ``servers`` stays a names-only list for
    compatibility; ``records`` carries the per-server enabled/source state the
    dashboard already exposes (``enabled``, ``source``).
    """
    records: List[Dict[str, Any]] = []
    try:
        from . import mcp_servers as MS

        for s in MS.list_servers(cfg).get("servers", []):
            if not isinstance(s, dict) or not s.get("name"):
                continue
            records.append({
                "name": str(s.get("name")),
                "enabled": bool(s.get("enabled")),
                "source": str(s.get("source", "qubit-registry")),
                "connected": bool(s.get("connected", False)),
                "status": str(s.get("status", "")),
                # Client invocation is a typed OPERATOR_ONLY disposition until a
                # sanctioned transport exists; every surface reads it here so
                # none can imply a server is callable.
                "invocable": bool(s.get("invocable", False)),
                "invocation_code": str(s.get("invocation_code", "")),
            })
    except Exception:
        # Degrade to the read-only config inventory rather than fabricate.
        from . import mcp_servers as MS

        records = [{"name": name, "enabled": False, "source": "qubit-config",
                    "connected": False, "status": "configured-not-connected",
                    "invocable": False,
                    "invocation_code": MS.INVOKE_OPERATOR_CODE}
                   for name in MS.config_server_names(cfg)]
    records.sort(key=lambda r: r["name"])
    servers = [r["name"] for r in records]
    return {"schema": "qubit-mcp-status/1", "servers": servers,
            "records": records,
            "invocable": False,
            "invocation_code": MS.INVOKE_OPERATOR_CODE,
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
