"""A38/A39 — bounded OpenClaw-delta desktop/browser ops.

Covers the two delta rows end-to-end against the real production modules
(external boundaries are explicit injected substitutes; gates, refusals,
audit, e-stop, origin/root confinement and receipts are always real):

* A38 ``voicebridge/browser_tabs.py`` — favicon capture + document download,
  confined to the attached tab's approved http(s) origin and to approved
  project roots via ``files.py``, e-stop-respecting, audited, with a receipt;
* A39 ``voicebridge/computer_use.py`` — allowlisted app launcher, Document
  Picture-in-Picture and presentation descriptor, each behind a confirmation
  gate with an explicit target/owner/resource boundary and a typed refusal
  when not permitted. No shell, argv, or window-manager path exists.
"""
from __future__ import annotations

import ast
import base64
import hashlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def call(self, method, params=None, *, timeout=None, session_id=""):
        self.calls.append((method, params))
        return self._responses.pop(0)

    def close(self):
        pass


def _page(url="https://example.com/page", tid="t1"):
    return {"id": tid, "url": url, "title": "Example"}


# ------------------------------------------------------------------ A38
def test_favicon_capture_same_origin_and_cache(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(BT, "estop_engaged", lambda _cfg: False)
    monkeypatch.setattr(BT, "_resolve_target",
                        lambda cfg, target="": _page())
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 16
    value = {"result": {"value": {
        "found": True, "origin": "https://example.com",
        "icon": "https://example.com/favicon.ico", "mime": "image/png",
        "data_url": "data:image/png;base64," + base64.b64encode(png).decode()}}}
    monkeypatch.setattr(BT, "_target_session",
                        lambda page: _FakeSession([value]))
    out = BT.favicon(cfg)
    assert out["state"] == "OK", out
    assert out["origin"] == "https://example.com"
    assert out["mime"] == "image/png" and out["bytes"] == len(png)
    assert out["cached"] is False
    assert out["trust"] == "untrusted-page-image"
    # second call is served from the bounded cache without a CDP session.
    monkeypatch.setattr(BT, "_target_session",
                        lambda page: (_ for _ in ()).throw(
                            AssertionError("cache should have been used")))
    cached = BT.favicon(cfg)
    assert cached["state"] == "OK" and cached["cached"] is True
    assert BT.favicon_cache(cfg)["count"] == 1


def test_favicon_origin_confinement_and_estop(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(BT, "estop_engaged", lambda _cfg: False)
    monkeypatch.setattr(BT, "_resolve_target",
                        lambda cfg, target="": _page())
    with pytest.raises(BT.BrowserTabsError) as exc:
        BT.favicon(cfg, origins=["https://other.test"])
    assert exc.value.code == BT.FAVICON_ORIGIN_CODE
    # a non-http(s) tab is refused before any session exists
    monkeypatch.setattr(BT, "_resolve_target",
                        lambda cfg, target="": _page("file:///etc/passwd"))
    with pytest.raises(BT.BrowserTabsError) as exc2:
        BT.favicon(cfg)
    assert exc2.value.code == BT.FAVICON_ORIGIN_CODE
    monkeypatch.setattr(BT, "estop_engaged", lambda _cfg: True)
    assert BT.favicon(cfg)["code"] == BT.FAVICON_ESTOP_CODE


def test_favicon_rejects_non_image(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(BT, "estop_engaged", lambda _cfg: False)
    monkeypatch.setattr(BT, "_resolve_target",
                        lambda cfg, target="": _page())
    bad = {"result": {"value": {
        "found": True, "mime": "text/plain",
        "data_url": "data:text/plain;base64,aGVsbG8="}}}
    monkeypatch.setattr(BT, "_target_session",
                        lambda page: _FakeSession([bad]))
    out = BT.favicon(cfg)
    assert out["state"] == "FAILED" and out["code"] == BT.FAVICON_BAD_INPUT_CODE


def test_download_writes_inside_approved_root(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT
    from voicebridge import files as FL

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(FL, "approved_roots", lambda _cfg: [root])
    monkeypatch.setattr(BT, "estop_engaged", lambda _cfg: False)
    monkeypatch.setattr(BT, "_resolve_target",
                        lambda cfg, target="": _page("https://example.com/doc"))
    raw = b"<html>qubit</html>"
    value = {"result": {"value": {
        "found": True, "url": "https://example.com/doc",
        "origin": "https://example.com", "mime": "text/html",
        "size": len(raw), "data_base64": base64.b64encode(raw).decode()}}}
    monkeypatch.setattr(BT, "_target_session",
                        lambda page: _FakeSession([value]))
    # confirmation is mandatory before any write.
    with pytest.raises(BT.BrowserTabsError) as exc:
        BT.download(cfg, str(root / "doc.html"))
    assert exc.value.code == BT.CONFIRM_CODE
    out = BT.download(cfg, str(root / "doc.html"), confirmed=True)
    assert out["state"] == "OK", out
    assert (root / "doc.html").read_bytes() == raw
    assert out["bytes"] == len(raw)
    assert out["receipt"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert out["receipt"]["kind"] == "browser.document.download"
    # an approved origin list that omits the tab origin fails closed.
    with pytest.raises(BT.BrowserTabsError) as exc2:
        BT.download(cfg, str(root / "doc.html"),
                    origins=["https://other.test"], confirmed=True)
    assert exc2.value.code == BT.DOWNLOAD_ORIGIN_CODE


def test_download_confined_to_root_and_estop(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT
    from voicebridge import files as FL

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(FL, "approved_roots", lambda _cfg: [root])
    monkeypatch.setattr(BT, "estop_engaged", lambda _cfg: False)
    monkeypatch.setattr(BT, "_resolve_target",
                        lambda cfg, target="": _page("https://example.com/doc"))
    monkeypatch.setattr(BT, "_target_session",
                        lambda page: _FakeSession([]))
    with pytest.raises(BT.BrowserTabsError) as exc:
        BT.download(cfg, str(tmp_path / "outside.html"), confirmed=True)
    assert exc.value.code == BT.DOWNLOAD_OUTSIDE_ROOT_CODE
    assert not (tmp_path / "outside.html").exists()
    monkeypatch.setattr(BT, "estop_engaged", lambda _cfg: True)
    out = BT.download(cfg, str(root / "doc.html"), confirmed=True)
    assert out["code"] == BT.DOWNLOAD_ESTOP_CODE


def test_download_too_large_fails_closed(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT
    from voicebridge import files as FL

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(FL, "approved_roots", lambda _cfg: [root])
    monkeypatch.setattr(BT, "estop_engaged", lambda _cfg: False)
    monkeypatch.setattr(BT, "_resolve_target",
                        lambda cfg, target="": _page("https://example.com/big"))
    raw = b"x" * 64
    value = {"result": {"value": {
        "found": True, "mime": "text/html", "size": len(raw),
        "data_base64": base64.b64encode(raw).decode()}}}
    monkeypatch.setattr(BT, "_target_session",
                        lambda page: _FakeSession([value]))
    out = BT.download(cfg, str(root / "big.html"), max_bytes=8, confirmed=True)
    assert out["state"] == "denied" and out["code"] == BT.DOWNLOAD_TOO_LARGE_CODE
    assert not (root / "big.html").exists()


# ------------------------------------------------------------------ A39
def _live():
    return {"state": "OK", "session": "x11", "headless": False, "detail": "x11"}


def test_desktop_ops_catalog_is_bounded():
    from voicebridge import computer_use as CU

    out = CU.desktop_ops()
    assert set(out["ops"]) == {"desktop.launch",
                               "desktop.picture_in_picture",
                               "desktop.present"}
    assert "browser" in out["apps"] and "terminal" in out["apps"]
    assert "rm -rf" not in out["apps"]
    assert set(out["presentation_modes"]) == set(CU.PRESENTATION_MODES)


def test_launch_app_allowlist_confirm_and_boundary(tmp_path, monkeypatch):
    from voicebridge import computer_use as CU

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CU, "_estop", lambda _cfg: False)
    monkeypatch.setattr(CU, "desktop_session", lambda env=None: _live())
    # an arbitrary string (or argv-shaped value) is never a launch.
    bad = CU.launch_app(cfg, "rm -rf /", confirmed=True)
    assert bad["state"] == "REFUSED"
    assert bad["code"] == CU.LAUNCH_BAD_INPUT_CODE
    assert CU.launch_app(cfg, {"argv": ["id"]},
                         confirmed=True)["code"] == CU.LAUNCH_BAD_INPUT_CODE
    # confirmation gate, then the typed no-launcher refusal.
    assert CU.launch_app(cfg, "browser")["code"] == CU.LAUNCH_CONFIRM_CODE
    nol = CU.launch_app(cfg, "browser", confirmed=True)
    assert nol["state"] == "REFUSED" and nol["code"] == CU.LAUNCH_NO_LAUNCHER_CODE
    # the injected boundary receives exactly (app, target); no argv/env.
    calls = []

    def launcher(app, target):
        calls.append((app, target))
        return {"ok": True, "detail": "ready"}

    ok = CU.launch_app(cfg, "browser", target="env-1", launcher=launcher,
                       confirmed=True)
    assert ok["state"] == "OK" and calls == [("browser", "env-1")]
    assert ok["resource"] == {"apps": 1, "scope": "env-1"}
    assert ok["authority"].startswith("injected allowlisted launcher")
    # a target carrying a shell metacharacter is refused before the boundary.
    bad_target = CU.launch_app(cfg, "browser", target="a; rm -rf /",
                               launcher=launcher, confirmed=True)
    assert bad_target["code"] == CU.LAUNCH_BAD_INPUT_CODE
    assert calls == [("browser", "env-1")]


def test_launch_app_headless_without_boundary(tmp_path, monkeypatch):
    from voicebridge import computer_use as CU

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CU, "_estop", lambda _cfg: False)
    headless = {"state": "OK", "session": "none", "headless": True,
                "detail": "no graphical session"}
    monkeypatch.setattr(CU, "desktop_session", lambda env=None: headless)
    out = CU.launch_app(cfg, "browser", confirmed=True)
    assert out["state"] == "UNAVAILABLE" and out["code"] == CU.NO_DESKTOP_CODE


def test_picture_in_picture_gates(tmp_path, monkeypatch):
    from voicebridge import computer_use as CU

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CU, "_estop", lambda _cfg: False)
    # no surface: honest typed refusal, never a fake popup.
    refused = CU.picture_in_picture(cfg, confirmed=True)
    assert refused["state"] == "REFUSED"
    assert refused["code"] == CU.PIP_UNAVAILABLE_CODE
    seen = []

    def surface(target, width, height):
        seen.append((target, width, height))
        return {"ok": True}

    assert CU.picture_in_picture(cfg, surface=surface)[
        "code"] == CU.PIP_CONFIRM_CODE
    assert CU.picture_in_picture(cfg, width=10, surface=surface,
                                 confirmed=True)["code"] == CU.PIP_BAD_INPUT_CODE
    ok = CU.picture_in_picture(cfg, target="env-1", surface=surface,
                               confirmed=True)
    assert ok["state"] == "OK"
    assert seen == [("env-1", 640, 400)]
    assert ok["resource"] == {"windows": 1, "scope": "env-1"}


def test_present_gates_and_persists(tmp_path, monkeypatch):
    from voicebridge import computer_use as CU

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CU, "_estop", lambda _cfg: False)
    assert CU.present(cfg)["code"] == CU.PRESENT_CONFIRM_CODE
    assert CU.present(cfg, mode="spiral", confirmed=True)[
        "code"] == CU.PRESENT_BAD_INPUT_CODE
    ok = CU.present(cfg, "dock", panes=["chat", "files"], target="env-1",
                    confirmed=True)
    assert ok["state"] == "OK" and ok["mode"] == "dock"
    assert ok["resource"] == {"panes": 2, "scope": "env-1"}
    status = CU.presentation_status(cfg)
    assert status["presenting"] is True
    assert status["mode"] == "dock"
    assert status["panes"] == ["chat", "files"]


def test_desktop_ops_estop_latches(tmp_path, monkeypatch):
    from voicebridge import computer_use as CU

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CU, "_estop", lambda _cfg: True)
    assert CU.launch_app(cfg, "browser", confirmed=True)[
        "code"] == CU.LAUNCH_ESTOP_CODE
    assert CU.picture_in_picture(cfg, surface=lambda *a: {},
                                 confirmed=True)["code"] == CU.PIP_ESTOP_CODE
    assert CU.present(cfg, confirmed=True)["code"] == CU.PRESENT_ESTOP_CODE


# ------------------------------------------------------- no shell/OS bypass
def test_no_shell_or_process_spawn_in_modules():
    banned_mods = {"subprocess", "pty", "multiprocessing", "ctypes"}
    for rel in ("voicebridge/computer_use.py", "voicebridge/browser_tabs.py"):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        assert not (mods & banned_mods), (rel, mods & banned_mods)
        assert "os.system" not in src, rel
        assert "os.popen" not in src, rel
        assert "shell=True" not in src, rel
