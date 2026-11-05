"""Focused tests for the second read-cache wave (gateway/approvals/model-info/
toolsets/update-check/config + the proxy-first config read).

Each test proves a cache hit (the expensive inner read runs once) and that the
matching mutation path invalidates the snapshot. Cache is enabled only inside
this file (the global default stays off under pytest).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    from voicebridge import read_cache as RC

    monkeypatch.setenv("QUBIT_READ_CACHE", "1")
    RC.reset_for_tests()
    yield
    RC.reset_for_tests()


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    voice = tmp_path / "voice.yaml"
    voice.write_text("logging:\n  jsonl: false\n", encoding="utf-8")
    c = C.Config.load(voice)
    home = c.state_dir() / "runtime"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "model:\n  provider: opencode-go\n  default: deepseek-v4-flash\n",
        encoding="utf-8")
    monkeypatch.setenv("QUBIT_HOME", str(home))
    return c


def test_gateway_cache_and_supervision_invalidation(cfg, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import gateway_supervisor as GS
    from voicebridge import qubit_compat as QC

    calls = []
    monkeypatch.setattr(
        OPS, "run_op",
        lambda *a, **k: calls.append("run") or {"state": "OK"})
    monkeypatch.setattr(
        GS, "status",
        lambda c: calls.append("status") or {"available": True,
                                             "detail": "", "state": "OK"})
    monkeypatch.setattr(GS, "service_unit", lambda c: "qubit-gateway.service")
    monkeypatch.setattr(
        GS, "control",
        lambda *a, **k: {"ok": True, "state": "OK", "code": ""})

    first = QC.h_gateway(cfg, {}, {}, {})
    second = QC.h_gateway(cfg, {}, {}, {})
    assert first == second
    assert calls == ["run", "status"]

    # Start/stop/restart drops the snapshot even though no stamped file changed.
    QC.h_gateway_supervision("start")(cfg, {}, {"confirm": True}, {})
    QC.h_gateway(cfg, {}, {}, {})
    assert calls == ["run", "status", "run", "status"]


def test_gateway_drain_invalidates(cfg, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import gateway_supervisor as GS
    from voicebridge import qubit_compat as QC

    calls = []
    monkeypatch.setattr(
        OPS, "run_op", lambda *a, **k: calls.append(1) or {"state": "OK"})
    monkeypatch.setattr(
        GS, "status", lambda c: {"available": True, "detail": "",
                                 "state": "OK"})
    monkeypatch.setattr(GS, "service_unit", lambda c: "qubit-gateway.service")

    QC.h_gateway(cfg, {}, {}, {})
    QC.h_gateway(cfg, {}, {}, {})
    assert len(calls) == 1
    QC.h_gateway_drain(cfg, {}, {"action": "drain"}, {})
    # The drain marker also changes the key, but the explicit invalidation is
    # what must drop the entry even if a caller suppresses the file stamp.
    from voicebridge import read_cache as RC

    assert RC.stats()["invalidations"] >= 1
    QC.h_gateway(cfg, {}, {}, {})
    assert len(calls) == 2


def test_approvals_cache_and_journal_transition_invalidation(cfg, monkeypatch):
    from voicebridge import approvals as AP
    from voicebridge import qubit_compat as QC

    calls = []
    original = AP.ApprovalJournal.pending

    def _counted(self):
        calls.append(1)
        return original(self)

    monkeypatch.setattr(AP.ApprovalJournal, "pending", _counted)

    journal = AP.ApprovalJournal(
        Path(cfg.state_dir()) / "approvals.jsonl", default_ttl_s=120.0)

    first = QC.h_approvals_list(cfg, {}, {}, {})
    second = QC.h_approvals_list(cfg, {}, {}, {})
    assert first == second
    assert calls == [1]

    item = journal.create("test.capability", "do the thing", ttl_s=60)
    third = QC.h_approvals_list(cfg, {}, {}, {})
    assert third[1]["count"] == 1
    assert calls == [1, 1]

    journal.respond(item.id, "deny")
    fourth = QC.h_approvals_list(cfg, {}, {}, {})
    assert fourth[1]["count"] == 0
    assert calls == [1, 1, 1]


def test_model_info_cache_and_model_set_invalidation(cfg, monkeypatch):
    from voicebridge import qubit_compat as QC
    from voicebridge import read_cache as RC
    from voicebridge.providers import resolver as R

    calls = []
    monkeypatch.setattr(
        QC, "_resolved_model",
        lambda c: calls.append(1) or {"model": "m1", "provider": "p1",
                                      "ctx": 1000, "profile_id": "m1",
                                      "kind": "api", "base_url": ""})
    monkeypatch.setattr(QC, "_vendored_runtime", lambda c: None)
    monkeypatch.setattr(QC, "_auto_context_length", lambda m, b, p: 1000)
    monkeypatch.setattr(R, "model_identity",
                        lambda c: {"match": True, "code": ""})

    first = QC.h_model_info(cfg, {}, {}, {})
    second = QC.h_model_info(cfg, {}, {}, {})
    assert first == second
    assert len(calls) == 1

    # A requested model is a distinct entry.
    QC.h_model_info(cfg, {"model": "other"}, {}, {})
    assert len(calls) == 2

    # The real model-switch handler invalidates the resolved snapshot.
    key = QC._model_info_cache_key(cfg, {})
    assert RC.get(key) is not None
    from voicebridge import dashboard_ops as OPS

    monkeypatch.setattr(
        OPS, "run_op",
        lambda *a, **k: {"state": "executed", "speech": "Set."})
    monkeypatch.setattr(QC, "_publish_chat_frame", lambda *a, **k: None)
    status, out = QC.h_model_set(
        cfg, {}, {"model": "m2", "confirmed": True}, {})
    assert status == 200 and out["ok"] is True
    assert RC.get(key) is None


def test_toolsets_cache_and_mutation_invalidation(cfg, monkeypatch):
    from voicebridge import qubit_compat as QC
    from voicebridge import qubit_runtime as QR
    from voicebridge import toolsets as TS

    def _no_vendored():
        raise RuntimeError("vendored runtime unavailable in this test")

    monkeypatch.setattr(QR, "ensure_path", _no_vendored)

    calls = []
    original = TS.toolset_rows
    monkeypatch.setattr(
        TS, "toolset_rows",
        lambda c, n: calls.append(1) or original(c, n))

    first = QC.h_toolsets(cfg, {}, {}, {})
    second = QC.h_toolsets(cfg, {}, {}, {})
    assert first == second
    assert len(calls) == 1

    TS.apply_toolset_mutation(cfg, "alpha", False)
    QC.h_toolsets(cfg, {}, {}, {})
    assert len(calls) == 2


def test_update_check_cache_force_and_invalidation(cfg, monkeypatch):
    from voicebridge import qubit_compat as QC
    from voicebridge import read_cache as RC

    calls = []
    monkeypatch.setattr(QC, "_update_resolve_source", lambda *a, **k: None)
    monkeypatch.setattr(
        QC, "_update_check",
        lambda *a, **k: calls.append(1) or {
            "install_method": "git", "current_version": "1.0.0",
            "behind": 0, "update_available": False, "can_apply": True})

    first = QC.h_qubit_update_check(cfg, {}, {}, {})
    second = QC.h_qubit_update_check(cfg, {}, {}, {})
    assert first == second
    assert len(calls) == 1

    # ``force`` (the "Check now" button) always re-runs the check.
    QC.h_qubit_update_check(cfg, {"force": "1"}, {}, {})
    assert len(calls) == 2

    RC.invalidate("update_check")
    QC.h_qubit_update_check(cfg, {}, {}, {})
    assert len(calls) == 3


def test_config_cache_and_save_invalidation(cfg, monkeypatch):
    from voicebridge import qubit_asgi as HA
    from voicebridge import qubit_compat as QC
    from voicebridge import read_cache as RC

    calls = []
    monkeypatch.setattr(
        QC, "_config_live",
        lambda c: calls.append(1) or {"general": {"x": 1}})
    monkeypatch.setattr(QC, "_terminal_live_block", lambda: {})

    first = QC.h_config(cfg, {}, {}, {})
    second = QC.h_config(cfg, {}, {}, {})
    assert first == second
    assert len(calls) == 1
    key = QC._config_read_key(cfg)
    assert RC.get(key) is not None

    def _flatten(obj):
        # The request carries the new value; the served record carries the old.
        return {"general.x": 2 if obj is body else 1}

    body = {"general": {"x": 2}, "confirm": True}
    monkeypatch.setattr(QC, "_flatten_config", _flatten)
    monkeypatch.setattr(QC, "_config_scalar_paths",
                        lambda rec: {"general.x": 0})
    monkeypatch.setattr(QC, "_compat_confirm", lambda *a, **k: None)
    monkeypatch.setattr(
        QC, "_config_overrides_path",
        lambda c: Path(c.state_dir()) / "qubit_config_overrides.json")
    monkeypatch.setattr(QC, "_load_json_store", lambda p: {})
    monkeypatch.setattr(QC, "_save_json_store", lambda p, v: None)
    monkeypatch.setattr(QC, "_persist_overrides_to_home",
                        lambda values, c: False)
    monkeypatch.setattr(QC, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(HA, "call",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("no in-process bridge in this test")))

    status, out = QC.config_save(cfg, {}, body, {})
    assert status == 200 and out["ok"] is True
    assert RC.get(key) is None


def test_proxied_config_read_cache_is_scoped_and_config_invalidated(
        cfg, monkeypatch):
    from voicebridge import read_cache as RC
    from voicebridge import webui

    key = webui._proxied_read_cache_key(cfg, "GET", "/api/config", {})
    RC.put(key, (200, {"content-type": "application/json"}, b'{"ok":true}'), 60)
    assert webui._proxied_read_cache_get(
        cfg, "GET", "/api/config", {}) is not None

    # A different query (profile/include_defaults) is a different entry.
    assert webui._proxied_read_cache_get(
        cfg, "GET", "/api/config", {"profile": "other"}) is None

    # Only the declared read path is cached; mutations are never stored.
    assert webui._proxied_read_cache_get(
        cfg, "GET", "/api/health", {}) is None
    webui._proxied_read_cache_put(
        cfg, "POST", "/api/config", {}, 200, {}, b"{}")
    assert webui._proxied_read_cache_get(
        cfg, "POST", "/api/config", {}) is None

    # The config-save invalidation namespace clears this snapshot too.
    RC.invalidate("config")
    assert webui._proxied_read_cache_get(
        cfg, "GET", "/api/config", {}) is None


def test_proxied_config_key_tracks_runtime_config_mtime(cfg):
    from voicebridge import webui

    before = webui._proxied_read_cache_key(cfg, "GET", "/api/config", {})
    path = Path(os.environ["QUBIT_HOME"]) / "config.yaml"
    path.write_text("model:\n  provider: opencode-go\n  default: renamed\n",
                    encoding="utf-8")
    after = webui._proxied_read_cache_key(cfg, "GET", "/api/config", {})
    assert before != after


def test_typed_refusal_is_not_cached(cfg, monkeypatch):
    """A failing read must be retried; only 2xx payloads are snapshotted."""
    from voicebridge import qubit_compat as QC

    calls = []

    def _boom(*_a, **_k):
        calls.append(1)
        raise RuntimeError("update backend unavailable")

    monkeypatch.setattr(QC, "_update_resolve_source", lambda *a, **k: None)
    monkeypatch.setattr(QC, "_update_check", _boom)

    first = QC.h_qubit_update_check(cfg, {}, {}, {})
    second = QC.h_qubit_update_check(cfg, {}, {}, {})
    assert first[0] == second[0] == 500
    assert first[1]["ok"] is False
    assert len(calls) == 2
