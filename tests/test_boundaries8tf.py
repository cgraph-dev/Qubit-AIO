"""Packet 8T-F — negative boundaries for account-bound BLOCKED rows.

Proves pairing/webhook/peer, gateway management, and messaging platform
rows are not silently omitted: every name resolves to a typed BLOCKED
state in the catalog, no top-level CLI entry point exists for them (so no
stub can be invoked), and no messaging transport can be imported.
"""
from __future__ import annotations


ACCOUNT_BOUND = {
    "pairing": "BLOCKED_PAIRING",
    "webhook": "BLOCKED_PAIRING",
    "peer": "BLOCKED_PAIRING",
    "gateway-manage": "BLOCKED_GATEWAY_MANAGE",
    "gateway": "BLOCKED_GATEWAY",
    "whatsapp": "BLOCKED_MESSAGING",
    "whatsapp-cloud": "BLOCKED_MESSAGING",
    "slack": "BLOCKED_MESSAGING",
    "send": "BLOCKED_MESSAGING",
}

ABSENT_COMMANDS = ("pairing", "webhook", "peer", "gateway", "whatsapp",
                   "whatsapp-cloud", "slack", "send", "portal", "login",
                   "logout", "pets", "skin", "serve",
                   "desktop", "gui", "dashboard")


def test_account_bound_names_are_typed_blocked():
    from voicebridge.capability_catalog import check

    for name, code in ACCOUNT_BOUND.items():
        out = check(name)
        assert out["state"] == "BLOCKED", (name, out)
        assert out["code"] == code, (name, out)
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
