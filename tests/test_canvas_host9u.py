"""Canvas confined host + flows hooks backend (lane I1).

Proves the two integrator-flagged gaps are closed with real backends:

* canvas file host (``voicebridge/files/canvas_host.py``): traversal/
  absolute/oversize refused typed, unconfirmed writes refused with nothing
  written, add->get byte-identical round-trip (sha256), metadata-only audit
  event per mutation, present/hide/navigate/snapshot on confined ops;
* flows hooks (``voicebridge/schedules.py`` hooks region): the 5 bundled
  hooks list disabled-first, enable/disable flips with confirmation,
  unknown names typed NOT_FOUND, programs renders recurring jobs only.

Hermetic: temp XDG/QUBIT_HOME only; no live service, network, or hardware.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.unit


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qubit_home"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _ch():
    from voicebridge.qubit_compat import _canvas_host_module

    return _canvas_host_module()


def test_bad_names_are_refused_typed(tmp_path, monkeypatch):
    """Traversal/absolute/scheme/odd names never reach disk."""
    CH = _ch()
    cfg = _cfg(tmp_path, monkeypatch)
    for bad in ("../evil.md", "/abs.md", "..", "", "a/../../b",
                "http://x/y", "//host/canvas/a.md", "a\x00b",
                "C:/win.md", ".", "a/./b", "seg:ment.md"):
        with pytest.raises(CH.CanvasError) as exc:
            CH.get_file(cfg, bad)
        assert exc.value.code in (
            "CANVAS_BAD_PATH", "CANVAS_ENTRY_REFUSED"), (bad, exc.value.code)
    # nothing was created for any of them
    assert list((Path(cfg.state_dir()) / "canvas").rglob("*")) == []


def test_oversize_add_refused_and_nothing_written(tmp_path, monkeypatch):
    """Content over 1 MiB is refused before any write."""
    CH = _ch()
    cfg = _cfg(tmp_path, monkeypatch)
    big = "x" * (CH.MAX_CONTENT_BYTES + 1)
    with pytest.raises(CH.CanvasError) as exc:
        CH.add_file(cfg, "big.md", big, confirmed=True)
    assert exc.value.code == "CANVAS_TOO_LARGE", exc.value.code
    assert not (Path(cfg.state_dir()) / "canvas" / "big.md").exists()


def test_add_requires_confirm_and_writes_nothing(tmp_path, monkeypatch):
    """An unconfirmed add stages nothing on disk."""
    CH = _ch()
    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(CH.CanvasError) as exc:
        CH.add_file(cfg, "probe.md", "hello", confirmed=False)
    assert exc.value.code == "CANVAS_CONFIRM_REQUIRED", exc.value.code
    assert not (Path(cfg.state_dir()) / "canvas" / "probe.md").exists()


def test_add_get_round_trip_byte_identical_and_audited(tmp_path, monkeypatch):
    """add -> get returns the exact bytes (sha256) + a metadata-only audit."""
    CH = _ch()
    cfg = _cfg(tmp_path, monkeypatch)
    events = []
    monkeypatch.setattr(CH, "_audit",
                        lambda op, actor, detail: events.append((op, detail)))
    body = "Hello canvas — qubit host ✓\n" * 40
    added = CH.add_file(cfg, "notes/probe.md", body, confirmed=True,
                        requested_by="test:canvas")
    assert added["state"] == "OK", added
    assert added["size"] == len(body.encode("utf-8")), added
    got = CH.get_file(cfg, "notes/probe.md")
    assert got["state"] == "OK", got
    assert base64.b64decode(got["data_base64"]).decode("utf-8") == body
    assert got["sha256"] == added["sha256"], (got, added)
    assert got["content"] == body[:CH.MAX_READ_CHARS]
    assert got["truncated"] is (len(body) > CH.MAX_READ_CHARS)
    assert [op for op, _ in events] == ["add", "read"], events
    assert all("Hello" not in str(detail) for _, detail in events), events


def test_remove_round_trip_and_missing_is_typed(tmp_path, monkeypatch):
    """remove deletes; a second remove (or get) is typed NOT_FOUND."""
    CH = _ch()
    cfg = _cfg(tmp_path, monkeypatch)
    CH.add_file(cfg, "gone.md", "bye", confirmed=True)
    out = CH.remove_file(cfg, "gone.md", confirmed=True)
    assert out["state"] == "OK" and out["removed"] == "gone.md", out
    with pytest.raises(CH.CanvasError) as exc:
        CH.remove_file(cfg, "gone.md", confirmed=True)
    assert exc.value.code == "CANVAS_NOT_FOUND", exc.value.code
    with pytest.raises(CH.CanvasError) as exc:
        CH.get_file(cfg, "gone.md")
    assert exc.value.code == "CANVAS_NOT_FOUND", exc.value.code
    with pytest.raises(CH.CanvasError) as exc:
        CH.remove_file(cfg, "gone.md", confirmed=False)
    assert exc.value.code == "CANVAS_CONFIRM_REQUIRED", exc.value.code


def test_list_and_live_are_read_only_and_bounded(tmp_path, monkeypatch):
    """list/live serve real root state without confirmation."""
    CH = _ch()
    cfg = _cfg(tmp_path, monkeypatch)
    CH.add_file(cfg, "a.md", "a", confirmed=True)
    CH.add_file(cfg, "sub/b.md", "bb", confirmed=True)
    listing = CH.list_files(cfg)
    assert listing["state"] == "OK", listing
    assert listing["root"] == "state-home canvas/", listing
    names = sorted(f["path"] for f in listing["files"])
    assert names == ["a.md", "sub/b.md"], listing
    live = CH.live_state(cfg)
    assert live["live_reload"] is False, live
    assert live["root"] == "state-home canvas/", live


def test_present_hide_navigate_snapshot_on_confined_ops(tmp_path, monkeypatch):
    """Presentation maps onto confined ops: missing files 404, external refused."""
    CH = _ch()
    cfg = _cfg(tmp_path, monkeypatch)
    events = []
    monkeypatch.setattr(CH, "_audit",
                        lambda op, actor, detail: events.append(op))
    CH.add_file(cfg, "deck.md", "# deck", confirmed=True)
    shown = CH.present(cfg, "node-1", "/__qubit__/canvas/deck.md",
                       confirmed=True)
    assert shown["state"] == "OK", shown
    assert shown["entry"] == "/__qubit__/canvas/deck.md", shown
    assert shown["preview_url"].endswith("/__qubit__/canvas/deck.md"), shown
    scoped = CH.present(cfg, "node-1", "deck.md",
                        scoped_base_url="https://node.local/__qubit__/cap/n1",
                        confirmed=True)
    assert scoped["preview_url"].startswith(
        "https://node.local/__qubit__/cap/n1/__qubit__/canvas/deck.md"), scoped
    with pytest.raises(CH.CanvasError) as exc:
        CH.present(cfg, "node-1", "/__qubit__/canvas/missing.md",
                   confirmed=True)
    assert exc.value.code == "CANVAS_NOT_FOUND", exc.value.code
    with pytest.raises(CH.CanvasError) as exc:
        CH.present(cfg, "node-1", "https://evil.example/x.md",
                   confirmed=True)
    assert exc.value.code == "CANVAS_ENTRY_REFUSED", exc.value.code
    hidden = CH.hide(cfg, "node-1", confirmed=True)
    assert hidden == {"state": "OK", "node_id": "node-1", "hidden": True,
                      "authority": hidden["authority"]}, hidden
    moved = CH.navigate(cfg, "node-1", "/__qubit__/canvas/deck.md",
                        confirmed=True)
    assert moved["exists"] is True and moved["node_id"] == "node-1", moved
    with pytest.raises(CH.CanvasError) as exc:
        CH.navigate(cfg, "node-1", "https://evil.example/x.md",
                    confirmed=True)
    assert exc.value.code == "CANVAS_ENTRY_REFUSED", exc.value.code
    proof = CH.snapshot(cfg, "node-1", confirmed=True)
    assert proof["state"] == "OK" and proof["count"] == 1, proof
    assert proof["files"][0]["path"] == "deck.md", proof
    assert len(proof["files"][0]["sha256_16"]) == 16, proof
    assert "no screenshot" in proof["proof"], proof
    for op in ("present", "hide", "navigate", "snapshot"):
        assert op in events, events
    with pytest.raises(CH.CanvasError) as exc:
        CH.snapshot(cfg, "node-1", confirmed=False)
    assert exc.value.code == "CANVAS_CONFIRM_REQUIRED", exc.value.code


def test_flow_hooks_list_five_disabled_first(tmp_path, monkeypatch):
    """The hooks registry lists exactly the 5 bundled hooks, all disabled."""
    from voicebridge import schedules as S

    cfg = _cfg(tmp_path, monkeypatch)
    out = S.list_flow_hooks(cfg)
    assert out["state"] == "OK", out
    assert [h["name"] for h in out["hooks"]] == [
        "session-memory", "bootstrap-extra-files", "command-logger",
        "compaction-notifier", "boot-md"], out
    assert all(h["enabled"] is False for h in out["hooks"]), out
    assert len(out["catalog"]) == 5, out


def test_flow_hook_enable_disable_round_trip(tmp_path, monkeypatch):
    """enable flips on, disable flips back; unknown + unconfirmed refuse."""
    from voicebridge import schedules as S

    cfg = _cfg(tmp_path, monkeypatch)
    events = []
    monkeypatch.setattr(S, "_audit",
                        lambda op, actor, detail: events.append(op))
    with pytest.raises(S.SchedulesError) as exc:
        S.enable_flow_hook(cfg, "command-logger", confirmed=False)
    assert exc.value.code == S.CONFIRM_CODE, exc.value.code
    with pytest.raises(S.SchedulesError) as exc:
        S.enable_flow_hook(cfg, "no-such-hook", confirmed=True)
    assert exc.value.code == S.HOOK_NOT_FOUND_CODE, exc.value.code
    on = S.enable_flow_hook(cfg, "command-logger", confirmed=True)
    assert on["hook"]["enabled"] is True, on
    assert S.list_flow_hooks(cfg)["hooks"][2]["enabled"] is True
    off = S.disable_flow_hook(cfg, "command-logger", confirmed=True)
    assert off["hook"]["enabled"] is False, off
    assert all(h["enabled"] is False
               for h in S.list_flow_hooks(cfg)["hooks"])
    assert events == ["hook_enable", "hook_disable"], events


def test_programs_renders_recurring_jobs_only(tmp_path, monkeypatch):
    """programs = stored recurring schedules; one-shots never render."""
    from voicebridge import schedules as S

    cfg = _cfg(tmp_path, monkeypatch)
    S.create_job(cfg, "standing", "echo hi", "every 60 minutes",
                 confirmed=True)
    S.create_job(cfg, "daily-ping", "echo hi", "daily 9am", confirmed=True)
    S.create_job(cfg, "once-ping", "echo hi", "once in 10 minutes",
                 confirmed=True)
    out = S.list_programs(cfg)
    assert out["state"] == "OK", out
    names = sorted(p["name"] for p in out["programs"])
    assert names == ["daily-ping", "standing"], out
    assert all(p["kind"] == "standing-order" for p in out["programs"]), out
