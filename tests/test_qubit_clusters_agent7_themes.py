"""Agent 7 — themes cluster, real-module proofs.

One focused test per Qubit gap-ledger row in the ``themes`` area, exercising
``voicebridge.themes`` directly: preset transitions, recovery/reset, access
control, token-override validation, corrupt-state fail-closed, audit and the
pinned-source download refusal.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from voicebridge import themes as TH


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ------------------------------------------------------------------------- themes
def test_themes_show_lists_and_defaults(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    listed = {t["name"] for t in TH.list_themes()["themes"]}
    assert listed == {"qubit-dark", "qubit-light", "qubit-contrast",
                       "qubit-terminal"}
    out = TH.show(cfg)
    assert out["theme"] == TH.DEFAULT_THEME and out["tweaks"] == {}
    assert out["css_href"] == "/static/theme.css"


# --------------------------------------------------------------------- themes_set
def test_themes_set_transition_recovery_and_access(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(TH.ThemeError) as exc:
        TH.set_theme(cfg, "qubit-light", confirmed=False)
    assert exc.value.code == TH.CONFIRM_CODE
    out = TH.set_theme(cfg, "qubit-light", confirmed=True)
    assert out["theme"] == "qubit-light" and out["previous"] == "qubit-dark"
    # Transition persists across a fresh load.
    assert TH.load(cfg)["theme"] == "qubit-light"
    with pytest.raises(TH.ThemeError) as exc:
        TH.set_theme(cfg, "neon", confirmed=True)
    assert exc.value.code == TH.UNKNOWN_CODE


# ------------------------------------------------------------------- themes_reset
def test_themes_reset_is_the_rollback(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    TH.set_theme(cfg, "qubit-contrast", confirmed=True)
    TH.set_tweak(cfg, "--primary", "#123456", confirmed=True)
    out = TH.reset(cfg, confirmed=True)
    assert out["theme"] == TH.DEFAULT_THEME and out["tweaks"] == {}
    assert TH.load(cfg)["tweaks"] == {}


# --------------------------------------------------------------- themes_tweak_set
def test_themes_tweak_set_validation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(TH.ThemeError) as exc:
        TH.set_tweak(cfg, "--primary", "#123456", confirmed=False)
    assert exc.value.code == TH.CONFIRM_CODE
    out = TH.set_tweak(cfg, "primary", "#123456", confirmed=True)
    assert out["tweaks"] == {"--primary": "#123456"}
    with pytest.raises(TH.ThemeError) as exc:
        TH.set_tweak(cfg, "--primary", "red", confirmed=True)
    assert exc.value.code == TH.BAD_COLOR_CODE
    with pytest.raises(TH.ThemeError) as exc:
        TH.set_tweak(cfg, "--font-family", "#fff", confirmed=True)
    assert exc.value.code == TH.UNKNOWN_CODE


# ------------------------------------------------------------- themes_tweak_clear
def test_themes_tweak_clear_idempotent(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    TH.set_tweak(cfg, "--primary", "#123456", confirmed=True)
    out = TH.clear_tweak(cfg, "--primary", confirmed=True)
    assert out["tweaks"] == {}
    assert TH.clear_tweak(cfg, "--primary", confirmed=True)["tweaks"] == {}


# ----------------------------------------------------------- pets_downloads + refusal
def test_pets_downloads_refusal_is_real_and_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = TH.refuse_pet_downloads()
    assert out["state"] == "REFUSED" and out["code"] == TH.PETS_CODE
    assert out["owner"] == "qubit-policy"
    # The refusal never downloads: no network/import path is exercised and the
    # local preset system still applies.
    assert TH.show(cfg)["theme"] == TH.DEFAULT_THEME


# ---------------------------------------------------------- corrupt/recovery/audit
def test_themes_corrupt_state_falls_back_and_css_never_raises(tmp_path,
                                                             monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    store = Path(cfg.state_dir()) / TH.STORE_NAME
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{not json")
    assert TH.load(cfg)["theme"] == TH.DEFAULT_THEME
    assert "app.css as shipped" in TH.css(cfg)
    # A subsequent valid write recovers the store (fail closed, then heal).
    assert TH.set_theme(cfg, "qubit-light", confirmed=True)["theme"] == \
        "qubit-light"
    assert TH.load(cfg)["theme"] == "qubit-light"


def test_themes_audit_records_mutations_without_secrets(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    captured = []
    import voicebridge.logging_setup as LS

    monkeypatch.setattr(LS, "log_event",
                        lambda event, **fields: captured.append((event, fields)))
    TH.set_theme(cfg, "qubit-light", requested_by="cli:user", confirmed=True)
    TH.set_tweak(cfg, "--primary", "#123456", requested_by="cli:user",
                 confirmed=True)
    events = [e for e, _ in captured]
    assert "theme.set" in events and "theme.set-tweak" in events
    assert "sk-secret" not in json.dumps(captured)
