"""Packet 8U/D20 — bounded PDF + transcript extraction over files.py.

Hermetic: project folders under tmp_path, isolated XDG/state, no network,
no real PDF dependency (the optional extractor is faked through a
module-level seam). Proves confinement/escape, size caps, unsupported and
unavailable typed refusals, a real txt/md extraction with sha256 and
truncation, bounded transcript parsing, confirmation, and emergency stop.

Project folders MUST live outside any secret-bearing directory. The 8R
project-root gate (``voicebridge.opencode.policy.resolve_project_root``)
correctly refuses a root whose path contains a component such as ``.local``
— and pytest's per-run basetemp defaults to a scratch dir nested under
``~/.local/state/...`` on this host. The ``tmp_path`` override below only
relocates the *test scratch* to a non-secret temp root; it does not relax
the gate.
"""
from __future__ import annotations

import hashlib
import sys
import types

import pytest

import voicebridge.doc_extract as DE

# Directory components the 8R gate treats as secret-bearing (mirrors
# ``voicebridge.opencode.policy._SECRET_DIR_NAMES``). Kept as a local copy so
# the test does not import production internals just to pick a temp path.
_SECRET_DIR_NAMES = {
    ".ssh", ".gnupg", ".opencode", ".config", ".local", ".password-store",
    ".aws", ".qubit",
}


def _is_safe_root(path) -> bool:
    """True when no path component of ``path`` is secret-bearing."""
    from pathlib import Path
    try:
        parts = {p.lower() for p in Path(path).resolve().parts}
    except OSError:
        return False
    return not (parts & _SECRET_DIR_NAMES)


@pytest.fixture
def tmp_path():
    """A per-test temp dir the 8R project-root gate will accept.

    Pytest's default ``tmp_path`` is derived from ``TMPDIR``, which this
    repo's ``tests/conftest.py`` points at a per-run basetemp under the user
    state scratch dir (``~/.local/state/...``). That path contains ``.local``,
    so ``voicebridge.projects.create`` correctly refuses a workspace folder
    inside it. Re-home the scratch under the first non-secret temp root; the
    gate itself is untouched.
    """
    import os
    import shutil
    import tempfile
    import uuid
    from pathlib import Path

    candidates = [os.environ.get("QUBIT_SAFE_TEST_TMPROOT"),
                  tempfile.gettempdir(), "/var/tmp", "/tmp"]
    base = next((c for c in candidates if c and _is_safe_root(c)), None)
    if base is None:
        pytest.skip("no non-secret-bearing temp root available for the "
                    "project-root gate")
    root = Path(base) / f"qubit-8u-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


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


# ------------------------------- TASK 3: real compressed PDF => pypdf preferred
def _real_compressed_pdf(page_lines):
    """A VALID multi-page PDF whose content streams are genuinely deflated.

    Unlike ``_synthetic_pdf`` (a stub with no xref/font/pages tree), this emits
    a real catalog / Pages / Font tree, per-page objects with a proper xref
    table, and deflate-compressed content streams (the shape reportlab and
    every real writer produce). It exists to prove the extractor against a
    genuinely compressed document, not a hand-built minimal stub.
    """
    import zlib

    def content(lines):
        parts = ["BT", "/F1 14 Tf", "72 720 Td", "16 TL"]
        for i, line in enumerate(lines):
            safe = (line.replace("\\", r"\\").replace("(", r"\(")
                    .replace(")", r"\)"))
            parts.append(f"({safe}) Tj")
            if i != len(lines) - 1:
                parts.append("T*")
        parts.append("ET")
        return ("\n".join(parts)).encode("latin-1")

    n = len(page_lines)
    page_nums, content_nums, next_num = [], [], 4
    for _ in range(n):
        page_nums.append(next_num)
        next_num += 1
        content_nums.append(next_num)
        next_num += 1
    total = next_num - 1

    objs = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_nums)}] "
            f"/Count {n} >>").encode(),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for idx in range(n):
        objs[page_nums[idx]] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents "
            f"{content_nums[idx]} 0 R >>").encode()
        comp = zlib.compress(content(page_lines[idx]), 9)
        objs[content_nums[idx]] = (
            b"<< /Length " + str(len(comp)).encode()
            + b" /Filter /FlateDecode >>\nstream\n" + comp
            + b"\nendstream")

    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num in range(1, total + 1):
        offsets[num] = len(buf)
        buf += f"{num} 0 obj\n".encode() + objs[num] + b"\nendobj\n"
    xref = len(buf)
    buf += f"xref\n0 {total + 1}\n".encode() + b"0000000000 65535 f \n"
    for num in range(1, total + 1):
        buf += ("%010d 00000 n \n" % offsets[num]).encode()
    buf += (f"trailer\n<< /Size {total + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(buf)


_REAL_PAGES = [
    ["Qubit extraction proof page one.",
     "The quick brown fox jumps over the lazy dog.",
     "Value alpha=111 for the record."],
    ["Qubit extraction proof page two.",
     "Compressed content streams on every page.",
     "Value beta=222 for the record."],
    ["Qubit extraction proof page three.",
     "Multi-page traversal must reach all pages.",
     "Value gamma=333 for the record."],
]


def test_real_compressed_pdf_is_genuinely_deflated():
    raw = _real_compressed_pdf(_REAL_PAGES)
    assert raw.count(b"/FlateDecode") == len(_REAL_PAGES)
    # The text must NOT be recoverable by plaintext scan — it is compressed.
    assert b"quick brown" not in raw
    assert b"extraction proof page one" not in raw


def test_pypdf_preferred_over_stdlib(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    pytest.importorskip("pypdf")
    # Live preference order resolves pypdf ahead of the stdlib fallback.
    assert DE.pdf_backend() == "pypdf"
    assert DE.status(cfg)["pdf"]["backend"] == "pypdf"
    # Force the stdlib-only order and confirm the fallback is still reachable.
    monkeypatch.setattr(DE, "PDF_BACKENDS", (("stdlib", "stdlib"),))
    assert DE.pdf_backend() == "stdlib"


def test_pypdf_extracts_real_compressed_multipage(tmp_path, monkeypatch):
    """pypdf returns correct text from a genuinely compressed multi-page PDF."""
    cfg = _cfg(tmp_path, monkeypatch)
    pytest.importorskip("pypdf")
    folder = _project(cfg, tmp_path)
    raw = _real_compressed_pdf(_REAL_PAGES)
    (folder / "report.pdf").write_bytes(raw)
    out = DE.extract(cfg, str(folder / "report.pdf"), confirmed=True)
    assert out["extractor"] == "pypdf"
    assert out["page_count"] == 3
    assert "Qubit extraction proof page one." in out["text"]
    assert "Qubit extraction proof page two." in out["text"]
    assert "Qubit extraction proof page three." in out["text"]
    assert "quick brown fox" in out["text"]
    assert "page one" in out["text"]
    assert "page three" in out["text"]
    assert out["sha256"] == hashlib.sha256(raw).hexdigest()
    # Single-page selection still works through the backend.
    one = DE.extract(cfg, str(folder / "report.pdf"), page=2, confirmed=True)
    assert "page two" in one["text"] and "page one" not in one["text"]


def test_stdlib_degrades_on_real_compressed_pdf(tmp_path, monkeypatch):
    """The stdlib scraper degrades a real compressed multi-page PDF.

    This is the regression that motivates preferring pypdf: on a valid
    multi-page compressed document the stdlib scraper either raises a typed
    refusal or loses the per-line structure that pypdf reconstructs (it joins
    ``Tj`` runs with no separator). Either outcome proves stdlib must not be
    the only path.
    """
    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    raw = _real_compressed_pdf(_REAL_PAGES)
    (folder / "report.pdf").write_bytes(raw)
    monkeypatch.setattr(DE, "PDF_BACKENDS", (("stdlib", "stdlib"),))
    try:
        out = DE._extract_pdf_stdlib(raw, DE.DEFAULT_MAX_CHARS, None)
    except DE.DocExtractError as exc:
        # A typed refusal is an acceptable "degraded" outcome.
        assert exc.code == DE.BAD_INPUT_CODE
        return
    assert out["extractor"] == "stdlib"
    # pypdf reconstructs each source line on its own line...
    pypdf_text = str(__import__("pypdf").PdfReader(
        str(folder / "report.pdf")).pages[0].extract_text() or "")
    assert "quick brown fox jumps over the lazy dog." in pypdf_text
    # ...while stdlib flattens the line break before it: the two adjacent
    # drawing runs are concatenated with no separator.
    assert "proof page one.The quick brown fox" in out["text"]
    assert "proof page one.\nThe quick brown fox" not in out["text"]


def test_stdlib_fails_on_reportlab_compressed_pdf(tmp_path, monkeypatch):
    """A reportlab-written compressed PDF: stdlib cannot extract, pypdf can.

    reportlab writes content streams that the stdlib regex scraper cannot
    recover (typed DOC_EXTRACT_BAD_INPUT), while pypdf reads all pages — the
    real-world case the pypdf dependency exists to fix. Skipped when reportlab
    (a dev-only generator, not a runtime dependency) is absent.
    """
    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas  # type: ignore

    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    lines = [["Reportlab compressed page one.", "quick brown fox jumps over"],
             ["Reportlab compressed page two.", "second page extractable text"],
             ["Reportlab compressed page three.", "third page closing marker"]]

    def _reportlab_pdf(path):
        c = canvas.Canvas(str(path))
        for page in lines:
            y = 720
            for ln in page:
                c.drawString(72, y, ln)
                y -= 24
            c.showPage()
        c.save()

    # Build in the approved folder, then extract through doc_extract.
    out_path = folder / "generated.pdf"
    _reportlab_pdf(out_path)
    raw = out_path.read_bytes()
    assert b"quick brown fox" not in raw          # genuinely compressed
    assert raw.count(b"/FlateDecode") >= 1

    # stdlib backend: typed refusal on this real-world document.
    with pytest.raises(DE.DocExtractError) as exc:
        DE._extract_pdf_stdlib(raw, DE.DEFAULT_MAX_CHARS, None)
    assert exc.value.code == DE.BAD_INPUT_CODE

    # pypdf backend: full correct text.
    out = DE.extract(cfg, str(out_path), confirmed=True)
    assert out["extractor"] == "pypdf"
    assert out["page_count"] == 3
    assert "quick brown fox jumps over" in out["text"]
    assert "second page extractable text" in out["text"]
    assert "third page closing marker" in out["text"]
