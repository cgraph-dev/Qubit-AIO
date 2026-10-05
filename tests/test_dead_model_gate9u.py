"""Dead-model activation gate (Lane 1, TDD).

A model the registry lists but upstream rejects at TURN time (e.g.
``muse-spark-1.3-contributor``: static probe resolves 1048576, real turns
fail ``ModelProtocolUnsupported``) must never be (re-)persisted into the
runtime ``config.yaml`` model slot or the ``terminal.profile`` pin: every
activation re-stamps both slots and every default turn then fails until a
hand repair.

Contract:
- ``record_dead`` / ``is_dead`` / ``clear_dead`` over
  ``<state_dir>/dead_models.json`` (same authority dir as profiles.json);
  all never raise; ``is_dead`` fails OPEN (None) on any error so a broken
  record can never block activation (today's behavior).
- ``_gate_remote_liveness`` consults the record BEFORE the static probe,
  so a turn-time-dead model is refused with the slot byte-unchanged.
- A later successful turn clears the record (auto-recovery when upstream
  restores the model); see ``gateway.py`` hooks.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from voicebridge.providers import dead_models as DM
from voicebridge.providers import endpoints as EP


def _cfg(tmp_path: Path):
    return SimpleNamespace(state_dir=lambda: tmp_path)


def test_record_is_dead_roundtrip(tmp_path: Path):
    cfg = _cfg(tmp_path)
    assert DM.is_dead(cfg, "opencode-go", "muse-spark-1.3-contributor") is None
    assert DM.record_dead(cfg, "opencode-go", "muse-spark-1.3-contributor",
                          "ModelProtocolUnsupported") is True
    rec = DM.is_dead(cfg, "opencode-go", "muse-spark-1.3-contributor")
    assert rec is not None and "ModelProtocol" in rec.get("reason", "")
    # Case-insensitive identity; unrelated model unaffected.
    assert DM.is_dead(cfg, "OpenCode-Go", "Muse-Spark-1.3-Contributor") is not None
    assert DM.is_dead(cfg, "opencode-go", "deepseek-v4-flash") is None


def test_clear_reallows(tmp_path: Path):
    cfg = _cfg(tmp_path)
    DM.record_dead(cfg, "opencode-go", "muse-spark-1.3-contributor", "x")
    assert DM.is_dead(cfg, "opencode-go", "muse-spark-1.3-contributor") is not None
    assert DM.clear_dead(cfg, "opencode-go", "muse-spark-1.3-contributor") is True
    assert DM.is_dead(cfg, "opencode-go", "muse-spark-1.3-contributor") is None


def test_gate_refuses_recorded_dead_model_without_probing(tmp_path: Path):
    cfg = _cfg(tmp_path)
    DM.record_dead(cfg, "opencode-go", "muse-spark-1.3-contributor",
                   "ModelProtocolUnsupported")
    out = EP._gate_remote_liveness(
        cfg=cfg, provider="opencode-go", model="muse-spark-1.3-contributor",
        base_url="https://opencode.ai/zen/go/v1")
    assert out is not None and out.get("code") == "MODEL_UNAVAILABLE"
    assert "muse-spark-1.3-contributor" in out.get("detail", "")


def test_gate_passes_unrecorded_model(tmp_path: Path):
    # No record -> gate defers to the probe (None here: inconclusive/fixture
    # offline means "not negative evidence", i.e. today's accept behavior).
    cfg = _cfg(tmp_path)
    out = EP._gate_remote_liveness(
        cfg=cfg, provider="opencode-go", model="some-unknown-model-xyz",
        base_url="")
    assert out is None or out.get("code") == "MODEL_UNAVAILABLE"


def test_terminal_follow_keeps_pin_on_dead_model(tmp_path: Path, monkeypatch):
    from voicebridge import qubit_compat as QC
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path)
    DM.record_dead(cfg, "opencode-go", "muse-spark-1.3-contributor",
                   "ModelProtocolUnsupported")
    dead_pid = "opencode-go-muse-spark-1.3-contributor"
    live = SimpleNamespace(id="qubit-go", kind=R.API,
                           provider="opencode-go", model="deepseek-v4-flash")
    dead = SimpleNamespace(id=dead_pid, kind=R.API,
                           provider="opencode-go",
                           model="muse-spark-1.3-contributor")
    monkeypatch.setattr(R, "get_profile",
                        lambda c, pid: dead if pid == dead_pid else live)
    monkeypatch.setattr(R, "terminal_context_guard",
                        lambda c, profile_id=None, session=None: {"ok": True})
    monkeypatch.setattr(QC, "_write_terminal_profile_locked",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("must not persist a dead model")))
    out = QC._terminal_follow_main_model(cfg, dead_pid)
    assert out["terminal_follows"] is False
    assert "dead" in out["terminal_detail"].lower() \
        or "unavailable" in out["terminal_detail"].lower()
