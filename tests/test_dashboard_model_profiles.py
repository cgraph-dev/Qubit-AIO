"""Qubit Models & Providers — resolver-backed dashboard ops + overlay page.

Proves the deliverable against the real backend: the thin typed wrappers in
``voicebridge.model_profiles_ops`` (over the canonical
``voicebridge.providers.resolver``), their registration through the existing
dashboard op-dispatch (``dashboard_ops.run_op`` -> the shared confirmation
journal), and the real ``qubit-models`` overlay plugin (manifest + entry that
call those ops). No mocks of the surfaces themselves; a status tile would not
pass these assertions.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

PLUGIN = REPO_ROOT / "frontend" / "qubit-app" / "plugins" / "qubit-models"
ENTRY = PLUGIN / "qubit-models.js"
MANIFEST = PLUGIN / "manifest.json"

OP_IDS = (
    "list_model_profiles", "show_model_profile", "create_model_profile",
    "clone_model_profile", "remove_model_profile", "test_model_profile",
    "select_session_model", "clear_session_model", "rollback_model_profile",
)


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _ops():
    from voicebridge import model_profiles_ops as MP

    return MP


# ---------------------------------------------------------------------------
# Ops-level: resolver-backed, typed, secret-ref only
# ---------------------------------------------------------------------------
def test_list_returns_shipped_default(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _ops().list_model_profiles(cfg)
    assert out["state"] == "OK"
    ids = {p["id"] for p in out["profiles"]}
    assert _ops().DEFAULT_LOCAL_ID in ids, ids
    assert out["count"] == len(out["profiles"])


def test_create_with_secret_ref_persists_and_shows(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _ops().create_model_profile(
        cfg, profile_id="research-api", kind="api", provider="openai",
        model="gpt-research", base_url="https://api.example/v1",
        key_ref="env:RESEARCH_KEY", roles="chat,hard", confirm=True)
    assert out["state"] == "OK" and out["action"] == "persisted", out
    listed = {p["id"]: p for p in _ops().list_model_profiles(cfg)["profiles"]}
    assert "research-api" in listed
    assert listed["research-api"]["has_secret_ref"] is True
    assert listed["research-api"]["locality"] == "external"
    shown = _ops().show_model_profile(cfg, "research-api")
    assert shown["state"] in ("OK", "DEGRADED")
    assert shown["profile"]["key_ref"] == "env:RESEARCH_KEY"
    # dry run: nothing is written without confirm
    dry = _ops().create_model_profile(
        cfg, profile_id="dry-run-api", kind="api", provider="openai",
        model="m", base_url="https://api.example/v1",
        key_ref="env:X", confirm=False)
    assert dry["dry_run"] is True
    assert "dry-run-api" not in {
        p["id"] for p in _ops().list_model_profiles(cfg)["profiles"]}


def test_raw_secret_is_refused_and_never_persisted(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    raw = "sk-" + "a" * 24
    bad = _ops().create_model_profile(
        cfg, profile_id="raw-key", kind="api", provider="openai", model="m",
        base_url="https://api.example/v1", key_ref=raw, confirm=True)
    assert bad["state"] == "REFUSED" and bad["code"] == "RAW_SECRET", bad
    assert raw not in json.dumps(bad)
    bad2 = _ops().create_model_profile(
        cfg, profile_id="not-a-ref", kind="api", provider="openai", model="m",
        base_url="https://api.example/v1", key_ref="plainvalue", confirm=True)
    assert bad2["state"] == "REFUSED" and bad2["code"] == "BAD_SECRET_REF", bad2
    ids = {p["id"] for p in _ops().list_model_profiles(cfg)["profiles"]}
    assert "raw-key" not in ids and "not-a-ref" not in ids


def test_session_select_and_clear_override(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    clone = _ops().clone_model_profile(cfg, "qubit-brain4", "local-copy",
                                       confirm=True)
    assert clone["state"] == "OK", clone
    sel = _ops().select_session_model(cfg, "s1", "local-copy", confirm=True)
    assert sel["state"] == "OK" and sel["action"] == "selected", sel
    assert R.resolve(cfg, session="s1").profile_id == "local-copy"
    cleared = _ops().clear_session_model(cfg, "s1", role="chat", confirm=True)
    assert cleared["state"] == "OK" and cleared["changed"] is True, cleared
    assert R.resolve(cfg, session="s1").profile_id == _ops().DEFAULT_LOCAL_ID


def test_test_profile_typed_states(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    unknown = _ops().test_model_profile(cfg, "nope")
    assert unknown["state"] == "FAILED" and unknown["code"] == "UNKNOWN_PROFILE"
    ok = _ops().test_model_profile(cfg, _ops().DEFAULT_LOCAL_ID)
    assert ok["state"] == "OK", ok
    _ops().create_model_profile(
        cfg, profile_id="egress-denied", kind="api", provider="openai",
        model="m", base_url="https://api.example/v1", key_ref="env:X",
        confirm=True)
    degraded = _ops().test_model_profile(cfg, "egress-denied")
    assert degraded["state"] == "DEGRADED", degraded
    assert degraded["effective"]["executable"] is False


def test_remove_in_use_refused_then_allowed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _ops().clone_model_profile(cfg, "qubit-brain4", "remove-me", confirm=True)
    _ops().select_session_model(cfg, "s2", "remove-me", confirm=True)
    refused = _ops().remove_model_profile(cfg, "remove-me", confirm=True)
    assert refused["state"] == "REFUSED"
    assert refused["code"] == "PROFILE_IN_USE", refused
    _ops().clear_session_model(cfg, "s2", role="chat", confirm=True)
    gone = _ops().remove_model_profile(cfg, "remove-me", confirm=True)
    assert gone["state"] == "OK" and gone["action"] == "removed", gone
    default = _ops().remove_model_profile(cfg, _ops().DEFAULT_LOCAL_ID,
                                          confirm=True)
    assert default["state"] == "REFUSED"
    assert default["code"] == "IMMUTABLE_DEFAULT", default


def test_rollback_selects_declared_target(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    _ops().clone_model_profile(cfg, "qubit-brain4", "rollback-child",
                               confirm=True)
    _ops().select_session_model(cfg, "s3", "rollback-child", confirm=True)
    back = _ops().rollback_model_profile(cfg, "rollback-child", session="s3",
                                         confirm=True)
    assert back["state"] == "OK" and back["rollback_to"] == "qubit-brain4"
    assert R.resolve(cfg, session="s3").profile_id == "qubit-brain4"
    none = _ops().rollback_model_profile(cfg, _ops().DEFAULT_LOCAL_ID,
                                         session="s3", confirm=True)
    assert none["state"] == "REFUSED" and none["code"] == "NO_ROLLBACK", none


# ---------------------------------------------------------------------------
# Dashboard op-dispatch: same functions, shared confirmation journal
# ---------------------------------------------------------------------------
def _dash(cfg, op_id, args=None):
    from voicebridge import dashboard_ops as OPS

    return OPS.run_op(cfg, op_id, dict(args or {}), actor="dashboard:user",
                      session="dashboard")


def test_dashboard_dispatch_reads_and_stages_mutations(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    listed = _dash(cfg, "list_model_profiles")
    assert listed["state"] == "executed", listed
    assert listed["receipt"]["result"]["count"] >= 1
    staged = _dash(cfg, "create_model_profile", {
        "profile_id": "via-dashboard", "kind": "api", "provider": "openai",
        "model": "m", "base_url": "https://api.example/v1",
        "key_ref": "env:DASH_KEY"})
    assert staged["state"] == "needs-confirm", staged
    assert staged.get("sensitive_required") == ["key_ref"]
    journal = (Path(cfg.state_dir()) / "approvals.jsonl").read_text()
    assert "DASH_KEY" not in journal
    # Confirm without the ref fails closed; nothing was written.
    need = OPS.confirm_op(cfg, staged["confirmation_id"])
    assert need["state"] == "failed" and need["code"] == "SENSITIVE_REQUIRED"
    assert "via-dashboard" not in {
        p["id"] for p in _ops().list_model_profiles(cfg)["profiles"]}
    done = OPS.confirm_op(cfg, staged["confirmation_id"],
                          sensitive={"key_ref": "env:DASH_KEY"})
    assert done["state"] == "executed", done
    assert done["receipt"]["result"]["state"] == "OK", done
    assert "via-dashboard" in {
        p["id"] for p in _ops().list_model_profiles(cfg)["profiles"]}


def test_dashboard_raw_secret_is_refused_at_confirm(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    raw = "sk-" + "b" * 24
    staged = _dash(cfg, "create_model_profile", {
        "profile_id": "dash-raw", "kind": "api", "provider": "openai",
        "model": "m", "base_url": "https://api.example/v1", "key_ref": raw})
    assert staged["state"] == "needs-confirm", staged
    done = OPS.confirm_op(cfg, staged["confirmation_id"],
                          sensitive={"key_ref": raw})
    result = done["receipt"]["result"]
    assert result["state"] == "REFUSED" and result["code"] == "RAW_SECRET"
    assert raw not in json.dumps(done)
    assert "dash-raw" not in {
        p["id"] for p in _ops().list_model_profiles(cfg)["profiles"]}


# ---------------------------------------------------------------------------
# Static overlay-plugin checks (real page, real ops)
# ---------------------------------------------------------------------------
def test_plugin_manifest_and_entry_are_real():
    assert MANIFEST.is_file() and ENTRY.is_file()
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert man["name"] == "qubit-models"
    assert man["tab"]["path"] == "/qubit-models"
    assert man["entry"] == "qubit-models.js"
    assert man.get("css") is None
    assert man.get("has_api") is True
    text = ENTRY.read_text(encoding="utf-8")
    assert 'register("qubit-models"' in text
    assert "/api/ops/run" in text and "/api/ops/confirm" in text
    for op_id in OP_IDS:
        assert op_id in text, op_id


def test_plugin_is_registered_by_the_backend_overlay_loader():
    from voicebridge import qubit_compat as HC

    names = {m["name"] for m in HC._overlay_manifests()}
    assert "qubit-models" in names, sorted(names)


def test_plugin_has_no_upstream_product_name():
    import re

    regex = re.compile(r"\bHermes\b|\bOpenClaw\b")
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    blob = f'{man.get("label", "")} {man.get("description", "")}'
    assert not regex.search(blob), blob


def test_models_plugin_confirm_renders_typed_refusal():
    """The confirm path must render a nested receipt.result typed state, not a
    false 'Confirmed: done' (V3 fix)."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] /
          "frontend/qubit-app/plugins/qubit-models/qubit-models.js"
          ).read_text(encoding="utf-8")
    assert "res.receipt && res.receipt.result" in js, js[:400]


def test_every_model_profile_op_dispatches_through_the_production_route(
        tmp_path, monkeypatch):
    """Each model-profile op id must reach the real dashboard dispatcher.

    Production-caller proof for every model-profile op row: run_op ->
    MODEL_PROFILE_OPS -> model_profiles_ops. Reads execute; mutations stage.
    """
    import voicebridge.dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    assert OPS.run_op(cfg, "list_model_profiles", {})["state"] == "executed"
    assert OPS.run_op(cfg, "show_model_profile",
                      {"profile_id": "qubit-brain4"})["state"] == "executed"
    assert OPS.run_op(cfg, "test_model_profile",
                      {"profile_id": "qubit-brain4"})["state"] == "executed"
    assert OPS.run_op(
        cfg, "create_model_profile",
        {"profile_id": "proof-local", "kind": "local",
         "provider": "ollama", "model": "phi4"})["state"] == "needs-confirm"
    assert OPS.run_op(
        cfg, "clone_model_profile",
        {"source_id": "qubit-brain4",
         "new_id": "proof-clone"})["state"] == "needs-confirm"
    assert OPS.run_op(
        cfg, "select_session_model",
        {"session": "s1",
         "profile_id": "qubit-brain4"})["state"] == "needs-confirm"
    assert OPS.run_op(
        cfg, "clear_session_model",
        {"session": "s1"})["state"] == "needs-confirm"
    assert OPS.run_op(
        cfg, "rollback_model_profile",
        {"profile_id": "qubit-brain4",
         "session": "s1"})["state"] == "needs-confirm"
    assert OPS.run_op(
        cfg, "remove_model_profile",
        {"profile_id": "proof-clone"})["state"] == "needs-confirm"
