"""Structural tests for the redesigned Voice Edit panel (real Tk, skipped offscreen)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _make():
    try:
        from voicebridge.voice_edit import overlay as OV
    except Exception as exc:  # pragma: no cover - import environment
        pytest.skip(f"overlay module unavailable: {exc}")
    actions = []
    try:
        ov = OV.PreviewOverlay(on_action=lambda a, p: actions.append((a, p)))
    except Exception as exc:
        pytest.skip(f"no display for tkinter: {exc}")
    return ov, actions


def test_initial_state_is_idle_and_review_actions_disabled():
    ov, _ = _make()
    try:
        ov.root.update()
        assert "Ready" in ov._pill.cget("text")
        assert ov._buttons["apply"].cget("state") == "disabled"
        assert ov._buttons["copy"].cget("state") == "disabled"
        assert "Hold Right-Ctrl" in ov.live_text.get("1.0", "end")
        assert ov._live_badge_var.get() == "LIVE"
    finally:
        ov.close()


def test_listening_and_live_transcript_render_before_preview():
    ov, _ = _make()
    try:
        ov.show()
        ov.set_status("listening", "speak now")
        ov.set_live_transcript("make it")
        ov.set_level(0.8)
        ov.root.update()
        assert "Listening" in ov._pill.cget("text")
        assert ov.live_text.get("1.0", "end").strip() == "make it"
        assert any(ov._level_canvas.itemcget(bar, "fill") in ("#22d3ee", "#a78bfa")
                   for bar in ov._level_bars)

        ov.set_live_transcript("make it clearer", final=True)
        ov.root.update()
        assert ov._live_badge_var.get() == "FINAL"
        assert ov.live_text.get("1.0", "end").strip() == "make it clearer"
    finally:
        ov.close()


def test_preview_enables_actions_and_renders_a_diff():
    ov, actions = _make()
    try:
        ov.show_preview({
            "original": "the quik brown fox",
            "proposed": "the quick brown fox",
            "model": "qwen3", "provider": "ollama",
            "privacy_mode": "local", "locality": "local",
            "detected": {"action": "rewrite", "style": "concise"},
            "target_app": "code",
        })
        ov.root.update()
        assert ov._buttons["apply"].cget("state") == "normal"
        assert ov._buttons["retry"].cget("state") == "normal"
        assert "quick" in ov.text.get("1.0", "end")
        assert "qwen3" in ov._meta_var.get()
        assert "rewrite" in ov._meta_var.get()
        assert "word" in ov._diff_stat_var.get()

        ov._buttons["apply"].invoke()
        assert actions and actions[-1][0] == "apply"
    finally:
        ov.close()


def test_edit_action_opens_the_editor_and_submits_text():
    ov, actions = _make()
    try:
        ov.show_preview({"original": "a", "proposed": "b"})
        ov._act("edit")
        ov._edit_entry.pack(fill="x", pady=(8, 0))
        ov._edit_entry.delete("1.0", "end")
        ov._edit_entry.insert("1.0", "edited text")
        ov._submit_edit()
        assert actions[-1] == ("edit_submit", {"text": "edited text"})
    finally:
        ov.close()


def test_streaming_updates_do_not_remap_or_hide_the_window():
    """Regression: partials re-show/re-lift/re-geometry the panel every tick."""
    ov, _ = _make()
    try:
        ov.show()
        ov.root.update()
        unmaps = []
        ov.root.bind("<Unmap>", lambda event: unmaps.append(event))
        for i in range(50):
            ov.set_status("listening", "speak now")
            ov.set_live_transcript(f"word {i}")
            ov.set_level(0.7)
            ov.root.update()
        assert ov.root.state() == "normal"
        assert ov.root.winfo_viewable() == 1
        assert unmaps == []
    finally:
        ov.close()


def test_history_rows_show_kind_time_and_text_and_clear():
    ov, _ = _make()
    try:
        ov.append_history_entry("answer", "what is the weather",
                                "18°C and clear", "12:50:03")
        ov.append_history_entry("dictate", "type this", "typed text", "12:51:00")
        ov.root.update()
        body = ov._history_text.get("1.0", "end")
        assert "answer" in body and "what is the weather" in body
        assert "18°C and clear" in body and "12:50:03" in body
        assert "dictate" in body
        ov._clear_history()
        body = ov._history_text.get("1.0", "end")
        assert "History cleared" in body
    finally:
        ov.close()


def test_error_state_surfaces_the_code_and_detail():
    ov, _ = _make()
    try:
        ov.show_error("VOICE_EDIT_FAILED", "provider unavailable")
        ov.root.update()
        assert "Error" in ov._pill.cget("text")
        assert "VOICE_EDIT_FAILED" in ov._detail_var.get()
        assert "provider unavailable" in ov._detail_var.get()
    finally:
        ov.close()
