"""Packet 8U/D20 — bounded PDF + transcript extraction over files.py.

Hermetic: project folders under tmp_path, isolated XDG/state, no network,
no real PDF dependency (the optional extractor is faked through a
module-level seam). Proves confinement/escape, size caps, unsupported and
unavailable typed refusals, a real txt/md extraction with sha256 and
truncation, bounded transcript parsing, confirmation, and emergency stop.
"""
from __future__ import annotations

import hashlib
import sys
import types

import pytest

import voicebridge.doc_extract as DE


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _project(cfg, tmp_path, name="Site"):
    from voicebridge import projects as PJ

    folder = tmp_path / "work" / "site"
    folder.mkdir(parents=True, exist_ok=True)
    created = PJ.create(cfg, name, [str(folder)], requested_by="cli:user",
                        confirmed=True)
    slug = created["project"]["slug"]
    PJ.use(cfg, slug, requested_by="cli:user", confirmed=True)
    return folder


def _fake_pdf_module(monkeypatch, pages):
    class _Page:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class _Reader:
        def __init__(self, path):
            self.pages = [_Page(text) for text in pages]

    module = types.ModuleType("fakepdf8u")
    module.PdfReader = _Reader
    monkeypatch.setitem(sys.modules, "fakepdf8u", module)
    monkeypatch.setattr(DE, "PDF_BACKENDS", (("fakepdf8u", "PdfReader"),))


def test_status_and_kinds(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    st = DE.status(cfg)
    assert st["state"] == "OK"
    assert st["kinds"] == list(DE.SUPPORTED_KINDS)
    assert set(DE.kinds(cfg)["kinds"]) == set(DE.SUPPORTED_KINDS)
    assert st["pdf"]["available"] == bool(DE.pdf_backend())


def test_txt_extraction_sha_and_truncation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    raw = ("line one\nline two\n" * 10).encode("utf-8")
    (folder / "notes.txt").write_bytes(raw)
    out = DE.extract(cfg, str(folder / "notes.txt"), max_chars=20,
                     confirmed=True)
    assert out["kind"] == "txt"
    assert out["sha256"] == hashlib.sha256(raw).hexdigest()
    assert out["truncated"] is True
    assert out["char_count"] == 20
    assert out["text"] == raw.decode()[:20]
    assert out["trust"] == "untrusted-file-text"
    full = DE.extract(cfg, str(folder / "notes.txt"), confirmed=True)
    assert full["truncated"] is False
    assert full["char_count"] == len(raw.decode())


def test_md_extraction_redacts_secrets(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    (folder / "readme.md").write_text(
        "# Title\ntoken=supersecret8u\nbearer sk-abcdef1234567890\n")
    out = DE.extract(cfg, str(folder / "readme.md"), confirmed=True)
    assert out["kind"] == "md"
    assert "supersecret8u" not in out["text"]
    assert "sk-abcdef1234567890" not in out["text"]
    assert "# Title" in out["text"]


def test_transcript_segments(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    (folder / "meeting.transcript").write_text(
        "[00:00:01] Alice: hello there\n"
        "[00:00:04] Bob: hi alice\n"
        "\n"
        "00:00:07.000 --> 00:00:09.000\n"
        "Alice: bye now\n")
    out = DE.extract(cfg, str(folder / "meeting.transcript"), confirmed=True)
    assert out["kind"] == "transcript"
    assert len(out["segments"]) == 3
    assert out["segments"][0]["time"] == "00:00:01"
    assert "hello there" in out["segments"][0]["text"]
    assert out["segments"][2]["time"] == "00:00:07.000"
    assert DE.render(out).count("\n") == 2


def test_unsupported_kind_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    (folder / "data.xyz").write_text("just text\n")
    with pytest.raises(DE.DocExtractError) as exc:
        DE.extract(cfg, str(folder / "data.xyz"), confirmed=True)
    assert exc.value.code == DE.UNSUPPORTED_CODE


def test_pdf_unavailable_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    (folder / "doc.pdf").write_bytes(b"%PDF-1.4\n1 0 obj\nendobj\n")
    monkeypatch.setattr(DE, "PDF_BACKENDS", ())
    with pytest.raises(DE.DocExtractError) as exc:
        DE.extract(cfg, str(folder / "doc.pdf"), confirmed=True)
    assert exc.value.code == DE.UNAVAILABLE_CODE
    assert "pypdf" in exc.value.detail
    assert DE.status(cfg)["pdf"]["available"] is False


def test_pdf_available_with_optional_backend(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    raw = b"%PDF-1.4\nfake pdf bytes\n"
    (folder / "doc.pdf").write_bytes(raw)
    _fake_pdf_module(monkeypatch, ["alpha page token=deadbeef", "beta page"])
    out = DE.extract(cfg, str(folder / "doc.pdf"), confirmed=True)
    assert out["kind"] == "pdf"
    assert out["page_count"] == 2
    assert "alpha page" in out["text"] and "beta page" in out["text"]
    assert "deadbeef" not in out["text"]
    assert out["sha256"] == hashlib.sha256(raw).hexdigest()
    assert DE.status(cfg)["pdf"]["available"] is True
    one = DE.extract(cfg, str(folder / "doc.pdf"), page=2, confirmed=True)
    assert "beta page" in one["text"] and "alpha page" not in one["text"]
    with pytest.raises(DE.DocExtractError) as exc:
        DE.extract(cfg, str(folder / "doc.pdf"), page=9, confirmed=True)
    assert exc.value.code == DE.BAD_INPUT_CODE


def test_confinement_escape_and_traversal(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("private\n")
    for bad in (str(outside), "/etc/hostname",
                str(folder / ".." / "outside.txt")):
        with pytest.raises(DE.DocExtractError) as exc:
            DE.extract(cfg, bad, confirmed=True)
        assert exc.value.code == DE.ESCAPE_CODE, bad
    link = folder / "evil.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        return
    with pytest.raises(DE.DocExtractError) as exc:
        DE.extract(cfg, str(link), confirmed=True)
    assert exc.value.code == DE.ESCAPE_CODE


def test_too_large_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    from voicebridge import files as FI

    big = folder / "big.txt"
    with open(big, "w") as fh:
        fh.write("x" * (FI.MAX_FILE_BYTES + 8))
    with pytest.raises(DE.DocExtractError) as exc:
        DE.extract(cfg, str(big), confirmed=True)
    assert exc.value.code == DE.TOO_LARGE_CODE


def test_confirm_required(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    (folder / "notes.txt").write_text("hello\n")
    with pytest.raises(DE.DocExtractError) as exc:
        DE.extract(cfg, str(folder / "notes.txt"))
    assert exc.value.code == DE.CONFIRM_CODE


def test_estop_fails_closed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    (folder / "notes.txt").write_text("hello\n")
    from voicebridge.power import power_control_store

    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        with pytest.raises(DE.DocExtractError) as exc:
            DE.extract(cfg, str(folder / "notes.txt"), confirmed=True)
        assert exc.value.code == DE.ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")


# --------------------------------------- OC-D20: zero-dependency PDF path
def _synthetic_pdf(pages):
    """Minimal content-stream PDF (uncompressed + FlateDecode variants)."""
    out = bytearray(b"%PDF-1.4\n")
    for number, (content, flate) in enumerate(pages, 1):
        payload = content.encode("latin-1")
        filt = b""
        if flate:
            import zlib
            payload = zlib.compress(payload)
            filt = b"/Filter /FlateDecode "
        out += (f"{number} 0 obj<</Length {len(payload)} ".encode()
                + filt + b">>stream\n")
        out += payload + b"\nendstream\nendobj\n"
    out += b"trailer<<>>\n%%EOF\n"
    return bytes(out)


def test_stdlib_pdf_backend_is_default(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(DE, "PDF_BACKENDS", (("stdlib", "stdlib"),))
    assert DE.pdf_backend() == "stdlib"
    assert DE.status(cfg)["pdf"]["available"] is True


def test_stdlib_pdf_extraction_and_pages(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    raw = _synthetic_pdf([
        ("BT /F1 12 Tf (Hello PDF 8U) Tj ET", False),
        ("BT /F1 12 Tf (Second page token=deadbeef) Tj ET", True),
    ])
    (folder / "doc.pdf").write_bytes(raw)
    monkeypatch.setattr(DE, "PDF_BACKENDS", (("stdlib", "stdlib"),))

    out = DE.extract(cfg, str(folder / "doc.pdf"), confirmed=True)
    assert out["kind"] == "pdf" and out["extractor"] == "stdlib"
    assert out["page_count"] == 2
    assert "Hello PDF 8U" in out["text"]
    assert "Second page" in out["text"]
    assert "deadbeef" not in out["text"]  # PDF text is redacted evidence
    assert out["sha256"] == hashlib.sha256(raw).hexdigest()
    assert out["trust"] == "untrusted-file-text"

    one = DE.extract(cfg, str(folder / "doc.pdf"), page=2, confirmed=True)
    assert "Second page" in one["text"] and "Hello" not in one["text"]
    with pytest.raises(DE.DocExtractError) as exc:
        DE.extract(cfg, str(folder / "doc.pdf"), page=9, confirmed=True)
    assert exc.value.code == DE.BAD_INPUT_CODE


def test_stdlib_pdf_requires_content_streams(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    (folder / "empty.pdf").write_bytes(b"%PDF-1.4\nno streams here\n")
    monkeypatch.setattr(DE, "PDF_BACKENDS", (("stdlib", "stdlib"),))
    with pytest.raises(DE.DocExtractError) as exc:
        DE.extract(cfg, str(folder / "empty.pdf"), confirmed=True)
    assert exc.value.code == DE.BAD_INPUT_CODE
