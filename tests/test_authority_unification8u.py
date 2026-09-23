"""8U authority unification — one writable authority per concern.

Covers the S3 authority wave:

* A7  confirmation authority: ``CapabilityBroker``, ``dispatch_guard`` and the
      fork-forward guard all default to the durable
      ``PersistentConfirmationStore`` (``actions.json``) so a confirmation
      minted in one process/broker is consumable by the next; the guard's
      internal auto-issue stays in-memory (never a second writable store).
* A8  one fork home: ``qubit_plugins`` resolves the same
      ``qubit_runtime.default_home`` and legacy homes converge copy-only.
* A9  one board authority: the vendored store is read-only and its in-process
      write boundary has a real production caller.
* A12 one gateway listener: the programmatic second listener is inert.
* A14 catalog ``cli`` claims are truthful (no hidden CLI_PENDING gaps).
* A15 scheduled/automation dispatch routes through the operation contract.

Hermetic: isolated XDG dirs, no network, no live services.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ---------------------------------------------------------------------------
# A7 — ONE confirmation authority
# ---------------------------------------------------------------------------
def test_broker_defaults_to_the_persistent_confirmation_store(tmp_path,
                                                              monkeypatch):
    from voicebridge.actions import PersistentConfirmationStore
    from voicebridge.broker import build_host_broker

    cfg = _cfg(tmp_path, monkeypatch)
    broker = build_host_broker(cfg=cfg)
    assert isinstance(broker.engine.confirmations,
                      PersistentConfirmationStore)


def test_confirmation_minted_by_one_broker_is_consumed_by_another(
        tmp_path, monkeypatch):
    """The cross-process gap: a fresh broker honours the durable record."""
    from voicebridge.broker import build_host_broker
    from voicebridge.capabilities import ActionRequest

    cfg = _cfg(tmp_path, monkeypatch)
    mint = build_host_broker(cfg=cfg)
    rec = mint.engine.confirmations.issue(
        "local.empty_trash", {}, issued_by="user", ttl_s=60)

    later = build_host_broker(cfg=cfg)
    decision = later.engine.decide(ActionRequest(
        capability="local.empty_trash", args={}, source="user",
        confirmation_id=rec.id))
    assert decision.allowed and decision.code == "ALLOWED"


def test_worker_guard_engine_uses_the_durable_store(tmp_path, monkeypatch):
    from voicebridge.actions import PersistentConfirmationStore
    from voicebridge.dispatch_guard import build_worker_engine

    cfg = _cfg(tmp_path, monkeypatch)
    engine, mode = build_worker_engine("confirm", cfg)
    assert mode == "confirm"
    assert isinstance(engine.confirmations, PersistentConfirmationStore)


def test_worker_guard_auto_issue_is_in_memory_not_a_second_store(
        tmp_path, monkeypatch):
    from voicebridge.actions import PersistentConfirmationStore
    from voicebridge.dispatch_guard import WorkerGuard

    cfg = _cfg(tmp_path, monkeypatch)
    cfg.data.setdefault("safety", {})["worker_dispatch"] = "confirm"
    guard = WorkerGuard(cfg)
    store = guard.engine.confirmations
    assert isinstance(store, PersistentConfirmationStore)

    out = guard.run("agent.task", lambda text: "ok", "do it", source="user")
    assert out == "ok"
    # No internal auto-confirmation is written to the durable file.
    persisted = store._pstore.path.read_text() if \
        store._pstore.path.exists() else ""
    assert "confirmations" not in persisted or \
        not json.loads(persisted).get("confirmations")


def test_fork_guard_engine_uses_the_durable_store(tmp_path, monkeypatch):
    from voicebridge.actions import PersistentConfirmationStore
    from voicebridge import cli_qubit_forward as FW

    cfg = _cfg(tmp_path, monkeypatch)
    engine = FW._get_fork_engine(cfg)
    assert isinstance(engine.confirmations, PersistentConfirmationStore)
    # Distinct state trees never share a fork engine/store.
    other = FW._get_fork_engine()
    assert other is not engine


# ---------------------------------------------------------------------------
# A8 — ONE fork home
# ---------------------------------------------------------------------------
def test_plugin_home_resolves_the_single_runtime_home(tmp_path, monkeypatch):
    from voicebridge import qubit_plugins as HP
    from voicebridge import qubit_runtime as HR

    _cfg(tmp_path, monkeypatch)
    assert HP._qubit_home(None) == HR.default_home()


def test_legacy_home_migration_is_copy_only(tmp_path, monkeypatch):
    import sqlite3

    from voicebridge import qubit_runtime as HR

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    base = tmp_path / "state" / "qubit-voicebridge"
    legacy = base / "qubit"
    legacy.mkdir(parents=True)
    # A real SQLite DB (exercises the consistent online-backup copy) ...
    conn = sqlite3.connect(str(legacy / "state.db"))
    conn.execute("CREATE TABLE sessions (id TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('sess-legacy')")
    conn.commit()
    conn.close()
    # ... plus a non-DB file (exercises the copy-only fallback).
    (legacy / "kanban.db").write_bytes(b"legacy-kanban")

    target = base / "runtime"
    report = HR.migrate_legacy_homes(target)
    assert (target / "kanban.db").read_bytes() == b"legacy-kanban"
    copy = sqlite3.connect(str(target / "state.db"))
    try:
        rows = copy.execute("SELECT id FROM sessions").fetchall()
    finally:
        copy.close()
    assert rows == [("sess-legacy",)]
    # Nothing is moved or deleted: the legacy home is preserved.
    assert (legacy / "state.db").exists() and (legacy / "kanban.db").exists()
    assert {row["file"] for row in report["copied"]} == \
        {"state.db", "kanban.db"}

    # An existing canonical file always wins (never overwritten).
    (target / "kanban.db").write_bytes(b"canonical")
    (legacy / "kanban.db").write_bytes(b"newer-legacy")
    HR.migrate_legacy_homes(target)
    assert (target / "kanban.db").read_bytes() == b"canonical"
    assert (legacy / "kanban.db").read_bytes() == b"newer-legacy"


def test_use_qubit_home_migrates_once(tmp_path, monkeypatch):
    from voicebridge import qubit_runtime as HR

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    monkeypatch.setattr(HR, "_HOME_MIGRATED", False)
    base = tmp_path / "state" / "qubit-voicebridge"
    legacy = base / "hermes-runtime"
    legacy.mkdir(parents=True)
    (legacy / "projects.db").write_bytes(b"legacy-projects")

    home = HR.use_qubit_home()
    assert home == base / "runtime"
    assert (home / "projects.db").read_bytes() == b"legacy-projects"


# ---------------------------------------------------------------------------
# A9 — ONE board authority
# ---------------------------------------------------------------------------
def test_vendored_write_has_a_production_caller_and_one_authority(
        tmp_path, monkeypatch):
    from voicebridge import kanban as KB
    from voicebridge import kanban_bridge as KBG

    assert KBG.BOARD_AUTHORITY["writable"] == ["qubit-native"]
    assert KBG.BOARD_AUTHORITY["read_only"] == ["hermes-vendored"]

    # ``qubit kanban delegated --subcommand <verb>`` is production-reachable
    # (CLI + dashboard op) and now answers a vendored-store write through the
    # in-process boundary with the single typed refusal.
    out = KB.refuse_delegated("tasks")
    assert out["ok"] is False
    assert out["code"] == KBG.VENDORED_WRITE_CODE == "KANBAN_WRITE_BROKERED"
    assert out["writable_store"] == "qubit-native"
    assert out["authoritative_store"] == "qubit-native"

    # Execution-shaped subcommands keep their typed delegation notice.
    assert KB.refuse_delegated("swarm")["code"] == KB.DELEGATED_CODE
    assert KB.refuse_delegated("runs")["code"] == KB.DELEGATED_CODE


# ---------------------------------------------------------------------------
# A12 — ONE gateway listener
# ---------------------------------------------------------------------------
def test_second_listener_is_inert():
    from voicebridge import qubit_backend as HB

    with pytest.raises(HB.SecondListenerDisabled) as ei:
        HB.serve(host="127.0.0.1", port=9119)
    assert ei.value.code == "SECOND_LISTENER_DISABLED"
    # The in-process ASGI app/status stay available for read proxying.
    assert callable(HB.app) and callable(HB.status)


# ---------------------------------------------------------------------------
# A14 — catalog ``cli`` claims are truthful
# ---------------------------------------------------------------------------
def test_catalog_cli_claims_are_truthful():
    import argparse
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC
    from voicebridge.cli import build_parser

    assert OPS.CLI_PENDING == {}
    # Every Tier-1 registry op advertises cli (the parity closure proves a
    # real family); CLI_PENDING would be the only place to retract it.
    cat = {o["id"]: o for o in OPS.catalog()}
    for entry_id in VC.REGISTRY:
        assert "cli" in cat[entry_id]["interfaces"], entry_id

    # The one op the raw coverage map omits (`vision_describe`) still has a
    # real native parser path, so the cli claim is truthful.
    sub = next(a for a in build_parser()._actions
               if getattr(a, "dest", None) == "command")
    assert "vision" in sub.choices
    vision_sub = next(a for a in sub.choices["vision"]._actions
                      if isinstance(a, argparse._SubParsersAction))
    assert "describe" in vision_sub.choices


# ---------------------------------------------------------------------------
# A15 — scheduled/automation dispatch uses the operation contract
# ---------------------------------------------------------------------------
def test_scheduled_task_routes_through_run_op(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import dashboard_ops as OPS
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    seen = []

    def _turn(cfg_, text, **kwargs):
        seen.append(text)
        return {"ok": True, "reply": "done:" + text}

    monkeypatch.setattr(CT, "turn", _turn)
    SC.create(cfg, "job", "hello contract", "every 5 minutes",
              confirmed=True)
    out = SC.run_due(cfg, now=time.time() + 3600)
    assert out["ran"] == 1, out
    assert out["results"][0]["state"] == "OK", out
    assert seen == ["hello contract"]
    # The dispatch op exists in the one-operation catalog and is dashboard
    # settlable through the shared journal (no private executor).
    assert "schedule_dispatch" in OPS.EXTRA_OPS


def test_scheduled_directive_routes_through_run_op(tmp_path, monkeypatch):
    from voicebridge import automation as AU
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    seen = []

    def _run_directive(cfg_, text, **kwargs):
        seen.append(text)
        return {"state": "OK", "code": "OK", "detail": "directive ran"}

    monkeypatch.setattr(AU, "run_directive", _run_directive)
    SC.create(cfg, "directive", "automation: status", "every 5 minutes",
              confirmed=True)
    out = SC.run_due(cfg, now=time.time() + 3600)
    assert out["ran"] == 1, out
    assert out["results"][0]["state"] == "OK", out
    assert seen == ["automation: status"]
