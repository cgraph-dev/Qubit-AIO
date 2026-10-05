"""Orb in-game override: fullscreen rule with ``waveform_ingame``.

Owner contract 2026-10-04: "SHOW WAVEFORM INGAME" keeps the orb mapped over
fullscreen games; "HIDE WAVEFORM INGAME" restores today's auto-hide (withdraw
on fullscreen focus, return on alt-tab) while the orb stays active.

- ``next_visibility(..., ingame_override=True)`` skips the auto-withdraw and
  restores an orb WE hid, even while fullscreen is focused. A window the owner
  closed themselves (withdrawn without our flag) is still never forced back.
- The fullscreen tick reads the override through the EXISTING poll: a
  read-only settings GET alongside status/log, same auth, no new auth.

Hermetic: fake ``run`` (no X11), bare-orb rule drives (no Tk), fake openers.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.orb import client as orb_client
from voicebridge.orb import fullscreen as fs


# -- pure rule with the override --------------------------------------------

def test_override_on_skips_auto_withdraw_over_fullscreen():
    assert fs.next_visibility(True, True, False, ingame_override=True) == (
        "none", False)


def test_override_on_restores_our_hidden_orb_even_while_fullscreen():
    # The owner said SHOW while we were auto-hidden ingame: come back.
    assert fs.next_visibility(True, False, True, ingame_override=True) == (
        "show", False)


def test_override_on_restores_after_alt_tab():
    assert fs.next_visibility(False, False, True, ingame_override=True) == (
        "show", False)


def test_override_on_never_forces_user_closed_orb():
    assert fs.next_visibility(True, False, False, ingame_override=True) == (
        "none", False)
    assert fs.next_visibility(False, False, False, ingame_override=True) == (
        "none", False)


def test_override_on_leaves_visible_windowed_orb_alone():
    assert fs.next_visibility(False, True, False, ingame_override=True) == (
        "none", False)


def test_override_defaults_off_todays_behavior_preserved():
    # No override arg = today's hide-on-fullscreen + return-on-alt-tab.
    assert fs.next_visibility(True, True, False) == ("hide", True)
    assert fs.next_visibility(False, False, True) == ("show", False)
    assert fs.next_visibility(True, True, False, ingame_override=False) == (
        "hide", True)


# -- app wiring (no Tk: drive the rule method on a bare instance) -----------

from contextlib import contextmanager


@contextmanager
def _bare_orb(probe, visible, auto, ingame=False):
    import unittest.mock as _mock

    from voicebridge.orb import app as orb_app
    orb = object.__new__(orb_app.VoiceOrb)
    orb._fullscreen_probe = probe
    orb._fs_auto_hidden = auto
    orb.ingame_override = ingame
    calls: list = []
    orb.hide_to_background = lambda: calls.append("hide")  # type: ignore[method-assign]
    orb.show = lambda: calls.append("show")  # type: ignore[method-assign]
    orb._calls = calls
    with _mock.patch.object(orb_app.VoiceOrb, "visible",
                            new_callable=_mock.PropertyMock,
                            return_value=visible):
        yield orb


def test_app_rule_override_stays_mapped_over_fullscreen():
    from voicebridge.orb import app as orb_app
    with _bare_orb(lambda: True, True, False, ingame=True) as orb:
        orb_app.VoiceOrb._apply_fullscreen_rule(orb)
        assert orb._calls == []
        assert orb._fs_auto_hidden is False


def test_app_rule_override_restores_auto_hidden_while_fullscreen():
    from voicebridge.orb import app as orb_app
    with _bare_orb(lambda: True, False, True, ingame=True) as orb:
        orb_app.VoiceOrb._apply_fullscreen_rule(orb)
        assert orb._calls == ["show"]
        assert orb._fs_auto_hidden is False


def test_app_rule_override_off_still_withdraws():
    from voicebridge.orb import app as orb_app
    with _bare_orb(lambda: True, True, False, ingame=False) as orb:
        orb_app.VoiceOrb._apply_fullscreen_rule(orb)
        assert orb._calls == ["hide"]
        assert orb._fs_auto_hidden is True


def test_app_rule_missing_override_attr_defaults_off():
    # Bare orbs built before the override (or without the poll yet run)
    # behave exactly like today: hide on fullscreen.
    import unittest.mock as _mock

    from voicebridge.orb import app as orb_app
    orb = object.__new__(orb_app.VoiceOrb)
    orb._fullscreen_probe = lambda: True
    orb._fs_auto_hidden = False
    calls: list = []
    orb.hide_to_background = lambda: calls.append("hide")  # type: ignore[method-assign]
    orb.show = lambda: calls.append("show")  # type: ignore[method-assign]
    with _mock.patch.object(orb_app.VoiceOrb, "visible",
                            new_callable=_mock.PropertyMock,
                            return_value=True):
        orb_app.VoiceOrb._apply_fullscreen_rule(orb)
    assert calls == ["hide"]


# -- client: read-only settings GET (same auth, no new auth) -----------------

import json as _json


class _Resp:
    def __init__(self, payload):
        self._raw = _json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener_factory(seen, payload):
    def _open(req, timeout=10.0):
        seen.append(req.full_url)
        # urllib capitalizes header names ("X-Api-Token" -> "X-api-token"),
        # so compare case-insensitively.
        hdrs = {k.lower(): v for k, v in req.header_items()}
        assert "x-api-token" in hdrs, "orb settings GET must use the same auth"
        return _Resp(payload)
    return _open


def test_client_get_settings_uses_same_auth_and_prefix():
    seen: list = []
    cli = orb_client.OrbClient("http://127.0.0.1:8890", "tok",
                               opener=_opener_factory(
                                   seen, {"settings": {"waveform_ingame": True}}))
    out = cli.get_settings()
    assert out == {"waveform_ingame": True}
    assert seen == ["http://127.0.0.1:8890/agent/api/voice-edit/settings"]


def test_client_get_settings_malformed_is_empty_never_raises():
    cli = orb_client.OrbClient("http://127.0.0.1:8890", "tok",
                               opener=_opener_factory(seen := [], {"nope": 1}))
    assert cli.get_settings() == {}
    cli2 = orb_client.OrbClient("http://127.0.0.1:8890", "tok",
                                opener=_opener_factory(seen, {}))
    assert cli2.get_settings() == {}
