"""D4 — OpenClaw browser/desktop rows closed by real capability + negative.

* OC-10 browser profiles + dedicated lifecycle — ``voicebridge/browser.py``:
  Qubit manages only its own dedicated profiles; a real/user profile name is
  always refused with the typed ``BROWSER_CLOSE_REFUSED``.
* OC-15 ``screen`` tool (arrange the Control UI) —
  ``voicebridge/computer_use.py``: a bounded, persisted pane arrangement that
  never drives a window manager or reads the display.
"""
from __future__ import annotations

import json
from pathlib import Path

from voicebridge import browser as BR
from voicebridge import computer_use as CU


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ------------------------------------------------------------------ OC-10
def test_oc10_managed_profile_lifecycle(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert BR.profiles(cfg)["profiles"] == []
    assert not BR._registry_path(cfg).exists()

    denied = BR.create_profile(cfg, "research")
    assert denied["state"] == "denied" and denied["code"] == BR.CONFIRM_CODE
    assert not BR._registry_path(cfg).exists()

    created = BR.create_profile(cfg, "research", cdp_port=9310, confirmed=True)
    assert created["state"] == "OK"
    assert created["profile"]["managed"] is True
    assert created["profile"]["cdp_port"] == 9310
    assert (BR._profiles_root(cfg) / "research").is_dir()

    opened = BR.open_profile(cfg, "research", launcher=lambda d, p: {"pid": 42},
                             confirmed=True)
    assert opened["profile"]["state"] == "running"
    assert opened["profile"]["pid"] == 42

    closed = BR.close_profile(cfg, "research",
                              stopper=lambda entry: None, confirmed=True)
    assert closed["state"] == "OK" and closed["profile"]["state"] == "stopped"

    removed = BR.remove_profile(cfg, "research", confirmed=True)
    assert removed["removed"] is True
    assert not (BR._profiles_root(cfg) / "research").exists()


def test_oc10_user_profiles_always_refused(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    for name in ("Default", "default", "user", "personal", "chromium"):
        out = BR.close_profile(cfg, name, confirmed=True)
        assert out["state"] == "REFUSED" and out["code"] == BR.CLOSE_CODE, name
        assert out["owner"] == "qubit-policy"
    # a refusal never mints a registry or a profile directory
    assert not BR._registry_path(cfg).exists()
    # reserved names cannot be created as managed profiles either
    assert BR.create_profile(cfg, "default",
                             confirmed=True)["code"] == \
        BR.PROFILE_BAD_INPUT_CODE
    # out-of-range ports (a public/user listener) are refused
    assert BR.create_profile(cfg, "work", cdp_port=80,
                             confirmed=True)["code"] == \
        BR.PROFILE_BAD_INPUT_CODE


def test_oc10_profile_ops_fail_closed_on_estop_and_redact(tmp_path,
                                                          monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(BR, "_estop", lambda _cfg: True)
    assert BR.create_profile(cfg, "research",
                             confirmed=True)["code"] == BR.ESTOP_CODE
    monkeypatch.setattr(BR, "_estop", lambda _cfg: False)
    out = BR.refuse_close_profile("x token=abc123secret")
    assert "abc123secret" not in json.dumps(out)


# ------------------------------------------------------------------ OC-15
def test_oc15_screen_catalog_and_arrange_persists(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    catalog = CU.screen_panes()
    assert catalog["schema"] == CU.SCREEN_SCHEMA
    assert set(catalog["panes"]) == set(CU.SCREEN_PANES)
    assert CU.screen_status(cfg)["arranged"] is False

    denied = CU.screen(cfg, ["chat", "files"])
    assert denied["state"] == "denied" and denied["code"] == \
        CU.SCREEN_CONFIRM_CODE
    assert CU.screen_status(cfg)["arranged"] is False

    out = CU.screen(cfg, "chat,files", layout="columns", confirmed=True)
    assert out["state"] == "OK" and out["panes"] == ["chat", "files"]
    status = CU.screen_status(cfg)
    assert status["arranged"] is True and status["panes"] == ["chat", "files"]
    # only a declarative Control UI descriptor is written; no display access
    assert "desktop" not in out["authority"] or "no desktop" in out["authority"]


def test_oc15_screen_bad_input_and_estop(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    bad = CU.screen(cfg, ["chat", "not-a-pane"], confirmed=True)
    assert bad["state"] == "FAILED" and bad["code"] == CU.SCREEN_BAD_INPUT_CODE
    dup = CU.arrange_screen(cfg, ["chat", "chat"])
    assert dup["code"] == CU.SCREEN_BAD_INPUT_CODE
    empty = CU.arrange_screen(cfg, "")
    assert empty["code"] == CU.SCREEN_BAD_INPUT_CODE
    too_many = CU.arrange_screen(cfg, list(CU.SCREEN_PANES))
    assert too_many["code"] == CU.SCREEN_BAD_INPUT_CODE
    bad_layout = CU.arrange_screen(cfg, ["chat"], layout="spiral")
    assert bad_layout["code"] == CU.SCREEN_BAD_INPUT_CODE

    monkeypatch.setattr(CU, "_estop", lambda _cfg: True)
    latched = CU.screen(cfg, ["chat"], confirmed=True)
    assert latched["state"] == "ESTOP" and latched["code"] == CU.SCREEN_ESTOP_CODE
    # fail-closed arrange leaves the persisted layout untouched
    assert CU.screen_status(cfg)["arranged"] is False
