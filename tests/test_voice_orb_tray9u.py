"""System-tray controller for the voice orb (voicebridge/orb/tray.py).

Hermetic: no display and no Qt needed. The pure helpers
(``state_labels``, ``menu_spec``, ``invoke``) are exercised directly;
``OrbTray`` is proven fail-soft on a tray that is never started.
"""
from __future__ import annotations


def test_tray_imports_cleanly_without_qt_or_display():
    import voicebridge.orb.tray as tray
    assert callable(tray.state_labels)
    assert callable(tray.menu_spec)
    assert callable(tray.invoke)
    assert tray.OrbTray is not None


def test_state_labels_exact_six_in_order():
    from voicebridge.orb.tray import state_labels
    assert state_labels() == ("idle", "listening", "thinking",
                              "working", "speaking", "error")


def test_menu_spec_ids_labels_kinds_and_lock_check():
    from voicebridge.orb import options as O
    from voicebridge.orb.tray import menu_spec
    spec = menu_spec()
    assert [i["id"] for i in spec] == ["toggle", "lock", "settings",
                                       "test", "quit"]
    assert [i["label"] for i in spec] == ["Show orb", "Lock position",
                                          "Settings\u2026", "Test animation",
                                          "Quit orb"]
    assert [i["kind"] for i in spec] == ["action", "check", "action",
                                         "submenu", "action"]
    assert spec[1]["checked"] is False
    assert menu_spec(O.OrbOptions(locked=True))[1]["checked"] is True
    # None/garbage options fail closed to unchecked, never raise
    assert menu_spec(None)[1]["checked"] is False
    assert menu_spec(object())[1]["checked"] is False


def test_menu_spec_test_submenu_covers_all_states():
    from voicebridge.orb.tray import menu_spec, state_labels
    spec = menu_spec()
    test_item = next(i for i in spec if i["id"] == "test")
    assert test_item["kind"] == "submenu"
    children = test_item["children"]
    assert [c["id"] for c in children] == ["test:" + s for s in state_labels()]
    assert [c["label"] for c in children] == list(state_labels())
    assert all(c["kind"] == "action" for c in children)


def test_invoke_dispatches_each_id():
    from voicebridge.orb.tray import invoke
    calls = []

    def rec(name):
        def _cb(value=None):
            calls.append((name, value))
        return _cb

    cbs = {"toggle": rec("toggle"), "lock": rec("lock"),
           "settings": rec("settings"), "quit": rec("quit"),
           "test": rec("test")}
    assert invoke("toggle", cbs) is True
    assert invoke("lock", cbs, value=True) is True
    assert invoke("settings", cbs) is True
    assert invoke("quit", cbs) is True
    assert invoke("test:speaking", cbs) is True
    assert calls == [("toggle", None), ("lock", True), ("settings", None),
                     ("quit", None), ("test", "speaking")]


def test_invoke_accepts_constructor_callback_names():
    from voicebridge.orb.tray import invoke
    calls = []

    def _cb(value=None):
        calls.append(value)

    assert invoke("toggle", {"on_toggle": _cb}) is True
    assert invoke("test:idle", {"on_test": _cb}) is True
    assert calls == [None, "idle"]


def test_invoke_zero_arg_callbacks_still_dispatch():
    from voicebridge.orb.tray import invoke
    calls = []
    assert invoke("lock", {"lock": lambda: calls.append("lock")}, value=True) is True
    assert invoke("test:error", {"test": lambda: calls.append("test")}) is True
    assert calls == ["lock", "test"]


def test_invoke_unknown_id_is_false_never_raises():
    from voicebridge.orb.tray import invoke
    calls = []

    def _cb(*args):
        calls.append(args)

    assert invoke("bogus", {"toggle": _cb}) is False
    assert invoke("", {"toggle": _cb}) is False
    assert invoke("toggle", {}) is False
    assert invoke("toggle", None) is False
    assert invoke(None, {"toggle": _cb}) is False
    # bad callbacks never escape
    assert invoke("settings", {"settings": "not-callable"}) is False
    assert calls == []


def test_invoke_callback_exception_is_swallowed():
    from voicebridge.orb.tray import invoke

    def boom(*_args):
        raise RuntimeError("nope")

    assert invoke("toggle", {"toggle": boom}) is False
    assert invoke("test:idle", {"test": boom}) is False


def test_start_false_when_qt_unavailable(monkeypatch):
    from voicebridge.orb import tray
    monkeypatch.setattr(tray, "_import_qt", lambda: None)
    assert tray.OrbTray(on_toggle=lambda: None).start() is False


def test_start_does_not_raise_in_this_environment():
    from voicebridge.orb import tray
    t = tray.OrbTray()
    out = t.start()
    assert isinstance(out, bool)
    if out:
        t.stop()


def test_mutators_and_stop_never_raise_on_unstarted_tray():
    from voicebridge.orb import tray
    t = tray.OrbTray()
    assert t.stop() is None
    assert t.set_locked(True) is None
    assert t.set_visible_state(False) is None
    assert t.set_visible_state(True) is None
    assert t.set_locked(False) is None
