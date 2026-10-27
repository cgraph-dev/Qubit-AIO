"""Cyberpunk HUD restyle for the Voice Edit overlay (real Tk).

These are structural/behavioural tests against a real Tk root, skipped when no
display is available. They pin the HUD chrome (Canvas polygons/rings/glow
layers), the neon palette, the public helper contract, the f504 anti-flicker
behaviour, and the plain fallback when Canvas drawing fails.
"""
from __future__ import annotations

import sys
import time
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
    return OV, ov, actions


def _windows(ov):
    return {
        "top": ov._win_top, "live": ov._win_live,
        "preview": ov._win_preview, "info": ov._win_info,
        "history": ov._win_history, "actions": ov._win_actions,
    }


def test_hud_canvas_structure_and_neon_palette():
    OV, ov, _ = _make()
    try:
        ov.show()
        ov.root.update()
        assert isinstance(ov._hud, ov._tk.Canvas)
        static = ov._hud.find_withtag("static")
        assert len(static) > 100, "HUD background/panels were not drawn"

        outlines = set()
        for item in static:
            if ov._hud.type(item) not in ("polygon", "line", "arc",
                                          "rectangle", "oval"):
                continue
            try:
                outlines.add(ov._hud.itemcget(item, "outline"))
            except Exception:
                pass
        for neon in (OV._MAGENTA, OV._CYAN, OV._VIOLET):
            assert neon in outlines, f"{neon} missing from HUD outlines"

        ov._draw_dynamic()
        types = {ov._hud.type(i) for i in ov._hud.find_withtag("gauge")}
        assert "arc" in types
        assert "oval" in types

        # Real widgets stay real widgets so select/copy/edit keep working.
        assert isinstance(ov.live_text, ov._tk.Text)
        assert isinstance(ov.text, ov._tk.Text)
        assert isinstance(ov._edit_entry, ov._tk.Text)
        assert isinstance(ov._history_text, ov._tk.Text)
        assert set(ov._info_vars) == {"model", "privacy", "app", "latency"}
    finally:
        ov.close()


def test_public_helper_contract_is_preserved():
    OV, ov, _ = _make()
    try:
        for name in ("set_status", "set_live_transcript", "set_level",
                     "show_preview", "append_history", "append_history_entry",
                     "show", "hide", "close", "show_error"):
            assert callable(getattr(ov, name, None)), name
        assert isinstance(ov._edit_entry, ov._tk.Text)
        for action in ("apply", "copy", "edit", "retry", "revert", "cancel"):
            assert action in ov._buttons
    finally:
        ov.close()


def test_partials_do_not_re_show_lift_or_re_geometry():
    """f5042141e regression: no per-partial show/lift/geometry/Unmap churn."""
    OV, ov, _ = _make()
    try:
        ov.show()
        ov.root.update()

        calls = {"geometry": 0, "lift": 0, "topmost": 0}
        real_geometry = ov.root.geometry
        real_lift = ov.root.lift
        real_attributes = ov.root.attributes

        def counting_geometry(*args, **kwargs):
            calls["geometry"] += 1
            return real_geometry(*args, **kwargs)

        def counting_lift(*args, **kwargs):
            calls["lift"] += 1
            return real_lift(*args, **kwargs)

        def counting_attributes(*args, **kwargs):
            if args and args[0] == "-topmost" and len(args) > 1:
                calls["topmost"] += 1
            return real_attributes(*args, **kwargs)

        ov.root.geometry = counting_geometry
        ov.root.lift = counting_lift
        ov.root.attributes = counting_attributes
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
        assert calls == {"geometry": 0, "lift": 0, "topmost": 0}
    finally:
        ov.close()


def test_hidden_window_stops_all_animation():
    OV, ov, _ = _make()
    try:
        ov.show()
        ov.root.update()
        assert ov._anim_job is not None
        ov.hide()
        assert ov._hidden is True
        assert ov._anim_job is None
        before = ov._tick_count
        time.sleep(0.3)
        ov.root.update()
        assert ov._tick_count == before
    finally:
        ov.close()


def test_animation_is_capped_at_ten_hz():
    OV, ov, _ = _make()
    try:
        assert OV._TICK_MS >= 100
    finally:
        ov.close()


def test_preview_populates_neon_info_cards_and_diff():
    OV, ov, actions = _make()
    try:
        ov.show_preview({
            "original": "the quik brown fox",
            "proposed": "the quick brown fox",
            "model": "qwen3", "provider": "ollama",
            "privacy_mode": "local", "locality": "local",
            "target_app": "code", "latency_ms": 1234,
            "detected": {"action": "rewrite", "style": "concise"},
        })
        ov.root.update()
        assert "qwen3" in ov._info_vars["model"].get()
        assert ov._info_vars["privacy"].get() == "local/local"
        assert ov._info_vars["app"].get() == "code"
        assert ov._info_vars["latency"].get() == "1234 ms"
        assert "quick" in ov.text.get("1.0", "end")
        assert ov._buttons["apply"].cget("state") == "normal"
        assert "Review" in ov._pill.cget("text")

        ov._buttons["apply"].invoke()
        assert actions[-1][0] == "apply"
    finally:
        ov.close()


def test_history_is_a_coloured_terminal_console():
    OV, ov, _ = _make()
    try:
        ov.append_history_entry("answer", "what is the weather",
                                "18°C and clear", "12:50:03")
        ov.append_history_entry("dictate", "type this", "typed text", "12:51:00")
        ov.root.update()
        body = ov._history_text.get("1.0", "end")
        assert "answer" in body and "what is the weather" in body
        assert "18°C and clear" in body and "12:50:03" in body
        assert ov._history_text.tag_cget("kind-answer", "foreground") == OV._VIOLET
        assert ov._history_text.tag_cget("kind-dictate", "foreground") == OV._CYAN
        assert ov._history_text.tag_cget("heard", "foreground") == OV._CYAN
        ov._clear_history()
        assert "History cleared" in ov._history_text.get("1.0", "end")
    finally:
        ov.close()


def test_edit_entry_opens_submits_and_hides():
    OV, ov, actions = _make()
    try:
        ov.show_preview({"original": "a", "proposed": "b"})
        ov._act("edit")
        # desktop_helper opens the editor with a plain pack() call; keep that.
        ov._edit_entry.pack(fill="x", pady=(8, 0))
        ov.root.update()
        assert ov._edit_entry.winfo_ismapped() == 1
        ov._edit_entry.delete("1.0", "end")
        ov._edit_entry.insert("1.0", "edited text")
        ov._submit_edit()
        assert actions[-1] == ("edit_submit", {"text": "edited text"})
        ov._hide_edit()
        ov.root.update()
        assert ov._edit_entry.winfo_ismapped() == 0
    finally:
        ov.close()


def test_canvas_failure_falls_back_to_plain_but_stays_usable(monkeypatch):
    OV, ov, _ = _make()
    try:
        def boom(*args, **kwargs):
            raise RuntimeError("canvas unavailable")

        monkeypatch.setattr(ov._hud, "create_polygon", boom)
        ov._redraw_static()
        assert ov._hud_ok is False

        ov.set_status("listening", "speak now")
        ov.set_live_transcript("still works")
        ov.root.update()
        assert ov.live_text.get("1.0", "end").strip() == "still works"
        assert "Listening" in ov._pill.cget("text")
        # Dynamic drawing is a safe no-op after the fallback.
        ov._draw_dynamic(safe=True)
    finally:
        ov.close()


@pytest.mark.parametrize("size", [(700, 560), (900, 760)])
def test_layout_fits_without_clipping(size):
    OV, ov, _ = _make()
    W, H = size
    try:
        ov._canvas_w, ov._canvas_h = W, H
        ov._relayout()
        for name, (x1, y1, x2, y2) in ov._rects.items():
            assert 0 <= x1 < x2 <= W, name
            assert 0 <= y1 < y2 <= H, name
        for name, item in _windows(ov).items():
            x, y = ov._hud.coords(item)
            w = int(ov._hud.itemcget(item, "width"))
            h = int(ov._hud.itemcget(item, "height"))
            assert x >= 0 and y >= 0, name
            assert x + w <= W and y + h <= H, name
            assert w > 0 and h > 0, name
    finally:
        ov.close()
