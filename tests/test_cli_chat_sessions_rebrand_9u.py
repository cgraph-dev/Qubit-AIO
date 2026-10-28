"""CLI-REBRAND — ``qubit chat-sessions`` must render upstream product names as
Qubit, exactly like the dashboard, without rewriting stored bytes.

The canonical chat store (``state.db``) is durable history and may hold legacy
upstream-authored text. The dashboard already rebrands ``name``/``title``/
``preview`` and message ``content``/``meta`` at the served boundary
(``voicebridge.qubit_compat``). This test pins the CLI to the same boundary
helpers so both surfaces agree for identical data, while path fields such as
``cwd`` and the stored transcript stay byte-exact.

Hermetic: isolated XDG dirs + ``QUBIT_HOME``, no network, no live services.
"""
from __future__ import annotations

import argparse
import json

from voicebridge import chat_transport as CT

TITLE = "Fix Hermes TUI v-flag regex error"
PREVIEW = "Recent shared history (same across OpenClaw/Hermes/browser/local)"


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    home = tmp_path / "home" / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(home))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seed(cfg):
    store = CT.store(cfg)
    sid = store.create("", session_id="s-rebrand")["session"]["id"]
    # ``rename`` writes the searchable ``title`` column (the vendored summary
    # search matches ``title``, not ``display_name``).
    store.rename(sid, TITLE)
    # The list preview is the first user message, so seed the upstream preview
    # text there; the assistant row carries upstream text in content + meta.
    store.append(sid, "user", PREVIEW)
    store.append(sid, "assistant", "OpenClaw says hi",
                  meta={"tool_name": "Hermes helper"})
    return store, sid


def _run_cli(cfg, capsys, **ns):
    args = argparse.Namespace(**ns)
    from voicebridge import cli as CLI

    rc = CLI._cmd_chat_sessions(cfg, args)
    assert rc == 0
    return json.loads(capsys.readouterr().out)


def _no_upstream(text):
    low = text.lower()
    return "hermes" not in low and "openclaw" not in low


# ---------------------------------------------------------------- list / show
def test_cli_list_and_show_rebrand_served_fields(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, monkeypatch)
    store, sid = _seed(cfg)

    listed = _run_cli(cfg, capsys, chat_sessions_command="list", limit=20,
                      archived=False)
    row = next(r for r in listed["sessions"] if r["id"] == sid)
    # The upstream session title/preview render as Qubit in the CLI...
    assert row["title"] == "Fix Qubit TUI v-flag regex error", row
    assert row["name"] == "Fix Qubit TUI v-flag regex error", row
    assert row["preview"].startswith(
        "Recent shared history (same across Qubit/Qubit/browser/l"), row
    assert _no_upstream(json.dumps(listed))

    shown = _run_cli(cfg, capsys, chat_sessions_command="show",
                     session_id=sid)
    assert shown["state"] == "OK"
    assert shown["session"]["name"] == "Fix Qubit TUI v-flag regex error"
    contents = [m["content"] for m in shown["messages"]]
    assert "Qubit says hi" in contents, contents
    assert _no_upstream(json.dumps(shown))

    # ...but the store bytes are never rewritten: the transcript authority
    # still holds the original upstream-authored text.
    raw = CT.store(cfg).show(sid)
    assert raw["session"]["name"] == TITLE
    assert [m["content"] for m in raw["messages"]] == [PREVIEW,
                                                       "OpenClaw says hi"]
    assert raw["messages"][1]["meta"]["tool_name"] == "Hermes helper"


# ------------------------------------------------------ messages / export
def test_cli_messages_and_export_rebrand_content_and_meta(tmp_path,
                                                          monkeypatch, capsys):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)

    msgs = _run_cli(cfg, capsys, chat_sessions_command="messages",
                    session_id="s-rebrand", limit=100)
    assert msgs["state"] == "OK"
    assert _no_upstream(json.dumps(msgs))
    assistant = next(m for m in msgs["messages"] if m["role"] == "assistant")
    assert assistant["content"] == "Qubit says hi"
    assert assistant["meta"]["tool_name"] == "Qubit helper"

    exported = _run_cli(cfg, capsys, chat_sessions_command="export",
                        session_id="s-rebrand")
    assert exported["state"] == "OK"
    assert exported["session"]["name"] == "Fix Qubit TUI v-flag regex error"
    assert _no_upstream(json.dumps(exported))

    # Search goes through the same store.list shape and is rebranded too.
    # The query matches the RAW stored title (the store is queried before the
    # served-boundary rebrand), and the emitted row is still Qubit-only.
    found = _run_cli(cfg, capsys, chat_sessions_command="search", limit=20,
                     query="Hermes")
    assert found["total"] >= 1, found
    assert _no_upstream(json.dumps(found))


# ------------------------------------------- path fields are NOT rewritten
def test_helper_only_rebrands_served_fields_and_keeps_paths():
    from voicebridge import cli as CLI

    out = {
        "state": "OK",
        "sessions": [{
            "id": "s1",
            "name": "Hermes run",
            "title": "OpenClaw run",
            "preview": "via Hermes",
            "cwd": "/home/trick/.hermes",
            "path": "/home/trick/.hermes/state.db",
            "model": "qubit-brain",
        }],
        "session": {
            "id": "s1", "name": "Fix Hermes", "cwd": "/home/trick/.hermes",
        },
        "messages": [{
            "role": "assistant",
            "content": "OpenClaw says hi",
            "meta": {"reasoning": "Hermes thought"},
        }],
    }
    rebranded = CLI._rebrand_chat_sessions_cli(out)

    row = rebranded["sessions"][0]
    assert row["name"] == "Qubit run"
    assert row["title"] == "Qubit run"
    assert row["preview"] == "via Qubit"
    # Real paths must stay intact — only the served identity fields change.
    assert row["cwd"] == "/home/trick/.hermes"
    assert row["path"] == "/home/trick/.hermes/state.db"
    assert row["model"] == "qubit-brain"

    assert rebranded["session"]["name"] == "Fix Qubit"
    assert rebranded["session"]["cwd"] == "/home/trick/.hermes"

    msg = rebranded["messages"][0]
    assert msg["content"] == "Qubit says hi"
    assert msg["meta"]["reasoning"] == "Qubit thought"

    # Non-dict inputs pass through untouched.
    assert CLI._rebrand_chat_sessions_cli(None) is None
    assert CLI._rebrand_chat_sessions_cli("raw") == "raw"
