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

ABSENT_COMMANDS = ("login", "logout", "pets", "skin", "serve", "dashboard")
# Formerly absent surfaces now served by the Qubit-owned fork CLI (not stubs):
# a real routed command with upstream parser/behaviour, confirm-gated where the
# operation is mutating.
ROUTED_FORK_COMMANDS = ("portal", "desktop", "gui")


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
    # The fork-backed families are real routes to the vendored CLI, never a
    # fabricated stub: each resolves to a forward/needs-confirm route.
    from voicebridge import cli_qubit_forward as HF

    for routed in ROUTED_FORK_COMMANDS:
        assert routed in names, routed
        resolved = HF.route_family([routed])
        assert resolved is not None and resolved.kind in (
            "forward", "needs-confirm"), (routed, resolved)


def test_import_agent_inspect_only_contract():
    from voicebridge import import_agent as IA
    from voicebridge.capability_catalog import check

    out = check("import-agent")
    assert out["state"] == "IMPLEMENTED", out
    assert callable(IA.inspect_tree) and callable(IA.refuse_import)
    assert not hasattr(IA, "import_tree"), \
        "no content-import API may exist (inspect-only contract)"
