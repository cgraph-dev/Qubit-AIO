"""Task 16 (v0.21.5 rebase) — voice intents for the new capabilities.

Every v0.21.5 capability that previously had no voice path is now wired to
either a real backend or an audited ``voice_unsupported`` guard. This test
proves each spoken intent resolves through the production ``resolve()``
pipeline to its intended entry, that the entry's backend is bound, and that
guard intents execute as typed refusals (never silent, never a fake success).

Pure ``resolve()`` checks need no config; the execution check only exercises
guards (no e-stop/journal interaction).
"""
from __future__ import annotations

from voicebridge import voice_commands as VC

# spoken phrase -> (expected entry id, "real" | "guard")
NEW_INTENTS = {
    "chat workspaces": ("chat_workspaces", "real"),
    "list chat workspaces": ("chat_workspaces", "real"),
    "pause local model download":
        ("guard_local_models_download_pause", "guard"),
    "resume local model download":
        ("guard_local_models_download_resume", "guard"),
    "computer-use screen status":
        ("guard_computer_use_screen_status", "guard"),
    "computer-use screen start":
        ("guard_computer_use_screen_start", "guard"),
    "computer-use screen stop":
        ("guard_computer_use_screen_stop", "guard"),
    "computer-use screen install":
        ("guard_computer_use_screen_install", "guard"),
    "plugins adopt": ("guard_plugins_adopt", "guard"),
    "plugins trust-update-url": ("guard_plugins_trust_update_url", "guard"),
    "plugins check-updates": ("guard_plugins_check_updates", "guard"),
    "desktop display stream": ("guard_display_ws", "guard"),
}


def test_new_intents_resolve_to_their_entry():
    for phrase, (expected_id, _cls) in NEW_INTENTS.items():
        res = VC.resolve(phrase)
        assert res.kind == VC.MATCH, (phrase, res.kind, res.speech)
        assert res.entry is not None, phrase
        assert res.entry.id == expected_id, (phrase, res.entry.id)


def test_new_intents_bind_a_real_or_guard_backend():
    for phrase, (expected_id, cls) in NEW_INTENTS.items():
        entry = VC._lookup_entry(expected_id)
        assert entry is not None, expected_id
        assert entry.backend in VC._BACKENDS, (expected_id, entry.backend)
        if cls == "guard":
            assert entry.backend == "voice_unsupported", expected_id
        else:
            assert entry.backend != "voice_unsupported", expected_id


def test_guard_intents_execute_as_typed_refusals():
    ctx = VC.make_ctx(None, actor="test:user", session="task16", source="user")
    for phrase, (expected_id, cls) in NEW_INTENTS.items():
        if cls != "guard":
            continue
        out = VC.execute(ctx, VC.resolve(phrase))
        assert out.kind == VC.EXECUTED, (phrase, out.kind, out.speech)
        result = out.receipt.get("result", {})
        assert result.get("state") == "refused", (phrase, result)
        assert result.get("code"), (phrase, result)
        assert result.get("guidance"), (phrase, result)
        assert out.confirmation_id == "", "a refusal must stage nothing"
