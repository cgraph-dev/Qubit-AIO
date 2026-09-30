"""Wave 13 P0 route parity + generator-bug regressions.

Proves, through the production compat dispatcher:

* ``GET /api/gateway/migrate/plan`` is a real read-only route returning the
  vendored ``MigrationPlan.to_dict()`` (reference actions.py:147);
* ``GET /api/host/identity`` reports this process owns the port
  (reference status.py:122);
* ``GET /api/providers/oauth`` returns the redacted provider catalog and NEVER
  a secret (``token_preview`` and every token/secret-shaped key are stripped);
* ``POST /api/gateway/migrate`` is a typed ``GATEWAY_MIGRATE_OPERATOR_OWNED``
  refusal naming the CLI counterpart, never a migration;
* ``GET /api/providers/oauth/{provider_id}/poll/{session_id}`` is a typed
  ``OAUTH_MANUAL_HANDOFF`` refusal naming ``qubit auth login``.

Also pins the two parity-inventory generator fixes:

* route classification resolves through the real ``qubit_compat.match`` (a
  registered route is never MISSING);
* skill enumeration requires a ``SKILL.md`` manifest, so an empty namespace
  directory (the reference's ``skills/product/``) is not counted.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ------------------------------------------------------------------ route table

def test_p0_routes_are_registered_with_expected_disposition():
    from voicebridge import qubit_compat as HC

    expected = {
        ("GET", "/api/gateway/migrate/plan"): "implemented",
        ("GET", "/api/host/identity"): "implemented",
        ("GET", "/api/providers/oauth"): "implemented",
        ("POST", "/api/gateway/migrate"): "gated",
    }
    for (method, path), disp in expected.items():
        route, _ = HC.match(method, path)
        assert route is not None, f"{method} {path} not registered"
        assert route["disposition"] == disp, (method, path, route)
    poll, params = HC.match(
        "GET", "/api/providers/oauth/nous/poll/abc123")
    assert poll is not None and poll["disposition"] == "gated"
    assert params == {"provider_id": "nous", "session_id": "abc123"}


# --------------------------------------------------------- implemented routes

def test_gateway_migrate_plan_is_real_read_only(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/gateway/migrate/plan", {}, {})
    assert code == 200 and out["ok"] is True
    # The vendored MigrationPlan.to_dict() shape.
    assert isinstance(out["eligible"], bool)
    assert out["command"] == "qubit gateway migrate"
    assert isinstance(out["profiles"], list)
    assert "blockers" in out and "notices" in out


def test_host_identity_reports_this_process(tmp_path, monkeypatch):
    import os

    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/host/identity", {}, {})
    assert code == 200 and out["ok"] is True
    assert out["protocolVersion"] == 1
    assert out["pid"] == os.getpid()
    assert out["role"] == "serve"
    assert out["servesSpa"] is True


def test_providers_oauth_is_redacted_and_shaped(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/providers/oauth", {}, {})
    assert code == 200 and out["ok"] is True
    providers = out["providers"]
    assert providers, "the OAuth catalog must not be empty"
    for p in providers:
        assert {"id", "name", "flow", "status"} <= set(p), p

    # Redaction policy: no token_preview key anywhere, no token/secret-valued
    # field, and no legacy portal host on this served product surface. The
    # non-secret boolean ``has_refresh_token`` is allowed to survive.
    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert str(k).lower() != "token_preview", (k, obj)
                assert str(k).lower() not in (
                    "access_token", "refresh_token", "id_token", "api_key",
                    "client_secret", "password", "private_key"), (k, obj)
                yield from _walk(v)
        elif isinstance(obj, list):
            for x in obj:
                yield from _walk(x)
        elif isinstance(obj, str):
            yield obj

    for value in _walk(out):
        assert "nousresearch.com" not in value.lower(), value


# ---------------------------------------------------------------- typed gates

def test_gateway_migrate_post_is_operator_owned_refusal(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    route, _ = HC.match("POST", "/api/gateway/migrate")
    assert route["handler"] == "__gate__" and route["disposition"] == "gated"
    code, out = HC.dispatch(cfg, "POST", "/api/gateway/migrate", {}, {})
    assert code == 403
    assert out["code"] == "GATEWAY_MIGRATE_OPERATOR_OWNED"
    assert "qubit gateway migrate" in out["qubit_counterpart"]


def test_oauth_poll_is_manual_handoff_refusal(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "GET", "/api/providers/oauth/nous/poll/abc123", {}, {})
    assert code == 403
    assert out["code"] == "OAUTH_MANUAL_HANDOFF"
    assert "qubit auth login" in out["qubit_counterpart"]


# ------------------------------------------------- generator-bug regressions

def test_probe_normalizes_like_the_runtime_dispatcher():
    from scripts import parity_inventory as pi

    assert pi._probe("/api/dashboard/plugins/catalog") == \
        "/api/dashboard/plugins/catalog"
    assert pi._probe("/api/x/") == "/api/x"
    assert pi._probe("/api/x/?a=1") == "/api/x"
    assert pi._probe("/api/sessions/{session_id}/timeline") == \
        "/api/sessions/_p_/timeline"


def test_generator_classifies_registered_catalog_route_as_ported():
    from scripts import parity_inventory as pi
    from voicebridge import qubit_compat as qc

    rows = pi.build_routes(qc)
    catalog = next(r for r in rows
                   if r["id"] == "GET /api/dashboard/plugins/catalog")
    assert catalog["classification"] != "MISSING", catalog
    assert catalog["classification"] == "PORTED"


def test_oauth_catalog_stays_on_the_qubit_path_not_the_fork_proxy():
    """The fork declares GET /api/providers/oauth, so without an authority-table
    row the proxy-first read would serve the fork's raw record (legacy portal
    host + a token fragment). It must be no-proxy so the compat handler wins."""
    from voicebridge import qubit_asgi as HA

    assert HA.gated_path("/api/providers/oauth") is True
    assert HA.safe_to_proxy("GET", "/api/providers/oauth") is False


def test_skill_enumeration_requires_a_manifest(tmp_path):
    from scripts import parity_inventory as pi

    (tmp_path / "product").mkdir()  # empty namespace dir (no SKILL.md)
    real = tmp_path / "productivity"
    real.mkdir()
    (real / "SKILL.md").write_text("---\nname: x\n---\nbody\n")
    assert pi._skill_dir_has_manifest(tmp_path / "product") is False
    assert pi._skill_dir_has_manifest(real) is True
    names = {p.name for p in pi._skill_dirs(tmp_path)}
    assert names == {"productivity"}, names
