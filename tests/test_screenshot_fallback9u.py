"""Screenshot backend fallback (9u).

Hermetic: no real screenshot, no real files outside ``tmp_path``. The local
``_a_screenshot`` action must try whichever backends are actually on PATH and
only claim success when a real non-empty file lands at the target path.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def _only(name: str, tool: str):
    return lambda which: tool if which == name else None


def test_import_backend_writes_file(monkeypatch, tmp_path):
    from voicebridge.local import actions as A

    monkeypatch.setattr(A, "_screenshot_dir", lambda: tmp_path)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(A.shutil, "which", _only("import", "/usr/bin/import"))

    calls = []

    def fake_run(argv, timeout=10.0):
        calls.append(list(argv))
        Path(argv[-1]).write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return ""

    monkeypatch.setattr(A, "_run", fake_run)

    assert A._a_screenshot({}) == "Screenshot saved."
    assert any(
        "import" in c and "-window" in c and "root" in c for c in calls
    ), calls


def test_no_tool_found_is_honest(monkeypatch, tmp_path):
    from voicebridge.local import actions as A

    monkeypatch.setattr(A, "_screenshot_dir", lambda: tmp_path)
    monkeypatch.setattr(A.shutil, "which", lambda which: None)

    calls = []
    monkeypatch.setattr(
        A, "_run", lambda argv, timeout=10.0: calls.append(list(argv)) or ""
    )

    msg = A._a_screenshot({})
    assert "No screenshot tool" in msg
    assert "saved" not in msg.lower()
    assert calls == []


def test_tool_runs_but_writes_no_file_is_not_success(monkeypatch, tmp_path):
    from voicebridge.local import actions as A

    monkeypatch.setattr(A, "_screenshot_dir", lambda: tmp_path)
    monkeypatch.setattr(A.shutil, "which", _only("scrot", "/usr/bin/scrot"))

    calls = []
    monkeypatch.setattr(
        A, "_run", lambda argv, timeout=10.0: calls.append(list(argv)) or ""
    )

    msg = A._a_screenshot({})
    assert "Screenshot saved." not in msg
    assert "No screenshot tool" in msg
    assert calls, "the detected tool should still have been invoked"


def test_empty_file_is_not_success(monkeypatch, tmp_path):
    from voicebridge.local import actions as A

    monkeypatch.setattr(A, "_screenshot_dir", lambda: tmp_path)
    monkeypatch.setattr(A.shutil, "which", _only("scrot", "/usr/bin/scrot"))

    def fake_run(argv, timeout=10.0):
        Path(argv[-1]).write_bytes(b"")
        return ""

    monkeypatch.setattr(A, "_run", fake_run)

    msg = A._a_screenshot({})
    assert "Screenshot saved." not in msg
    assert "No screenshot tool" in msg
    assert list(tmp_path.iterdir()) == [], "empty artifact left behind"


def test_import_requires_display(monkeypatch, tmp_path):
    from voicebridge.local import actions as A

    monkeypatch.setattr(A, "_screenshot_dir", lambda: tmp_path)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(A.shutil, "which", _only("import", "/usr/bin/import"))
    monkeypatch.setattr(A, "_run", lambda argv, timeout=10.0: "")

    msg = A._a_screenshot({})
    assert "No screenshot tool" in msg
    assert "Screenshot saved." not in msg
