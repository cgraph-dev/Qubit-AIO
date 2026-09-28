"""Wave 22 — no publicly reachable legacy Hermes update route.

Owner directive: replace the legacy ``/api/hermes/update.*`` alias with the
canonical Qubit update surface and leave **no** publicly reachable
Hermes-named update route.

The canonical surface is:

* ``GET  /api/qubit/update/check``      — real read-only version probe (200);
* ``POST /api/qubit/update``            — real confirmed, reversible apply;
* ``POST /api/qubit/update/rollback``   — real confirmed rollback.

This suite pins, through the production compat dispatcher:

(a) the served route table registers zero ``/api/hermes`` paths;
(b) the canonical Qubit update routes resolve to their real handlers;
(c) unknown ``GET/POST /api/qubit/update/*`` subpaths are the typed
    ``UPDATE_OPERATOR_OWNED`` gate, never a bare 404;
(d) the legacy path is gone (no route, an explicit 404 — never a fabricated
    success);
(e) the served OpenAPI document (``/docs``) advertises no legacy-branded path,
    while still advertising the canonical update operations and the checked-in
    source carries no ``/api/hermes/update`` literal.

The vendored dashboard-plugin namespace was renamed from
``/api/plugins/hermes-achievements/*`` to ``/api/plugins/qubit-achievements/*``
(the coordinated public-namespace rename this suite originally flagged). No
public route now carries a legacy brand token, so the exception is gone and the
route table is asserted to be completely clean.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_API_HERMES_RE = re.compile(r"^/api/hermes(?:/|$)", re.IGNORECASE)
_LEGACY_BRAND_RE = re.compile(r"hermes|nous|openclaw", re.IGNORECASE)


def _cfg(tmp_path, monkeypatch):
    """Hermetic Config in a throwaway XDG/HOME/QUBIT_HOME tree."""
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        target = tmp_path / var.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(target))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nwebui:\n  token: t\n")
    from voicebridge.config import Config

    return Config.load(p)


# --------------------------------------------------------------------------
# (a) served route table: zero /api/hermes paths
# --------------------------------------------------------------------------
def test_served_route_table_has_no_api_hermes_path():
    from voicebridge import qubit_compat as HC

    offenders = [(r["method"], r["pattern"]) for r in HC.ROUTES
                 if _API_HERMES_RE.match(str(r.get("pattern") or ""))]
    assert offenders == [], offenders

    gated = [(m, p) for m, p, _g in HC.GATED
             if _API_HERMES_RE.match(str(p))]
    assert gated == [], gated


def test_served_route_table_has_no_legacy_branded_path():
    """No public route carries a legacy brand token.

    The vendored plugin namespace is now ``/api/plugins/qubit-achievements/*``;
    a re-introduced alias would surface here.
    """
    from voicebridge import qubit_compat as HC

    legacy_routes = [r["pattern"] for r in HC.ROUTES
                     if _LEGACY_BRAND_RE.search(str(r.get("pattern") or ""))]
    assert legacy_routes == [], legacy_routes


def test_compat_source_no_longer_registers_the_legacy_alias():
    source = (REPO_ROOT / "voicebridge" / "qubit_compat.py").read_text(
        encoding="utf-8")
    for literal in ('"/api/hermes/update', "r\"/api/hermes/update",
                    "r'/api/hermes/update", "'/api/hermes/update"):
        assert literal not in source, f"legacy alias literal still present: {literal}"


# --------------------------------------------------------------------------
# (b) canonical routes resolve to their real handlers
# --------------------------------------------------------------------------
def test_canonical_qubit_update_routes_resolve():
    from voicebridge import qubit_compat as HC

    check, _ = HC.match("GET", "/api/qubit/update/check")
    assert check is not None, "GET /api/qubit/update/check not registered"
    assert check["disposition"] == "implemented", check
    assert check["handler"] == "h_qubit_update_check", check

    apply_route, _ = HC.match("POST", "/api/qubit/update")
    assert apply_route is not None, "POST /api/qubit/update not registered"
    assert apply_route["disposition"] == "implemented", apply_route
    assert apply_route["handler"] == "h_qubit_update_apply", apply_route

    rollback, _ = HC.match("POST", "/api/qubit/update/rollback")
    assert rollback is not None, "POST /api/qubit/update/rollback not registered"
    assert rollback["disposition"] == "implemented", rollback
    assert rollback["handler"] == "h_qubit_update_rollback", rollback

    assert callable(HC.HANDLERS.get("h_qubit_update_check"))
    assert callable(HC.HANDLERS.get("h_qubit_update_apply"))
    assert callable(HC.HANDLERS.get("h_qubit_update_rollback"))


# --------------------------------------------------------------------------
# (c) unknown canonical subpaths are typed (GET and POST), never 404
# --------------------------------------------------------------------------
def test_unknown_update_subpaths_are_typed_gate_not_404(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for method in ("GET", "POST"):
        path = "/api/qubit/update/__no_such_subpath__"
        route, _ = HC.match(method, path)
        assert route is not None, (method, path)
        assert route["disposition"] == "gated", (method, path, route)
        assert route["gate"].get("code") == "UPDATE_OPERATOR_OWNED", route
        code, out = HC.dispatch(cfg, method, path, {}, {})
        assert code == 403, (method, path, code, str(out)[:200])
        assert out.get("ok") is False, (method, path, out)
        assert out.get("code") == "UPDATE_OPERATOR_OWNED", (method, path, out)
        assert out.get("qubit_counterpart"), (method, path, out)


# --------------------------------------------------------------------------
# (d) the legacy path is gone — explicit 404, never a fabricated 200
# --------------------------------------------------------------------------
def test_legacy_hermes_update_path_is_gone(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for method, path in (
            ("GET", "/api/hermes/update/check"),
            ("GET", "/api/hermes/update"),
            ("POST", "/api/hermes/update"),
            ("POST", "/api/hermes/update/rollback")):
        route, _ = HC.match(method, path)
        assert route is None, (method, path, route)
        code, out = HC.dispatch(cfg, method, path, {}, {})
        assert code == 404, (method, path, code, str(out)[:200])
        assert isinstance(out, dict) and out.get("ok") is False, (path, out)
        assert out.get("code") == "NOT_FOUND", (path, out)


# --------------------------------------------------------------------------
# (e) served OpenAPI: no legacy-branded path of any kind
# --------------------------------------------------------------------------
def test_served_openapi_has_no_legacy_branded_path(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, doc = HC.h_openapi(cfg, {}, {}, {})
    assert code == 200, code
    paths = doc["paths"]
    legacy = sorted(p for p in paths
                    if _LEGACY_BRAND_RE.search(p))
    assert legacy == [], legacy
    # The canonical operations are still advertised.
    assert "/api/qubit/update/check" in paths
    assert "/api/qubit/update" in paths
    assert "/api/qubit/update/rollback" in paths


# --------------------------------------------------------------------------
# (c) real behavior: check is a real 200; apply confirm-gates
# --------------------------------------------------------------------------
def test_update_check_real_200_and_apply_confirm_gates(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/update/check", {}, {})
    assert code == 200 and out.get("ok") is True, (code, out)
    for key in ("install_method", "current_version", "behind",
                "update_available", "can_apply", "update_command", "message"):
        assert key in out, (key, out)

    # Without confirm the real apply stages a typed 409 — it never runs.
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/update", {}, {})
    assert code == 409, (code, out)
    assert out.get("ok") is False, out
    assert out.get("code") == "CONFIRM_REQUIRED", out
