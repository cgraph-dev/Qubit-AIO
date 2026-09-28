"""Agent 7 — OpenClaw ledger wiring proof for browser/desktop/web_media rows.

Hermetic (synthetic fixtures, fake CDP, no live Chromium, no network, no
credentials). Proves the Agent-7 capabilities are reachable through the
already-wired production read/entry points — not only the new functions:

* OC-10  browser profile lifecycle + Qubit-managed close-profile
* OC-D27 browser screencast with screenshot fallback
* OC-15  desktop ``screen`` arrange tool
* OC-D20 zero-dependency PDF text extraction
* OC-D28 provider account controls: read-only model fallback picker

Each test calls the same surfaces the CLI/dashboard/voice handlers call
(``local_models.status``/``catalog``, ``computer_use.status``,
``browser_tabs.doctor``, ``doc_extract.status``) and asserts the negative
paths stay fail-closed.
"""
from __future__ import annotations

import base64
import json

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# --------------------------------------------------------------- OC-D28
def test_status_and_catalog_surface_fallback_picker(tmp_path, monkeypatch):
    from voicebridge import local_models as LM

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(LM, "_probe_local",
                        lambda *_a, **_k: {"state": "OK", "models": [
                            {"id": "qubit-brain4:latest", "name":
                             "qubit-brain4:latest", "size": 1}]})
    status = LM.status(cfg)
    assert status["fallback"]["state"] == "OK"
    assert status["fallback"]["selected"] == "qubit-brain4"
    catalog = LM.catalog(cfg)
    assert any(row["available"] for row in catalog["fallback"])
    # No credentials are involved anywhere in the picker output.
    assert "api_key" not in json.dumps(status).lower()


def test_status_fallback_is_typed_unavailable_without_runtime(tmp_path,
                                                              monkeypatch):
    from voicebridge import local_models as LM

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(LM, "_probe_local", lambda *_a, **_k: None)
    pick = LM.status(cfg)["fallback"]
    assert pick["state"] == "UNAVAILABLE"
    assert pick["code"] == LM.UNAVAILABLE_CODE
    assert pick["selected"] is None


# --------------------------------------------------------------- OC-15
def test_status_surfaces_screen_catalog(tmp_path, monkeypatch):
    from voicebridge import computer_use as CU

    cfg = _cfg(tmp_path, monkeypatch)
    out = CU.status()
    assert out["screen"]["schema"] == CU.SCREEN_SCHEMA
    assert "chat" in out["screen"]["panes"]
    # Arranging is confirm-gated and estop-gated (negative).
    monkeypatch.setattr(CU, "_estop", lambda _cfg: False)
    assert CU.screen(cfg, ["chat"])["code"] == CU.SCREEN_CONFIRM_CODE
    monkeypatch.setattr(CU, "_estop", lambda _cfg: True)
    assert CU.screen(cfg, ["chat"],
                     confirmed=True)["code"] == CU.SCREEN_ESTOP_CODE


# --------------------------------------------------------------- OC-D27
def test_doctor_surfaces_screencast_capability(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT
    from voicebridge import cdp as CDP

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CDP, "probe", lambda **_k: {
        "Browser": "Synthetic/1.0", "Protocol-Version": "1.3",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/x"})
    monkeypatch.setattr(BT, "_list_targets", lambda: [])
    out = BT.doctor(cfg)
    assert out["screencast"]["supported"] is True
    assert out["screencast"]["screenshot_fallback"] is True


def test_screencast_never_returns_fake_frame_without_endpoint(tmp_path,
                                                              monkeypatch):
    from voicebridge import browser_tabs as BT
    from voicebridge import cdp as CDP

    cfg = _cfg(tmp_path, monkeypatch)

    def _down(method, path, *, timeout=CDP.HTTP_TIMEOUT_S, query=""):
        raise CDP.CdpError(CDP.UNAVAILABLE_CODE, "no loopback CDP endpoint")

    monkeypatch.setattr(CDP, "http_json", _down)
    out = BT.screencast(cfg, fallback=True)
    assert out["state"] == "UNAVAILABLE"
    assert "image_base64" not in out and "frames" not in out


# --------------------------------------------------------------- OC-10
def test_profiles_surface_is_managed_only(tmp_path, monkeypatch):
    from voicebridge import browser as BR

    cfg = _cfg(tmp_path, monkeypatch)
    assert BR.profiles(cfg)["profiles"] == []
    # A real user-profile name is refused and never enters the registry.
    assert BR.close_profile(cfg, "Default",
                            confirmed=True)["code"] == BR.CLOSE_CODE
    assert BR._load_registry(cfg) == {}


# --------------------------------------------------------------- OC-D20
def test_status_surfaces_stdlib_pdf_backend(tmp_path, monkeypatch):
    from voicebridge import doc_extract as DE

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(DE, "PDF_BACKENDS", (("stdlib", "stdlib"),))
    out = DE.status(cfg)
    assert out["pdf"]["available"] is True
    assert out["pdf"]["backend"] == "stdlib"


def test_pdf_negative_without_backend(tmp_path, monkeypatch):
    from voicebridge import doc_extract as DE
    from voicebridge import projects as PJ

    cfg = _cfg(tmp_path, monkeypatch)
    folder = tmp_path / "work"
    folder.mkdir()
    created = PJ.create(cfg, "Docs", [str(folder)], requested_by="cli:user",
                        confirmed=True)
    PJ.use(cfg, created["project"]["slug"], requested_by="cli:user",
           confirmed=True)
    (folder / "doc.pdf").write_bytes(b"%PDF-1.4\n(hi) Tj\n")
    monkeypatch.setattr(DE, "PDF_BACKENDS", ())
    with pytest.raises(DE.DocExtractError) as exc:
        DE.extract(cfg, str(folder / "doc.pdf"), confirmed=True)
    assert exc.value.code == DE.UNAVAILABLE_CODE


# --------------------------------------------------------- media boundary
def test_screencast_uses_screenshot_fallback_frames(tmp_path, monkeypatch):
    """The live-panel fallback is the existing bounded screenshot path."""
    from voicebridge import browser_tabs as BT
    from voicebridge import cdp as CDP

    cfg = _cfg(tmp_path, monkeypatch)
    raw = b"\x89PNG-fallback-frame"

    class _Session:
        def __init__(self):
            self.calls = []
            self.closed = False

        def call(self, method, params=None, *, timeout=None, session_id=""):
            self.calls.append(method)
            if method == "Page.startScreencast":
                raise CDP.CdpError(CDP.METHOD_CODE, "unsupported")
            return {"data": base64.b64encode(raw).decode()}

        def close(self):
            self.closed = True

    session = _Session()
    page = {"id": "AAA111", "title": "Synthetic", "url": "https://x.test/a",
            "type": "page"}
    monkeypatch.setattr(BT, "_resolve_target", lambda cfg, t="": page)
    monkeypatch.setattr(BT, "_target_session", lambda p: session)
    out = BT.screencast(cfg)
    assert out["state"] == "OK"
    assert out["mode"] == "screenshot-fallback"
    assert base64.b64decode(out["image_base64"]) == raw
    assert "Page.captureScreenshot" in session.calls
