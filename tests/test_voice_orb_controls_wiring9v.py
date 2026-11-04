"""Orb tray/settings control wiring (voicebridge.orb.qt_app).

The pure tray/panel contracts are covered by ``test_voice_orb_tray9u`` and
``test_voice_orb_panel9u``. The gap these tests close is the wiring that
turns a tray/settings id into a real local-brain action:

* ``QtVoiceOrb._run_brain_action`` must dispatch ``brain_unload``/``brain_warm``
  through ``voicebridge.local.actions.ACTIONS`` (the same registry the voice
  command uses) and ignore every other id.
* ``QtVoiceOrb._maybe_start_tray`` must pass those actions to ``OrbTray`` as
  the ``on_brain_unload``/``on_brain_warm`` callbacks.

Hermetic: no display and no system tray. The tests only need PySide6 to be
importable so the conditional ``QtVoiceOrb`` class exists; they never
construct a widget.
"""
from __future__ import annotations

import threading
import types

import pytest


qa = pytest.importorskip("voicebridge.orb.qt_app")
if getattr(qa, "QtVoiceOrb", None) is None:
    pytest.skip("PySide6 unavailable", allow_module_level=True)


def _dummy_orb(**attrs):
    """Small stand-in carrying only what the bound methods read."""
    d = types.SimpleNamespace()
    for name, value in attrs.items():
        setattr(d, name, value)
    return d


def test_run_brain_action_dispatches_registered_action(monkeypatch):
    from voicebridge.local import actions as local_actions

    calls = []
    done = threading.Event()

    def _fn(_params):
        calls.append(_params)
        done.set()
        return "ok"

    monkeypatch.setitem(local_actions.ACTIONS, "brain_unload",
                        types.SimpleNamespace(fn=_fn))

    qa.QtVoiceOrb._run_brain_action(_dummy_orb(), "brain_unload")

    assert done.wait(5), "action was not dispatched"
    assert calls == [{}]


def test_run_brain_action_rejects_non_brain_ids(monkeypatch):
    from voicebridge.local import actions as local_actions

    calls = []

    def _fn(_params):
        calls.append(_params)
        return "ok"

    monkeypatch.setitem(local_actions.ACTIONS, "brain_warm",
                        types.SimpleNamespace(fn=_fn))

    for bad in ("quit", "toggle", "open_website", "", "brain_warm2"):
        qa.QtVoiceOrb._run_brain_action(_dummy_orb(), bad)

    assert calls == []


def test_maybe_start_tray_wires_brain_callbacks(monkeypatch):
    from voicebridge.orb import options as opts_mod
    from voicebridge.orb import tray as tray_mod

    instances = []

    class _FakeTray:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            instances.append(self)

        def start(self):
            return True

    monkeypatch.setattr(tray_mod, "OrbTray", _FakeTray)

    brain_calls = []

    dummy = _dummy_orb(
        _options=opts_mod.OrbOptions(show_tray=True),
        _tray=None,
        request_toggle=lambda: None,
        open_settings=lambda: None,
        request_quit=lambda: None,
        set_test_state=lambda *a, **k: None,
        apply_options=lambda o: None,
        _run_brain_action=lambda action: brain_calls.append(action),
    )

    qa.QtVoiceOrb._maybe_start_tray(dummy)

    assert len(instances) == 1
    kwargs = instances[0].kwargs
    assert kwargs["on_toggle"] is dummy.request_toggle
    assert kwargs["on_settings"] is dummy.open_settings
    assert kwargs["on_quit"] is dummy.request_quit
    assert kwargs["on_test"] is dummy.set_test_state
    assert kwargs["options"] is dummy._options

    kwargs["on_brain_unload"]()
    kwargs["on_brain_warm"]()
    assert brain_calls == ["brain_unload", "brain_warm"]


def test_maybe_start_tray_respects_show_tray_false(monkeypatch):
    from voicebridge.orb import options as opts_mod
    from voicebridge.orb import tray as tray_mod

    instances = []

    class _FakeTray:
        def __init__(self, **kwargs):
            instances.append(self)

        def start(self):
            return True

    monkeypatch.setattr(tray_mod, "OrbTray", _FakeTray)

    dummy = _dummy_orb(
        _options=opts_mod.OrbOptions(show_tray=False),
        _tray=None,
    )

    qa.QtVoiceOrb._maybe_start_tray(dummy)

    assert instances == []
