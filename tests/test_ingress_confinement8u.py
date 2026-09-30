"""Ingress confinement (Packet 8U, wave 17).

The dashboard SPA reaches the backend through ``/agent/api/*`` and
``webui._hermes_api`` serves the vendored Hermes fork in-process (the
Qubit-owned Hermes runtime). The corrected read routing is:

* sensitive/confined surfaces + Qubit-owned surfaces + the explicit
  ``qubit_compat.GATED`` table stay on Qubit;
* every other read the vendored fork declares is served by the fork, so the
  preserved dashboard gets the fork's real data;
* the old ``_owned_read_patterns`` rule ("all implemented compat routes are
  no-proxy") is deliberately NOT used: it made empty Qubit stubs shadow the
  fork's real readers and broke the dashboard.

The old proxy-first branch also leaked:

* ``/api/fs/*`` and ``/api/files*`` reads to the fork's unrestricted filesystem
  reader instead of Qubit's project-root-confined ``h_fs_*`` handlers
  (``finding.fs_confinement_bypass``);
* the fork's ``/auth/*`` login/callback/native endpoints because the sensitive
  list only carried ``/api/auth`` (``finding.auth_prefix_bypass``);
* raw secrets/config because only some secret routes were gated.

These tests pin the repaired policy: the sensitive/Qubit-owned/GATED sets never
proxy, while fork-declared non-sensitive reads are fork-served. They are
pure-policy plus real-dispatch and never start the fork bridge unless the test
explicitly exercises the live listener.
"""
from __future__ import annotations

import http.client
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# --------------------------------------------------------------- A1/A2 inputs
#: Prefixes that must never reach the fork proxy (secrets, identity, raw
#: config, and the filesystem confinement surface).
REQUIRED_PREFIXES = (
    "/api/config/raw",
    "/api/secrets",
    "/api/keys",
    "/api/auth",
    "/auth",
    "/api/oauth",
    "/api/mcp/oauth",
    "/api/fs",
    "/api/files",
)

#: Fork-declared, non-sensitive reads the corrected cutover serves from the
#: vendored fork (real dashboard data), never from the Qubit compat stub.
FORK_SERVED_READS = (
    "/api/status",
    "/api/config",
    "/api/env",
    "/api/health",
    "/api/sessions",
    "/api/logs",
    "/api/skills",
    "/api/cron/jobs",
    "/api/profiles",
    "/api/memory",
    "/api/mcp/servers",
)


def test_sensitive_prefixes_cover_fs_files_auth_and_raw_config():
    from voicebridge import qubit_asgi as HA

    for prefix in REQUIRED_PREFIXES:
        assert prefix in HA.SENSITIVE_READ_PREFIXES, prefix
        assert HA.gated_path(prefix) is True, prefix
        assert HA.gated_path(prefix + "/child") is True, prefix

    # ``/api/config`` is a fork-declared dashboard read, not a raw-secret
    # surface: only ``/api/config/raw`` stays gated (the operator's real config
    # document must come from the fork, not an empty Qubit stub).
    assert "/api/config" not in HA.SENSITIVE_READ_PREFIXES
    assert HA.gated_path("/api/config") is False


def test_fork_declared_reads_are_served_by_the_fork(monkeypatch):
    """Corrected cutover: a fork-declared, non-sensitive read is fork-served
    even though Qubit also implements a compat handler for it. ``known_route``
    is stubbed so this stays a pure policy composition check (the live listener
    tests below prove the real fork bytes)."""
    from voicebridge import qubit_asgi as HA

    monkeypatch.setattr(HA, "known_route", lambda _p: True)
    for path in FORK_SERVED_READS:
        assert HA.gated_path(path) is False, path
        assert HA.safe_to_proxy("GET", path) is True, path
    # The vendored credential-pool payload carries token_preview fragments, so
    # the read is gated exactly like the mutations; secret material never
    # reaches the browser.
    assert HA.gated_path("/api/credentials/pool") is True
    assert HA.safe_to_proxy("GET", "/api/credentials/pool") is False
    # Raw config and reveal surfaces are gated too.
    assert HA.gated_path("/api/config/raw") is True
    assert HA.safe_to_proxy("GET", "/api/config/raw") is False


# -------------------------------------------------------------- A1 confinement
def test_fs_and_files_reads_are_confined_to_qubit_handlers():
    """The fs/files read routes are gated, never proxied, and dispatch to the
    Qubit confined handler -- not the fork."""
    from voicebridge import qubit_asgi as HA
    from voicebridge import qubit_compat as HC

    expected = {
        "/api/fs/list": "h_fs_list",
        "/api/fs/tree": "h_fs_tree",
        "/api/fs/stat": "h_fs_stat",
        "/api/fs/read": "h_fs_read_text",
        "/api/fs/read-text": "h_fs_read_text",
        "/api/fs/read-data-url": "h_fs_read_data_url",
        "/api/fs/git-root": "h_fs_git_root",
        "/api/fs/default-cwd": "h_fs_default_cwd",
        "/api/files": "h_files",
        "/api/files/read": "h_files_read",
    }
    for path, handler in expected.items():
        route, _ = HC.match("GET", path)
        assert route is not None, path
        assert route["disposition"] == "implemented", (path, route)
        assert route["handler"] == handler, (path, route["handler"])
        assert HA.gated_path(path) is True, path
        assert HA.safe_to_proxy("GET", path) is False, path


def test_fs_read_outside_project_root_is_refused(tmp_path, monkeypatch):
    """The security-relevant edge: the once-proxied host reads now answer the
    Qubit confinement refusal, never the host bytes."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for path, query in (("/api/fs/read-text", {"path": "/etc/hostname"}),
                        ("/api/fs/list", {"path": "/"})):
        code, out = HC.dispatch(cfg, "GET", path, query, {})
        assert code == 400, (path, code, out)
        assert out.get("ok") is False, (path, out)
        assert out.get("code") == "FILES_OUTSIDE_ROOT", (path, out)
        assert out.get("qubit_counterpart"), (path, out)

    # /api/files with no registered project answers the real empty listing,
    # never a whole-filesystem enumeration.
    code, out = HC.dispatch(cfg, "GET", "/api/files", {}, {})
    assert code == 200, (code, out)
    assert out.get("entries") == [], out


# ----------------------------------------------------------------- A2 auth/config
def test_auth_prefix_variants_are_gated():
    from voicebridge import qubit_asgi as HA
    from voicebridge import qubit_compat as HC

    # Identity/auth variants and the raw-secret config surface: gated, so the
    # fork can never answer a login/identity or raw-secret read.
    for path, handler in (
        ("/auth/login", "h_auth_login"),
        ("/auth/callback", "h_auth_callback"),
        ("/auth/native/authorize", "h_auth_native_authorize"),
        ("/api/config/raw", "__gate__"),
    ):
        route, _ = HC.match("GET", path)
        assert route is not None and route["handler"] == handler, (path, route)
        assert HA.gated_path(path) is True, path
        assert HA.safe_to_proxy("GET", path) is False, path


def test_non_sensitive_config_reads_are_fork_served(monkeypatch):
    """The non-secret config readers are fork-declared reads: the corrected
    policy serves the fork's real document instead of Qubit's compat stub."""
    from voicebridge import qubit_asgi as HA
    from voicebridge import qubit_compat as HC

    monkeypatch.setattr(HA, "known_route", lambda _p: True)
    for path, handler in (
        ("/login", "h_login_page"),
        ("/api/config", "h_config"),
        ("/api/config/schema", "h_config_schema"),
        ("/api/config/defaults", "h_config_defaults"),
    ):
        route, _ = HC.match("GET", path)
        assert route is not None and route["handler"] == handler, (path, route)
        assert HA.gated_path(path) is False, path
        assert HA.safe_to_proxy("GET", path) is True, path


def test_config_read_serves_qubit_document(tmp_path, monkeypatch):
    """Flag-off/fallback: the Qubit compat handler still returns the Qubit
    config document. When the fork backend is on, ``/api/config`` is a
    fork-declared read and is served by the fork instead (the live test below
    proves the fork shape)."""
    from voicebridge import qubit_asgi as HA
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/config", {}, {})
    assert code == 200, (code, out)
    assert isinstance(out, dict) and "intent" in out and "webui" in out, out
    # The corrected policy does not gate this read, so the fork can serve it.
    assert HA.gated_path("/api/config") is False


# ------------------------------------------------------------ A3 read routing
def test_shadowed_get_routes_are_now_fork_served_unless_gated(monkeypatch):
    """The once-shadowed routes (audit table §1d) are NOT all Qubit-served:
    the corrected policy serves the fork's real data for non-sensitive reads
    and keeps only the sensitive/Qubit-owned ones on Qubit."""
    from voicebridge import qubit_asgi as HA
    from voicebridge import qubit_compat as HC

    monkeypatch.setattr(HA, "known_route", lambda _p: True)
    fork_served = (
        "/api/status", "/api/sessions", "/api/sessions/stats",
        "/api/profiles", "/api/logs", "/api/memory",
        "/api/config/schema", "/api/local-models/status",
        "/api/analytics/usage", "/api/learning/graph",
        "/api/egress/status", "/api/mcp/servers", "/api/cron/jobs",
        "/api/system/stats", "/api/curator", "/api/git/status",
        "/api/pairing", "/api/webhooks", "/api/messaging/platforms",
        "/api/skills", "/api/tools/toolsets", "/api/profiles/active",
    )
    for path in fork_served:
        route, _ = HC.match("GET", path)
        assert route is not None, path
        assert route["disposition"] == "implemented", (path, route)
        assert HA.gated_path(path) is False, path
        assert HA.safe_to_proxy("GET", path) is True, path

    # The confinement and Qubit-owned exceptions in the same audit set stay
    # gated, so the fork can never read the host filesystem or a Qubit surface.
    for path in ("/api/fs/default-cwd", "/api/qubit/update/check"):
        assert HA.gated_path(path) is True, path
        assert HA.safe_to_proxy("GET", path) is False, path


def test_gated_patterns_derive_from_the_route_table():
    """The no-proxy sets are derived from the Qubit route authority (``GATED``
    and implemented ``ROUTES``). Only ``GATED`` drives the decision; the
    implemented-route set is retained for telemetry and must NOT gate a
    fork-declared non-sensitive read."""
    from voicebridge import qubit_asgi as HA
    from voicebridge import qubit_compat as HC

    # Gated routes stay gated (regression guard for the derivation rewrite).
    assert HA.gated_path("/api/config/raw") is True
    assert HA.gated_path("/api/pty") is True
    # An unknown route is not gated; only the fork route table can allow it.
    assert HA.gated_path("/api/not-a-qubit-route") is False
    # The gate set has one compiled pattern per gated GET/HEAD row ...
    expected_gated = sum(1 for m, _p, _g in HC.GATED
                         if m.upper() in ("GET", "HEAD"))
    assert len(HA._gated_patterns()) == expected_gated
    # ... and the owned-read set one per implemented GET/HEAD route, but the
    # corrected policy does not consult it: a route it matches (e.g. /api/status)
    # is still fork-served.
    expected_owned = sum(
        1 for r in HC.ROUTES
        if r["method"].upper() in ("GET", "HEAD")
        and r["disposition"] == "implemented")
    assert len(HA._owned_read_patterns()) == expected_owned
    assert HA._matches(HA._owned_read_patterns(), "/api/status") is True
    assert HA.gated_path("/api/status") is False


def test_qubit_owned_prefixes_never_proxy():
    from voicebridge import qubit_asgi as HA

    for prefix in HA.QUBIT_OWNED_PREFIXES:
        assert HA.gated_path(prefix) is True, prefix
        assert HA.gated_path(prefix + "/child") is True, prefix
        assert HA.safe_to_proxy("GET", prefix) is False, prefix


def test_trailing_slash_cannot_slip_past_the_gate(monkeypatch):
    from voicebridge import qubit_asgi as HA

    monkeypatch.setattr(HA, "known_route", lambda _p: True)
    # A gated fs read stays gated with or without a trailing slash, so the
    # normalization cannot turn the confinement bypass back on ...
    assert HA.gated_path("/api/fs/read-text/") is True
    assert HA.safe_to_proxy("GET", "/api/fs/read-text/") is False
    # ... and a fork-served read stays fork-served after normalization too.
    assert HA.gated_path("/api/status/") is False
    assert HA.safe_to_proxy("GET", "/api/status/") is True


# ----------------------------------------------------------------- A29 update
def test_update_check_is_typed_read_not_a_blanket_403(tmp_path, monkeypatch):
    from voicebridge import qubit_asgi as HA
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    route, _ = HC.match("GET", "/api/qubit/update/check")
    assert route is not None and route["disposition"] == "implemented", route
    assert route["handler"] == "h_qubit_update_check", route

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/update/check", {}, {})
    assert code == 200, (code, out)
    for key in ("install_method", "current_version", "behind",
                "update_available", "can_apply", "update_command", "message"):
        assert key in out, (key, out)
    assert out["behind"] is None, out
    assert out["update_available"] is False, out
    assert out["can_apply"] is False, out
    assert out["current_version"], out

    # The read stays Qubit-owned (never proxied) ...
    assert HA.gated_path("/api/qubit/update/check") is True
    assert HA.safe_to_proxy("GET", "/api/qubit/update/check") is False
    # ... while the in-place self-update mutation is a real confirmed handler.
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/update", {}, {})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)


# -------------------------------------------------------------------- A11 ops
def test_ops_run_and_confirm_share_one_canonical_path(tmp_path, monkeypatch):
    """The classic webui surface and the SPA compat route go through the same
    helpers in qubit_compat, so the operation contract is implemented once."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    assert callable(getattr(HC, "ops_run_request"))
    assert callable(getattr(HC, "ops_confirm_request"))

    raw = HC.ops_run_request(cfg, {"id": "kanban_list", "args": {}})
    assert raw["state"] == "executed", raw
    code, envelope = HC.h_ops_run(cfg, {}, {"id": "kanban_list", "args": {}}, {})
    assert code == 200 and envelope["ok"] is True, (code, envelope)
    assert envelope["state"] == "executed", envelope

    # Unknown op is a typed envelope, never a fabricated success.
    code, out = HC.dispatch(cfg, "POST", "/api/ops/run", {},
                            {"id": "not-a-real-op"})
    assert out["code"] == "UNKNOWN_OP", out
    assert code == 400, (code, out)

    # Malformed bodies are typed consistently on both surfaces.
    assert HC.ops_run_request(cfg, {})["code"] == "MALFORMED"
    assert HC.ops_confirm_request(cfg, {})["code"] == "MALFORMED"
    code, out = HC.h_ops_run(cfg, {}, {}, {})
    assert code == 400 and out["code"] == "MALFORMED", (code, out)


# ------------------------------------------------------- live WebUI ingress
def _get(port, path, token="test-token"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    conn.request("GET", path,
                 headers={"X-Qubit-Session-Token": token})
    resp = conn.getresponse()
    out = (resp.status, resp.read())
    conn.close()
    return out


def test_live_agent_ingress_serves_fork_reads_and_stays_confined(tmp_path):
    """End-to-end over the real WebUI listener with the fork backend flag ON:
    fork-declared non-sensitive reads return the fork's real data (rebranded),
    while the confinement/secret gates and Qubit-owned surfaces stay on Qubit."""
    import time

    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\n"
                 f"webui:\n  token: test-token\n  qubit_backend: true\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "local"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    port = ui._server.server_address[1]
    try:
        # Status is the Qubit canonical status document, not the empty stub:
        # ``/api/status`` is on the QUBIT_READ_AUTHORITY exempt list so the SPA
        # sidebar reads the real Qubit gateway supervision (systemd + port
        # probe) rather than the fork's unsupervised null gateway fields.
        status, body = _get(port, "/agent/api/status")
        assert status == 200, (status, body[:200])
        out = json.loads(body)
        assert out.get("schema") == "qubit-runtime-status/1", out
        assert out.get("version") and out.get("release_date"), out
        assert out.get("model"), out
        assert "gateway_running" in out and "gateway_state" in out, out

        # Env is the fork's real environment document: a non-empty dict.
        status, body = _get(port, "/agent/api/env")
        assert status == 200, (status, body[:200])
        env = json.loads(body)
        assert isinstance(env, dict) and env, env

        # Host filesystem read is refused by the Qubit confinement.
        status, body = _get(
            port, "/agent/api/fs/read-text?path=/etc/hostname")
        assert status == 400, (status, body[:200])
        out = json.loads(body)
        assert out.get("code") == "FILES_OUTSIDE_ROOT", out

        # The fork's config document IS served (non-sensitive read), not the
        # Qubit compat stub's Qubit-specific document.
        status, body = _get(port, "/agent/api/config")
        assert status == 200, (status, body[:200])
        out = json.loads(body)
        assert "model" in out and "providers" in out and "toolsets" in out, out
        assert "intent" not in out and "webui" not in out, out

        # A secret surface stays gated even though the fork declares it.
        status, body = _get(port, "/agent/api/config/raw")
        assert status == 403, (status, body[:200])
        assert json.loads(body).get("code") == "SECRET_NEVER_SERVED", body[:200]

        # The update check is a typed read, not the blanket 403.
        status, body = _get(port, "/agent/api/qubit/update/check")
        assert status == 200, (status, body[:200])
        assert "update_available" in json.loads(body), body[:200]
    finally:
        ui.stop()
