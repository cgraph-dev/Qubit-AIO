"""Voice site shortcuts: open Instagram/Messenger/Gmail/opencode/YouTube/GitHub/dashboard."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.local.actions import match
from voicebridge.router.l0 import route


def _url(phrase: str) -> str:
    hit = match(phrase)
    assert hit is not None, phrase
    assert hit.name == "open_website", (phrase, hit.name)
    return hit.params["url"]


def test_account_site_shortcuts_route_to_open_website():
    assert _url("open instagram") == "https://www.instagram.com"
    assert _url("open messenger") == "https://www.messenger.com"
    assert _url("open gmail") == "https://mail.google.com"
    assert _url("open github") == "https://github.com"


def test_youtube_music_is_not_plain_youtube():
    assert _url("open youtube music") == "https://music.youtube.com"
    assert _url("open youtube") == "https://www.youtube.com"


def test_opencode_shortcuts():
    assert _url("open opencode") == "https://opencode.ai"
    assert _url("open opencode.ai") == "https://opencode.ai"


def test_qubit_dashboard_shortcut():
    assert _url("open qubit in browser") == "http://127.0.0.1:8890/"
    assert _url("open qubit dashboard") == "http://127.0.0.1:8890/"
    assert _url("open qubit web dashboard") == "http://127.0.0.1:8890/"


def test_site_shortcuts_are_l0_local_actions():
    for phrase in ("open instagram", "open messenger", "open gmail",
                   "open opencode.ai", "open youtube music", "open youtube",
                   "open github", "open qubit in browser"):
        decision = route(phrase)
        assert decision is not None, phrase
        assert decision.kind == "local", (phrase, decision.kind)
        assert decision.params.get("action") == "open_website", phrase
