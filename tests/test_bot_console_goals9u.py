"""Bot/harness console sidecar: goals-set accepts a missing milestones field.

Regression test: ``bot/goals-set`` without ``milestones`` used to raise
``TypeError: 'NoneType' object is not iterable`` (surfaced as
GATEWAY_METHOD_FAILED); the frontend ``setBotGoal`` omits the field when the
caller passes no milestones. Hermetic: temp XDG/QUBIT_HOME only.
"""
from __future__ import annotations

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
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qubit_home"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_goals_set_without_milestones(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/bot/goals-set", {},
                            {"confirm": True, "title": "g-no-ms"})
    assert code == 200, out
    assert out.get("state") == "OK", out
    assert out["goal"]["milestones"] == [], out


def test_goals_set_with_milestones_roundtrip(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/bot/goals-set", {},
                            {"confirm": True, "title": "g-ms",
                             "milestones": ["m1", "m2"]})
    assert code == 200, out
    assert out["goal"]["milestones"] == ["m1", "m2"], out
    code, listed = HC.dispatch(cfg, "GET", "/api/qubit/bot/goals-list",
                               {}, None)
    assert code == 200, listed
    ids = [g.get("id") for g in listed.get("goals", [])]
    assert out["goal"]["id"] in ids, listed


def test_goals_set_rejects_empty_title(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/bot/goals-set", {},
                            {"confirm": True, "title": ""})
    assert code == 400, out
    assert out.get("code") == "BOT_GOAL_BAD_PARAMS", out
