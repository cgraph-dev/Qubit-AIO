"""9U — dashboard backend closure: real backends for priority OPEN controls.

Sole-owner evidence for the dashboard-backend workstream. Each priority control
is driven through the real canonical dispatcher
(``voicebridge.qubit_compat.dispatch``) against a hermetic ``Config`` and
asserted on a persisted read-back:

* **curator pause/run** — persisted ``curator_control.json``; a paused run
  refuses with ``CURATOR_PAUSED``; a run records ``last_run_at``/``run_count``;
* **memory reset** — canonical memory-store tombstone (MEMORY.md analog) and
  markdown memory-workspace forget (USER.md analog), returning the real
  deleted ids;
* **hooks create/list/update/delete** — the canonical hooks registry, staged
  through the shared journal and settleable by id;
* **config key read/write** — redacted read (no raw secret leaves) and a
  confirm-gated persisted write;
* **keys reference add/update/remove** — the vault-reference registry, refs
  only, never a raw secret.

Every mutating path is also asserted to refuse under the shared e-stop latch.
These are hermetic backend-contract proofs only: no real provider, model,
network, credential, hardware, or browser E2E is exercised, and no
``IMPLEMENTED``/``VERIFIED``/100 %/1:1 claim is made.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.unit


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / "qh"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nwebui:\n  token: closure-token\n")
    return C.Config.load(p)


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    return _cfg(tmp_path, monkeypatch)


def _dispatch(cfg, method, path, body=None, query=None):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, method, path, query or {}, body or {})


def _estop(cfg, latched):
    from voicebridge.power import power_control_store

    power_control_store(cfg).set(emergency_stop=bool(latched),
                                 global_disable=False, issued_by="cli:user")


# ---------------------------------------------------------------------------
# Curator pause / run
# ---------------------------------------------------------------------------
def test_curator_pause_persists_and_run_refuses_while_paused(cfg):
    code, out = _dispatch(cfg, "GET", "/api/curator")
    assert code == 200 and out["paused"] is False and out["run_count"] == 0, out

    code, out = _dispatch(cfg, "PUT", "/api/curator/paused", {"paused": True})
    assert code == 200 and out["ok"] is True and out["paused"] is True, out

    # Persisted: a fresh read shows the flag.
    code, out = _dispatch(cfg, "GET", "/api/curator")
    assert code == 200 and out["paused"] is True, out

    # A paused run is a typed refusal, never a fabricated pass.
    code, out = _dispatch(cfg, "POST", "/api/curator/run")
    assert code == 409 and out["code"] == "CURATOR_PAUSED", out

    # Persisted on disk, not just in the response.
    state = json.loads((Path(cfg.state_dir()) / "curator_control.json").read_text())
    assert state["paused"] is True, state

    # Resume and run a real pass.
    code, out = _dispatch(cfg, "PUT", "/api/curator/paused", {"paused": False})
    assert code == 200 and out["paused"] is False, out
    code, out = _dispatch(cfg, "POST", "/api/curator/run")
    assert code == 200 and out["ok"] is True, out
    assert out["state"] == "executed" and out["run_count"] == 1, out
    assert out["last_run_at"], out
    assert out["summary"].get("curator") == "QUBIT-OWNED", out

    code, out = _dispatch(cfg, "GET", "/api/curator")
    assert out["run_count"] == 1 and out["last_run_at"], out


def test_curator_pause_requires_boolean_field(cfg):
    code, out = _dispatch(cfg, "PUT", "/api/curator/paused", {})
    assert code == 400 and out["code"] == "MALFORMED", out


def test_curator_run_is_audited(cfg, monkeypatch):
    from voicebridge import logging_setup as LS

    captured = []
    monkeypatch.setattr(LS, "log_event",
                        lambda event, **f: captured.append((event, f)))
    code, _ = _dispatch(cfg, "POST", "/api/curator/run")
    assert code == 200, code
    assert any(e == "qubit_compat.curator_run" for e, _ in captured), captured


def test_curator_mutations_refuse_when_estop_latched(cfg):
    _estop(cfg, True)
    code, out = _dispatch(cfg, "PUT", "/api/curator/paused", {"paused": True})
    assert code == 403 and out["code"] == "EMERGENCY_STOP", out
    code, out = _dispatch(cfg, "POST", "/api/curator/run")
    assert code == 403 and out["code"] == "EMERGENCY_STOP", out


# ---------------------------------------------------------------------------
# Memory reset
# ---------------------------------------------------------------------------
def _seed_memory(cfg):
    from voicebridge import active_memory as AM
    from voicebridge.memory import MemoryStore, workspace as WS

    store = MemoryStore(AM.memory_db_path(cfg))
    try:
        store.remember("the launch codename is orion")
        store.remember("the staging host is nebula")
    finally:
        store.close()
    WS.write_note(cfg, "profile", "prefers concise answers", tags=["user"],
                  requested_by="t", confirmed=True)


def test_memory_reset_stages_settles_and_deletes_store(cfg):
    from voicebridge import active_memory as AM
    from voicebridge.memory import MemoryStore

    _seed_memory(cfg)
    db = AM.memory_db_path(cfg)
    store = MemoryStore(db)
    assert len(store.export()) == 2, "seed failed"
    store.close()

    # No confirm: a real, settleable journal id (never an empty id).
    code, staged = _dispatch(cfg, "POST", "/api/memory/reset", {"target": "memory"})
    assert code == 409 and staged["code"] == "CONFIRM_REQUIRED", staged
    assert staged.get("confirmation_id"), staged

    # Settle by re-issuing with the id.
    code, out = _dispatch(cfg, "POST", "/api/memory/reset",
                          {"target": "memory",
                           "confirmation_id": staged["confirmation_id"]})
    assert code == 200 and out["ok"] is True, out
    assert out["deleted"] and all(d.startswith("memory#") for d in out["deleted"]), out

    store = MemoryStore(db)
    try:
        assert store.export() == [], "memory store records survived the reset"
    finally:
        store.close()


def test_memory_reset_user_target_forgets_workspace_notes(cfg):
    from voicebridge.memory import workspace as WS

    _seed_memory(cfg)
    assert WS.list_notes(cfg)["count"] == 1
    code, out = _dispatch(cfg, "POST", "/api/memory/reset",
                          {"target": "user", "confirm": True})
    assert code == 200 and out["deleted"] == ["profile"], out
    assert WS.list_notes(cfg)["count"] == 0


def test_memory_reset_settles_via_pending_approvals_banner(cfg):
    from voicebridge import dashboard_ops as OPS
    from voicebridge.memory import workspace as WS

    _seed_memory(cfg)
    code, staged = _dispatch(cfg, "POST", "/api/memory/reset", {"target": "user"})
    assert code == 409 and staged.get("confirmation_id"), staged
    # The id-only Confirm path the pending-approvals banner uses.
    out = OPS.confirm_op(cfg, staged["confirmation_id"], actor="dashboard:user",
                         session="dashboard")
    assert out["state"] == "executed", out
    assert WS.list_notes(cfg)["count"] == 0


def test_memory_reset_rejects_unknown_target_and_refuses_on_estop(cfg):
    code, out = _dispatch(cfg, "POST", "/api/memory/reset",
                          {"target": "galaxy", "confirm": True})
    assert code == 400 and out["code"] == "MALFORMED", out
    _estop(cfg, True)
    code, out = _dispatch(cfg, "POST", "/api/memory/reset",
                          {"target": "all", "confirm": True})
    assert code == 403 and out["code"] == "EMERGENCY_STOP", out


# ---------------------------------------------------------------------------
# Hooks CRUD
# ---------------------------------------------------------------------------
def test_hooks_create_list_update_delete_roundtrip(cfg):
    from voicebridge import hooks as H

    # Create (confirmed) persists to the canonical registry.
    code, out = _dispatch(cfg, "POST", "/api/ops/hooks",
                          {"event": "task.completed", "command": "echo hi",
                           "confirm": True})
    assert code == 200 and out["ok"] is True, out
    names = {r["name"] for r in H.list_hooks(cfg)["hooks"]}
    assert "task-completed" in names, names

    # Read through the compat wire shape.
    code, out = _dispatch(cfg, "GET", "/api/ops/hooks")
    assert code == 200 and any(h["event"] == "task.completed"
                               for h in out["hooks"]), out

    # Update (remove + re-declare) changes the event and persists.
    code, out = _dispatch(cfg, "PUT", "/api/ops/hooks",
                          {"name": "task-completed", "event": "task.failed",
                           "command": "echo bye", "confirm": True})
    assert code == 200 and out["ok"] is True, out
    rec = next(r for r in H.list_hooks(cfg)["hooks"] if r["name"] == "task-completed")
    assert rec["event"] == "task.failed", rec

    # Delete (confirmed) removes it.
    code, out = _dispatch(cfg, "DELETE", "/api/ops/hooks",
                          {"name": "task-completed", "confirm": True})
    assert code == 200 and out["ok"] is True, out
    assert H.list_hooks(cfg)["hooks"] == []


def test_hooks_create_stages_a_settleable_confirmation(cfg):
    from voicebridge import hooks as H

    code, staged = _dispatch(cfg, "POST", "/api/ops/hooks",
                             {"event": "task.completed", "command": "echo hi"})
    assert code == 409 and staged["code"] == "CONFIRM_REQUIRED", staged
    assert staged.get("confirmation_id"), staged
    assert H.list_hooks(cfg)["hooks"] == [], "staging wrote the registry"

    code, done = _dispatch(cfg, "POST", "/api/ops/hooks",
                           {"event": "task.completed", "command": "echo hi",
                            "confirmation_id": staged["confirmation_id"]})
    assert code == 200 and done["ok"] is True, done
    assert {r["name"] for r in H.list_hooks(cfg)["hooks"]} == {"task-completed"}


def test_hooks_mutations_refuse_when_estop_latched(cfg):
    _estop(cfg, True)
    code, out = _dispatch(cfg, "POST", "/api/ops/hooks",
                          {"event": "task.completed", "command": "echo hi",
                           "confirm": True})
    assert code == 403 and out["code"] == "EMERGENCY_STOP", out
    code, out = _dispatch(cfg, "DELETE", "/api/ops/hooks",
                          {"name": "task-completed", "confirm": True})
    assert code == 403 and out["code"] == "EMERGENCY_STOP", out
    code, out = _dispatch(cfg, "PUT", "/api/ops/hooks",
                          {"name": "task-completed", "event": "task.failed",
                           "confirm": True})
    assert code == 403 and out["code"] == "EMERGENCY_STOP", out


# ---------------------------------------------------------------------------
# Config key read/write (redacted)
# ---------------------------------------------------------------------------
def test_config_read_redacts_secret_leaves(cfg):
    code, out = _dispatch(cfg, "GET", "/api/config")
    assert code == 200, out
    assert "closure-token" not in json.dumps(out), "raw secret leaked from config"
    assert out["webui"]["token"] == "[REDACTED]", out["webui"]


def test_config_write_persists_and_refuses_secret_values(cfg):
    code, out = _dispatch(cfg, "PUT", "/api/config",
                          {"config": {"voice": {"input_engine": "local"}},
                           "confirm": True})
    assert code == 200 and out["ok"] is True, out
    code, out = _dispatch(cfg, "GET", "/api/config")
    assert out["voice"]["input_engine"] == "local", out["voice"]

    # A newly provided secret-shaped leaf is refused, never persisted.
    code, out = _dispatch(cfg, "PUT", "/api/config",
                          {"config": {"webui": {"token": "brand-new-secret"}},
                           "confirm": True})
    assert code == 403 and out["code"] == "SECRET_WRITE_REFUSED", out
    code, out = _dispatch(cfg, "GET", "/api/config")
    assert "brand-new-secret" not in json.dumps(out), out


def test_config_write_stages_and_refuses_on_estop(cfg):
    code, out = _dispatch(cfg, "PUT", "/api/config",
                          {"config": {"voice": {"input_engine": "local"}}})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", out
    assert out.get("confirmation_id"), out
    _estop(cfg, True)
    code, out = _dispatch(cfg, "PUT", "/api/config",
                          {"config": {"voice": {"input_engine": "local"}},
                           "confirm": True})
    assert code == 403 and out["code"] == "EMERGENCY_STOP", out


# ---------------------------------------------------------------------------
# Keys reference add / update / remove (refs only)
# ---------------------------------------------------------------------------
def test_env_reference_add_update_remove(cfg):
    from voicebridge import config as C

    env_path = Path(C.canonical_runtime_env_path())
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("CLOSURE_REF_TOKEN=present\n")

    # ``is_set`` means the credential actually resolves (UI audit D1), so the
    # add round-trips to True only for a resolvable ``env:`` target.
    code, out = _dispatch(cfg, "PUT", "/api/env",
                          {"name": "CLOSURE_REF",
                           "value": "env:CLOSURE_REF_TOKEN",
                           "confirm": True})
    assert code == 200 and out["ok"] is True \
        and out["value"] == "env:CLOSURE_REF_TOKEN", out
    code, out = _dispatch(cfg, "GET", "/api/env")
    assert out["CLOSURE_REF"]["is_set"] is True, out.get("CLOSURE_REF")

    # Update: same key, a reference with no resolvable target -> honest False.
    code, out = _dispatch(cfg, "PUT", "/api/env",
                          {"name": "CLOSURE_REF",
                           "value": "env:CLOSURE_REF_MISSING",
                           "confirm": True})
    assert code == 200 and out["value"] == "env:CLOSURE_REF_MISSING", out
    code, out = _dispatch(cfg, "GET", "/api/env")
    assert out["CLOSURE_REF"]["is_set"] is False, out.get("CLOSURE_REF")
    assert "env:CLOSURE_REF_MISSING" in out["CLOSURE_REF"]["description"], \
        out["CLOSURE_REF"]

    # A ``vault:`` reference has no Qubit-side resolver: registered, not set.
    code, out = _dispatch(cfg, "PUT", "/api/env",
                          {"name": "CLOSURE_REF", "value": "vault:alpha",
                           "confirm": True})
    assert code == 200 and out["value"] == "vault:alpha", out
    code, out = _dispatch(cfg, "GET", "/api/env")
    assert out["CLOSURE_REF"]["is_set"] is False, out.get("CLOSURE_REF")

    # Remove.
    code, out = _dispatch(cfg, "DELETE", "/api/env",
                          {"key": "CLOSURE_REF", "confirm": True})
    assert code == 200 and out["removed"] is True, out
    code, out = _dispatch(cfg, "GET", "/api/env")
    assert "CLOSURE_REF" not in out, out


def test_env_reference_rejects_raw_secret_and_estop(cfg):
    code, out = _dispatch(cfg, "PUT", "/api/env",
                          {"name": "RAW", "value": "sk-live-rawsecret",
                           "confirm": True})
    assert code == 403 and out["code"] == "SECRET_WRITE_REFUSED", out
    _estop(cfg, True)
    code, out = _dispatch(cfg, "PUT", "/api/env",
                          {"name": "CLOSURE_REF", "value": "vault:alpha",
                           "confirm": True})
    assert code == 403 and out["code"] == "EMERGENCY_STOP", out
    code, out = _dispatch(cfg, "DELETE", "/api/env",
                          {"key": "CLOSURE_REF", "confirm": True})
    assert code == 403 and out["code"] == "EMERGENCY_STOP", out
