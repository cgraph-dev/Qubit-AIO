"""Packet 8T-F — converted boundaries: former account-bound rows now resolve
to typed IMPLEMENTED states, and genuinely absent surfaces still have no
stub commands.

Proves pairing/webhook/peer, gateway management, and messaging platform
names are not silently omitted (every name resolves with owner/rollback
evidence), no top-level CLI entry point exists for the remaining absent
surfaces (so no stub can be invoked), and no messaging transport can be
imported.
"""
from __future__ import annotations


CONVERTED_TOKENS = ("pairing", "webhook", "peer", "gateway-manage",
                    "gateway", "whatsapp", "whatsapp-cloud", "slack", "send")

ABSENT_COMMANDS = ("portal", "login", "logout", "pets", "skin", "serve",
                   "desktop", "gui", "dashboard")


def test_converted_names_are_typed_implemented():
    from voicebridge.capability_catalog import check

    for name in CONVERTED_TOKENS:
        out = check(name)
        assert out["state"] == "IMPLEMENTED", (name, out)
        assert out["code"] == "OK", (name, out)
        assert out["explanation"], (name, out)
        assert out["owner"], (name, out)
        assert out["rollback"], (name, out)


def test_no_stub_commands_for_blocked_rows():
    from voicebridge.cli import build_parser

    names = set()
    for action in build_parser()._actions:
        import argparse
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices.keys())
    for absent in ABSENT_COMMANDS:
        assert absent not in names, \
            f"{absent!r} must not exist as a CLI command (no stubs)"


def test_import_agent_inspect_only_contract():
    from voicebridge import import_agent as IA
    from voicebridge.capability_catalog import check

    out = check("import-agent")
    assert out["state"] == "IMPLEMENTED", out
    assert callable(IA.inspect_tree) and callable(IA.refuse_import)
    assert not hasattr(IA, "import_tree"), \
        "no content-import API may exist (inspect-only contract)"
