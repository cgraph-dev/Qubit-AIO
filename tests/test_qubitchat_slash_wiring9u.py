"""Qubit-chat slash wiring (item 1: composer offers == turn executes).

The ``/qubit-chat`` composer offers the static registry catalog plus the live
``GET /api/chat/slash-commands`` catalog (skills/plugins/MCP/toolsets — the
"hundreds of commands"), and the palette sends completion text. Every offered
token must EXECUTE on the turn path (real backend, honest dashboard guidance,
or honest catalog miss) — never "Unknown slash command".

* registry names + aliases parse known and answer guidance naming the verb
  (never unknown, never phantom execution);
* composer conveniences resolve (``/models`` -> ``/model``; ``/mcp`` stays its
  own real verb);
* ``/skill-*`` / ``/plugin-*`` / ``/mcp-*`` / ``/toolset-*`` parse and execute
  against the same authorities the catalog reads;
* genuinely unknown tokens still answer honest help naming the miss;
* mid-sentence inline matching stays import-free and never matches paths/URLs.

Hermetic: isolated XDG dirs; no network. The vendored registry import is the
same lazy path the catalog handler uses.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_registry_verbs_all_parse_known():
    """Every CLI-registry name+alias parses (never unknown)."""
    from voicebridge import chat_transport as CT

    names, aliases, _desc = CT._registry_slash_map()
    assert len(names) > 100, f"registry read failed: {len(names)} names"
    unknown = []
    for verb in sorted(names) + sorted(aliases):
        parsed = CT.parse_qubitchat_slash(f"/{verb}")
        if parsed is None or parsed.get("command") == "unknown":
            unknown.append(verb)
    assert not unknown, f"registry verbs parse unknown: {unknown}"


def test_registry_aliases_resolve_canonical():
    from voicebridge import chat_transport as CT

    assert CT.parse_qubitchat_slash("/compact")["command"] == "compress"
    assert CT.parse_qubitchat_slash("/tasks")["command"] == "agents"
    assert CT.parse_qubitchat_slash("/models")["command"] == "model"
    # /mcp is its own dashboard verb (real server list), not an alias.
    assert CT.parse_qubitchat_slash("/mcp")["command"] == "mcp"


def test_registry_guidance_names_verb_and_runs_nowhere_phantom(
        tmp_path, monkeypatch):
    """Guidance answers ok with the verb named; never claims execution."""
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    for verb in ("/clear", "/new", "/quit", "/palette", "/login"):
        res = CT.run_qubitchat_slash(cfg, verb)
        assert res.get("ok") is True, (verb, res)
        text = str(res.get("text") or "")
        assert verb in text, (verb, text[:120])
        assert res.get("route") == "slash"


def test_catalog_prefixes_parse_and_execute(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    for token in ("/skill-antigravity-cli", "/skill-nope",
                  "/plugin-qubit-boards", "/plugin-nope",
                  "/toolset-web"):
        parsed = CT.parse_qubitchat_slash(token)
        assert parsed is not None and parsed.get("command") != "unknown", token
        res = CT.run_qubitchat_slash(cfg, token)
        assert res.get("ok") is True, (token, res)
        assert res.get("route") == "slash", (token, res)
    miss = CT.run_qubitchat_slash(cfg, "/skill-nope")
    assert "no skill" in str(miss.get("text") or "").lower()
    assert "/skills" in str(miss.get("text") or "")


def test_unknown_still_answers_honest_help(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    assert CT.parse_qubitchat_slash("/frobnicate")["command"] == "unknown"
    assert CT.parse_qubitchat_slash("/tmp/x")["command"] == "unknown"
    res = CT.run_qubitchat_slash(cfg, "/frobnicate")
    assert res.get("ok") is True
    assert "/frobnicate" in str(res.get("text") or "")


def test_inline_matches_tuple_conveniences_and_prefixes():
    from voicebridge import chat_transport as CT

    assert CT.extract_inline_slash("please run /model here")["command"] == \
        "model"
    inline = CT.extract_inline_slash("try /skill-blackbox now")
    assert inline is not None and inline["command"] == "skill-blackbox"
    assert CT.extract_inline_slash("see https://x/y and /tmp/z") is None
    assert CT.extract_inline_slash("run /model then /skills") is None
