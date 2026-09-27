"""Task 16b (v0.21.5 rebase) — close the last voice gap.

Verification (``prep/VERIFY_VOICE.md``) found exactly one capability with no
voice path: ``POST /api/dashboard/agent-plugins/activate`` — the no-id **bulk
remote** plugin activate. The whole ``/api/dashboard/agent-plugins/.*``
namespace is gated in ``qubit_compat`` (``_NO_REMOTE``), so there is no
spoken-safe backend; the honest fix is an audited ``voice_unsupported`` guard
that names the real, voiced counterpart (the id-scoped, confirm-gated
``qubit plugins enable/disable``). This test proves that guard resolves and
executes as a typed refusal — never a silent stub, never a fake success.

It also pins the 8 other ``unresolved`` inventory rows as false negatives whose
capability already has a real/guard voice path (and adds the two missing
``update apply`` / ``update rollback`` aliases).

Pure ``resolve()`` checks need no config; the execution check only exercises a
guard (no e-stop/journal interaction).
"""
from __future__ import annotations

import pytest

from voicebridge import voice_commands as VC

# The genuine gap fixed here: spoken forms -> the new guard.
ACTIVATE_PHRASES = (
    "activate plugin",
    "activate a plugin",
    "bulk activate plugins",
    "activate all plugins",
    "agent plugins activate",
)
ACTIVATE_GUARD = "guard_agent_plugins_activate"


def test_bulk_activate_voice_guard_resolves():
    for phrase in ACTIVATE_PHRASES:
        res = VC.resolve(phrase)
        assert res.kind == VC.MATCH, (phrase, res.kind, res.speech)
        assert res.entry is not None, phrase
        assert res.entry.id == ACTIVATE_GUARD, (phrase, res.entry.id)


def test_bulk_activate_guard_is_audited_refusal():
    entry = VC._lookup_entry(ACTIVATE_GUARD)
    assert entry is not None, ACTIVATE_GUARD
    assert entry.backend == "voice_unsupported"
    assert entry.backend in VC._BACKENDS

    ctx = VC.make_ctx(None, actor="test:user", session="task16b", source="user")
    out = VC.execute(ctx, VC.resolve("activate plugin"))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    result = out.receipt.get("result", {})
    assert result.get("state") == "refused", result
    assert result.get("code") == "VOICE_REMOTE_UNSUPPORTED", result
    assert result.get("op") == "agent_plugins_activate", result
    # Names the real, voiced, id-scoped counterpart.
    guidance = result.get("guidance", "")
    assert "plugins enable" in guidance, guidance
    assert "plugins disable" in guidance, guidance
    assert out.confirmation_id == "", "a refusal must stage nothing"


# The 8 rows verification classified ``unresolved`` are generator-intent
# defects: the capability already resolves through a differently-named
# real/guard entry. Pin that mapping so a future regression fails loudly.
FALSE_NEGATIVES = {
    # capability -> (spoken probe, expected entry id)
    "update_apply": ("update apply", "update_apply"),
    "update_rollback": ("update rollback", "update_rollback"),
    "chat_workspaces": ("chat workspaces", "chat_workspaces"),
    "guard_display_ws": ("desktop display stream", "guard_display_ws"),
    "guard_computer_use_screen_status":
        ("computer-use screen status", "guard_computer_use_screen_status"),
    "guard_plugins_adopt": ("plugins adopt", "guard_plugins_adopt"),
    "guard_plugins_check_updates":
        ("plugins check-updates", "guard_plugins_check_updates"),
    "guard_plugins_trust_update_url":
        ("plugins trust-update-url", "guard_plugins_trust_update_url"),
}


@pytest.mark.parametrize("capability", sorted(FALSE_NEGATIVES))
def test_false_negative_intent_resolves(capability):
    phrase, expected_id = FALSE_NEGATIVES[capability]
    res = VC.resolve(phrase)
    assert res.kind == VC.MATCH, (capability, phrase, res.kind, res.speech)
    assert res.entry is not None, (capability, phrase)
    assert res.entry.id == expected_id, (capability, phrase, res.entry.id)
    assert res.entry.backend in VC._BACKENDS, (capability, res.entry.backend)


def test_activate_guard_does_not_shadow_id_scoped_enable_disable():
    # The bulk guard must not steal the real id-scoped counterparts' phrases.
    enable = VC.resolve("plugins enable my-plugin")
    disable = VC.resolve("plugins disable my-plugin")
    assert enable.entry is not None and enable.entry.id == "cli_plugins_enable"
    assert disable.entry is not None and disable.entry.id == "cli_plugins_disable"
    assert enable.entry.backend != "voice_unsupported"
    assert disable.entry.backend != "voice_unsupported"
