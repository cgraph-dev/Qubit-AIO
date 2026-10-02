"""Wave B2 Task 3 (backend only): /qubit-chat PC actions via the shared local path.

Scope: `voicebridge.local.actions.match` / `execute` only. The
`chat_transport` `local_action` branch is Wave C's — this file must NOT
import or drive `chat_transport`.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from voicebridge.local import actions as LA


def test_open_app_knows_editors_and_ides():
    for app in ("vscode", "cursor", "chromium", "terminal"):
        m = LA.match(f"open {app}")
        assert m is not None and m.name == "open_app", app


def test_open_app_knows_file_manager():
    for phrase in ("open file-manager", "open file manager"):
        m = LA.match(phrase)
        assert m is not None and m.name == "open_app", phrase
        assert m.params.get("app") == "files", m.params


@pytest.mark.parametrize("phrase,launcher", [
    ("open vscode", "code"),
    ("open cursor", "cursor"),
    ("open chromium", "chromium"),
    ("open terminal", "gnome-terminal"),
    ("open file-manager", "xdg-open"),
])
def test_open_app_execute_uses_launcher_mocked(monkeypatch, phrase, launcher):
    calls = []
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda argv, **kw: (calls.append(argv), _DummyProc())[1],
    )
    m = LA.match(phrase)
    assert m is not None and m.name == "open_app", phrase
    out = LA.execute(m)
    assert calls and calls[0][0] == launcher, (phrase, calls)
    assert "opening" in out.lower(), out


def test_open_app_unknown_launcher_is_honest():
    m = LA.ActionMatch(name="open_app", params={"app": "no-such-app"}, text="open no-such-app")
    out = LA.execute(m)
    assert "don't have a launcher" in out, out


class _DummyProc:
    pass


def test_open_text_document_routes_to_open_document():
    # "Open a text document." matched NOTHING (fell to the free-form agent,
    # which narrated a phantom "I will assist you..."). A document is not
    # an app: it must never match open_app.
    for phrase in ("open a text document", "open a txt file",
                   "open a new document so i can write"):
        m = LA.match(phrase)
        assert m is not None and m.name == "open_document", (phrase, m)


def test_create_text_file_routes_to_create_document():
    for phrase in ("create a new text file",
                   "create and open a new text file so i can write something",
                   "make me a txt note", "create a text document"):
        m = LA.match(phrase)
        assert m is not None and m.name == "create_document", (phrase, m)


def test_create_document_takes_an_optional_name():
    m = LA.match("create a txt note called shopping list")
    assert m is not None and m.name == "create_document", m
    assert "shopping" in (m.params.get("name") or "").lower(), m.params


def test_create_document_creates_and_opens(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda argv, **kw: (calls.append(argv), _DummyProc())[1])
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    m = LA.match("create and open a new text file so i can write something")
    assert m is not None and m.name == "create_document"
    out = LA.execute(m)
    made = list((tmp_path / "Documents").glob("*.txt"))
    assert len(made) == 1, (made, out)
    assert made[0].name in out, out
    assert calls, "the editor must actually open"


def test_open_document_without_name_opens_editor(monkeypatch):
    calls = []
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda argv, **kw: (calls.append(argv), _DummyProc())[1])
    m = LA.match("open a text document")
    assert m is not None and m.name == "open_document"
    out = LA.execute(m)
    assert calls, "the editor must actually open"
    assert "open" in out.lower(), out


def test_document_actions_do_not_shadow_apps_or_projects():
    assert LA.match("open vscode").name == "open_app"
    assert LA.match("open chromium").name == "open_app"


def test_open_website_routes():
    for phrase, url in (
        ("open youtube", "https://www.youtube.com"),
        ("open github.com", "https://github.com"),
        ("go to gmail.com", "https://mail.google.com"),
        ("visit dexonline.ro", "https://dexonline.ro"),
        ("open https://example.com/page", "https://example.com/page"),
    ):
        m = LA.match(phrase)
        assert m is not None and m.name == "open_website", (phrase, m)
        assert m.params.get("url") == url, (phrase, m.params)


def test_open_website_executes_browser(monkeypatch):
    calls = []
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda argv, **kw: (calls.append(argv), _DummyProc())[1])
    m = LA.match("open github.com")
    out = LA.execute(m)
    assert calls and calls[0][:2] == ["xdg-open", "https://github.com"], calls
    assert "github.com" in out, out


def test_open_website_refuses_non_http():
    for phrase in ("open file:///etc/passwd",
                   "open javascript:alert(1)"):
        m = LA.match(phrase)
        if m is not None and m.name == "open_website":
            out = LA.execute(m)
            assert "can't open" in out.lower() or "refus" in out.lower(), out


def test_website_action_does_not_shadow_apps_or_docs():
    assert LA.match("open discord").name == "open_app"
    assert LA.match("open a text document").name == "open_document"
    assert LA.match("open file manager").name == "open_app"


# --- Browser-tab voice control (local actions over browser_tabs) -----------
from voicebridge import browser_tabs as BT


class _FakeCfg:
    """Minimal cfg double: state_dir() for the tab ledger, get() for reads."""

    def __init__(self, tmp):
        self._tmp = tmp

    def state_dir(self):
        return self._tmp

    def get(self, *path, default=None):
        return default


def _stub_tabs_cfg(monkeypatch, tmp_path):
    monkeypatch.setattr(LA, "_tabs_cfg", lambda: _FakeCfg(tmp_path))


_PAGES = [
    {"id": "AAA", "title": "Inbox — Mail",
     "url": "https://mail.example.com/inbox", "type": "page"},
    {"id": "BBB", "title": "GitHub",
     "url": "https://github.com/", "type": "page"},
    {"id": "CCC", "title": "New Tab", "url": "about:blank", "type": "page"},
]


def _cdp_down(*a, **k):
    raise BT.BrowserTabsError("CDP_UNAVAILABLE", "no loopback CDP endpoint")


def test_tab_close_routes():
    for phrase in ("close this tab", "close the tab", "close tab",
                   "close current tab", "close the inbox tab",
                   "close the tab called inbox"):
        m = LA.match(phrase)
        assert m is not None and m.name == "tab_close", (phrase, m)


def test_tab_duplicate_routes():
    for phrase in ("duplicate this tab", "duplicate the inbox tab",
                   "duplicate the current tab"):
        m = LA.match(phrase)
        assert m is not None and m.name == "tab_duplicate", (phrase, m)


def test_tab_new_routes():
    for phrase in ("new tab", "open a new tab", "open new tab",
                   "please open a new tab", "create a new tab"):
        m = LA.match(phrase)
        assert m is not None and m.name == "tab_new", (phrase, m)


def test_tab_switch_routes():
    for phrase in ("switch to the inbox tab", "switch to inbox tab",
                   "go to the github tab", "switch to github",
                   "bring the inbox tab to front", "focus the inbox tab"):
        m = LA.match(phrase)
        assert m is not None and m.name == "tab_switch", (phrase, m)


def test_tab_switch_extracts_target():
    m = LA.match("switch to the inbox tab")
    assert m.params.get("target") == "inbox", m.params
    m = LA.match("switch to github")
    assert m.params.get("target") == "github", m.params


def test_tab_list_routes():
    for phrase in ("what tabs are open", "what tabs", "list my tabs",
                   "list open tabs", "list tabs", "show me my tabs",
                   "how many tabs are open", "which tabs are open"):
        m = LA.match(phrase)
        assert m is not None and m.name == "tab_list", (phrase, m)


def test_tab_switch_does_not_steal_url_intents():
    # Both directions pinned: TLD present -> website; tab word -> tab_switch.
    m = LA.match("go to github.com")
    assert m is not None and m.name == "open_website", m
    assert m.params.get("url") == "https://github.com", m.params
    m = LA.match("go to the github tab")
    assert m is not None and m.name == "tab_switch", m


def test_tab_actions_do_not_shadow_apps_docs_window_or_music():
    # No shadowing either way.
    assert LA.match("open discord").name == "open_app"
    assert LA.match("open chromium").name == "open_app"
    assert LA.match("open a text document").name == "open_document"
    assert LA.match("open youtube").name == "open_website"
    assert LA.match("close this window").name == "window_close"
    assert LA.match("close the window").name == "window_close"
    assert LA.match("close this tab").name == "tab_close"
    assert LA.match("stop the music").name == "youtube_stop"
    assert LA.match("close the youtube tabs").name == "youtube_stop"
    assert LA.match("show me the system stats").name == "system_stats"
    assert LA.match("play lofi in a new tab").name == "play_youtube"


def test_tab_close_executes_by_resolved_name(monkeypatch, tmp_path):
    _stub_tabs_cfg(monkeypatch, tmp_path)
    calls = {}

    def _resolve(cfg, target=""):
        assert target == "inbox", target
        return dict(_PAGES[0])

    def _close(cfg, target, **kw):
        calls["target"] = target
        calls["confirmed"] = kw.get("confirmed")
        calls["by"] = kw.get("requested_by")
        return {"state": "OK", "target": target, "closed": True}

    monkeypatch.setattr(BT, "_resolve_target", _resolve)
    monkeypatch.setattr(BT, "close", _close)
    m = LA.match("close the inbox tab")
    assert m.name == "tab_close"
    out = LA.execute(m)
    assert calls == {"target": "AAA", "confirmed": True,
                     "by": "voice:user"}, calls
    assert "closed" in out.lower() and "inbox" in out.lower(), out


def test_tab_close_empty_name_resolves_current(monkeypatch, tmp_path):
    _stub_tabs_cfg(monkeypatch, tmp_path)
    seen = {}

    def _resolve(cfg, target=""):
        seen["target"] = target
        return dict(_PAGES[1])

    monkeypatch.setattr(BT, "_resolve_target", _resolve)
    monkeypatch.setattr(BT, "close",
                        lambda cfg, target, **kw: {"state": "OK",
                                                  "target": target,
                                                  "closed": True})
    m = LA.match("close this tab")
    assert m.name == "tab_close"
    assert (m.params.get("target") or "") == "", m.params
    out = LA.execute(m)
    assert seen == {"target": ""}, seen
    assert "closed" in out.lower() and "github" in out.lower(), out


def test_tab_close_miss_is_honest(monkeypatch, tmp_path):
    _stub_tabs_cfg(monkeypatch, tmp_path)

    def _resolve(cfg, target=""):
        raise BT.BrowserTabsError(BT.NOT_FOUND_CODE,
                                  "no open tab matches 'nope'")

    monkeypatch.setattr(BT, "_resolve_target", _resolve)
    closed = []
    monkeypatch.setattr(BT, "close",
                        lambda cfg, target, **kw: (closed.append(target),
                                                  {"state": "OK"})[1])
    out = LA.execute(LA.match("close the nope tab"))
    assert closed == [], closed
    assert "couldn't find" in out.lower() and "nope" in out.lower(), out


def test_tab_close_ambiguity_is_fail_closed(monkeypatch, tmp_path):
    _stub_tabs_cfg(monkeypatch, tmp_path)
    closed = []
    monkeypatch.setattr(BT, "close",
                        lambda cfg, target, **kw: (closed.append(target),
                                                  {"state": "OK"})[1])

    def _resolve(cfg, target=""):
        raise BT.BrowserTabsError(BT.BAD_INPUT_CODE,
                                  "tab reference matches 2 tabs "
                                  "(Inbox — Mail, Inbox — Archive); "
                                  "be more specific")

    monkeypatch.setattr(BT, "_resolve_target", _resolve)
    out = LA.execute(LA.match("close the inbox tab"))
    assert closed == [], closed
    assert "match" in out.lower(), out


def test_tab_close_cdp_down_is_honest(monkeypatch, tmp_path):
    _stub_tabs_cfg(monkeypatch, tmp_path)
    monkeypatch.setattr(BT, "_resolve_target", _cdp_down)
    closed = []
    monkeypatch.setattr(BT, "close",
                        lambda cfg, target, **kw: (closed.append(target),
                                                  {"state": "OK"})[1])
    out = LA.execute(LA.match("close this tab"))
    assert closed == [], closed
    assert "browser isn't reachable" in out.lower(), out


def test_tab_close_all_is_a_onetime_refusal(monkeypatch, tmp_path):
    _stub_tabs_cfg(monkeypatch, tmp_path)
    closed = []
    monkeypatch.setattr(BT, "close",
                        lambda cfg, target, **kw: (closed.append(target),
                                                  {"state": "OK"})[1])
    m = LA.match("close all tabs")
    assert m is not None and m.name == "tab_close", m
    out = LA.execute(m)
    assert closed == [], closed
    assert "one tab at a time" in out.lower(), out


def test_tab_duplicate_opens_same_url(monkeypatch, tmp_path):
    _stub_tabs_cfg(monkeypatch, tmp_path)
    monkeypatch.setattr(BT, "_resolve_target",
                        lambda cfg, target="": dict(_PAGES[1]))
    calls = {}

    def _open(cfg, url, **kw):
        calls["url"] = url
        calls["confirmed"] = kw.get("confirmed")
        return {"state": "OK", "target": "NEW1", "url": url}

    monkeypatch.setattr(BT, "open", _open)
    out = LA.execute(LA.match("duplicate the github tab"))
    assert calls.get("url") == "https://github.com/", calls
    assert calls.get("confirmed") is True, calls
    assert "duplicat" in out.lower() and "github" in out.lower(), out


def test_tab_duplicate_blank_tab_is_honest(monkeypatch, tmp_path):
    _stub_tabs_cfg(monkeypatch, tmp_path)
    monkeypatch.setattr(BT, "_resolve_target",
                        lambda cfg, target="": dict(_PAGES[2]))

    def _open(cfg, url, **kw):
        raise BT.BrowserTabsError(BT.BAD_INPUT_CODE,
                                  "only http(s) URLs may be opened")

    monkeypatch.setattr(BT, "open", _open)
    out = LA.execute(LA.match("duplicate this tab"))
    assert "no web address" in out.lower(), out


def test_tab_new_opens_blank_tab(monkeypatch, tmp_path):
    _stub_tabs_cfg(monkeypatch, tmp_path)
    calls = {}
    import voicebridge.cdp as _cdp

    def _put(method, path, **kw):
        calls["method"] = method
        calls["path"] = path
        calls["query"] = kw.get("query", None)
        return {"id": "NEW1", "title": "New Tab", "url": "about:blank",
                "type": "page"}

    monkeypatch.setattr(_cdp, "http_json", _put)
    monkeypatch.setattr(BT, "estop_engaged", lambda cfg: False)
    remembered = {}
    monkeypatch.setattr(BT, "_remember",
                        lambda cfg, tid: remembered.setdefault("tid", tid))
    out = LA.execute(LA.match("open a new tab"))
    assert calls.get("method") == "PUT" and calls.get("path") == "/json/new", \
        calls
    assert "url=" not in str(calls.get("query")), calls
    assert remembered.get("tid") == "NEW1", remembered
    assert "new blank tab" in out.lower(), out


def test_tab_new_cdp_down_is_honest(monkeypatch, tmp_path):
    _stub_tabs_cfg(monkeypatch, tmp_path)
    import voicebridge.cdp as _cdp

    def _put(method, path, **kw):
        raise _cdp.CdpError(_cdp.UNAVAILABLE_CODE, "no loopback CDP endpoint")

    monkeypatch.setattr(_cdp, "http_json", _put)
    monkeypatch.setattr(BT, "estop_engaged", lambda cfg: False)
    out = LA.execute(LA.match("new tab"))
    assert "browser isn't reachable" in out.lower(), out


def test_tab_switch_activates_by_name(monkeypatch, tmp_path):
    _stub_tabs_cfg(monkeypatch, tmp_path)
    monkeypatch.setattr(BT, "_resolve_target",
                        lambda cfg, target="": dict(_PAGES[0]))
    calls = {}

    def _activate(cfg, target, **kw):
        calls["target"] = target
        calls["confirmed"] = kw.get("confirmed")
        return {"state": "OK", "target": target, "activated": True}

    monkeypatch.setattr(BT, "activate", _activate)
    m = LA.match("switch to the inbox tab")
    assert m.name == "tab_switch"
    out = LA.execute(m)
    assert calls == {"target": "AAA", "confirmed": True}, calls
    assert "switch" in out.lower() and "inbox" in out.lower(), out


def test_tab_switch_without_name_asks_which(monkeypatch, tmp_path):
    _stub_tabs_cfg(monkeypatch, tmp_path)
    activated = []
    monkeypatch.setattr(BT, "activate",
                        lambda cfg, target, **kw: (activated.append(target),
                                                  {"state": "OK"})[1])
    out = LA.execute(LA.match("switch to the tab"))
    assert activated == [], activated
    assert "which tab" in out.lower(), out


def test_tab_switch_cdp_down_is_honest(monkeypatch, tmp_path):
    _stub_tabs_cfg(monkeypatch, tmp_path)
    monkeypatch.setattr(BT, "_resolve_target", _cdp_down)
    out = LA.execute(LA.match("switch to the inbox tab"))
    assert "browser isn't reachable" in out.lower(), out


def test_tab_list_speaks_titles(monkeypatch, tmp_path):
    _stub_tabs_cfg(monkeypatch, tmp_path)
    monkeypatch.setattr(BT, "tabs",
                        lambda cfg: {"state": "OK", "tabs": list(_PAGES),
                                     "current_target": "AAA"})
    out = LA.execute(LA.match("what tabs are open"))
    assert "3 tabs" in out.lower(), out
    assert "inbox" in out.lower() and "github" in out.lower(), out


def test_tab_list_empty_is_honest(monkeypatch, tmp_path):
    _stub_tabs_cfg(monkeypatch, tmp_path)
    monkeypatch.setattr(BT, "tabs",
                        lambda cfg: {"state": "OK", "tabs": [],
                                     "current_target": ""})
    out = LA.execute(LA.match("list my tabs"))
    assert "no browser tabs" in out.lower(), out


def test_tab_list_cdp_down_is_honest(monkeypatch, tmp_path):
    _stub_tabs_cfg(monkeypatch, tmp_path)
    monkeypatch.setattr(BT, "tabs",
                        lambda cfg: {"state": "UNAVAILABLE",
                                     "code": "BROWSER_UNAVAILABLE",
                                     "detail": "no loopback CDP endpoint"})
    out = LA.execute(LA.match("list tabs"))
    assert "browser isn't reachable" in out.lower(), out


def test_no_cross_steals_volume_vs_brightness():
    # P0: bare "turn up/down" in the volume patterns stole brightness intents.
    assert LA.match("turn up the brightness").name == "brightness_up"
    assert LA.match("turn down brightness").name == "brightness_down"
    assert LA.match("turn the volume up").name == "volume_up"
    assert LA.match("turn it up").name == "volume_up"
    assert LA.match("turn the volume down").name == "volume_down"


def test_no_cross_steals_play_vs_devices_and_timers():
    # P0: bare "turn on" / "start a" play verbs stole bluetooth + timer.
    assert LA.match("turn on bluetooth").name == "bluetooth_toggle"
    assert LA.match("start a timer").name == "timer"
    assert LA.match("remind me in 10 minutes").name == "timer"
    assert LA.match("remind me to call mom").name == "quick_note"
    assert LA.match("play lofi hip hop").name == "play_youtube"
    assert LA.match("put on some jazz").name == "play_youtube"
    assert LA.match("play next song videos").name == "play_youtube"


def test_natural_phrasing_routing():
    cases = {
        "make it louder": "volume_up",
        "make it quieter": "volume_down",
        "boost the audio": "volume_up",
        "mute": "volume_mute",
        "microphone mute": "mic_mute",
        "mute my mic": "mic_mute",
        "screenshot": "screenshot",
        "show clipboard": "clipboard",
        "take out the trash": "empty_trash",
        "open chrome": "open_app",
        "open firefox": "open_app",
        "dupe this tab": "tab_duplicate",
        "open a new blank tab": "tab_new",
        "switch tabs": "tab_switch",
        "skip this track": "youtube_next",
        "find recipe file": "find_file",
        "how is the system": "system_stats",
        "open the shopping list document": "open_document",
        "open my notes file": "open_document",
        "take a note remember to buy milk": "quick_note",
    }
    for phrase, want in cases.items():
        m = LA.match(phrase)
        assert m is not None and m.name == want, (phrase, m)


def test_named_document_open_extracts_name():
    m = LA.match("open the shopping list document")
    assert m is not None and m.name == "open_document", m
    assert "shopping" in (m.params.get("name") or ""), m.params


def test_documents_open_in_local_editor_not_vscode(monkeypatch, tmp_path):
    # Owner ruling: the OS .txt editor opens documents; VS Code only when
    # explicitly named — even when `code` is installed.
    calls = []
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda argv, **kw: (calls.append(argv), _DummyProc())[1])
    monkeypatch.setattr(shutil, "which",
                        lambda name: ("/usr/bin/" + name)
                        if name in ("code", "gnome-text-editor") else None)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    m = LA.match("open a text document")
    assert m is not None and m.name == "open_document"
    assert m.params.get("editor", "") != "vscode"
    LA.execute(m)
    assert calls and calls[0][0] == "gnome-text-editor", calls


def test_documents_open_in_vscode_when_named(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda argv, **kw: (calls.append(argv), _DummyProc())[1])
    monkeypatch.setattr(shutil, "which",
                        lambda name: "/usr/bin/code" if name == "code" else None)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    m = LA.match("open a text document in vscode")
    assert m is not None and m.name == "open_document", m
    assert m.params.get("editor") == "vscode", m.params
    LA.execute(m)
    assert calls and calls[0][0] == "code", calls
