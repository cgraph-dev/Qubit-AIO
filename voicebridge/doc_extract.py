"""Packet 8U/D20 — bounded PDF + transcript extraction over ``files.py``.

Read-only document extraction confined to the project folders approved by
``files.py``. Nothing here writes, launches, or reaches the network. Plain
text kinds (txt/md/log/transcript) are decoded, redacted, and clipped; PDFs
use the text layer only and only through an already-installed optional
extractor (pypdf / PyPDF2). When no PDF extractor is present the module
returns a typed ``DOC_EXTRACT_UNAVAILABLE`` with install guidance and never
fabricates text. Everything else is a typed ``DOC_EXTRACT_UNSUPPORTED``.

Provenance: Tool/media delta D20 in ``docs/AUDIT_OPENCLAW_V2026_9_4_DELTA.md``
(upstream ``src/agents/tools/pdf-tool*``, ``transcripts-tool-result.ts``).
"""
from __future__ import annotations

import hashlib
import importlib
import re
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CONFIRM_CODE = "DOC_EXTRACT_CONFIRM_REQUIRED"
UNAVAILABLE_CODE = "DOC_EXTRACT_UNAVAILABLE"
UNSUPPORTED_CODE = "DOC_EXTRACT_UNSUPPORTED"
BAD_INPUT_CODE = "DOC_EXTRACT_BAD_INPUT"
TOO_LARGE_CODE = "DOC_EXTRACT_TOO_LARGE"
ESCAPE_CODE = "DOC_EXTRACT_ESCAPE"
ESTOP_CODE = "DOC_EXTRACT_ESTOP"

DEFAULT_MAX_CHARS = 20000
MAX_MAX_CHARS = 20000
MAX_PAGES = 50
MAX_SEGMENTS = 500
MAX_SEGMENT_CHARS = 2000

KIND_EXTENSIONS: Dict[str, Tuple[str, ...]] = {
    "pdf": (".pdf",),
    "txt": (".txt", ".text"),
    "md": (".md", ".markdown", ".mdown"),
    "log": (".log",),
    "transcript": (".transcript", ".trans", ".vtt", ".srt"),
}
PLAIN_KINDS = ("txt", "md", "log", "transcript")
SUPPORTED_KINDS = ("pdf",) + PLAIN_KINDS

PDF_BACKENDS: Tuple[Tuple[str, str], ...] = (
    ("pypdf", "PdfReader"),
    ("PyPDF2", "PdfReader"),
    ("stdlib", "stdlib"),
)

_STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
_PDF_TOKEN_RE = re.compile(
    r"\((?:\\.|[^\\()])*\)|\bTJ\b|\bTj\b|\bTd\b|\bTD\b|\bT\*\b|\bET\b")
_PDF_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b",
                "f": "\f", "(": "(", ")": ")", "\\": "\\"}

_SIMPLE_TS = re.compile(
    r"^\s*\[?(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?)\]?\s*(.*)$")
_RANGE_TS = re.compile(
    r"^\s*(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?)\s*-->\s*\S+.*$")


class DocExtractError(Exception):
    """Typed document-extraction failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: Any) -> str:
    from .redaction import redact_text

    try:
        return redact_text(str(text or ""))
    except Exception:
        return "?"


def _audit(op: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event

        log_event(f"doc_extract.{op}",
                  **{k: str(v)[:160] for k, v in detail.items()})
    except Exception:
        pass


def _estop(cfg: Any) -> bool:
    try:
        from .plugins import estop_engaged

        return bool(estop_engaged(cfg))
    except Exception:
        return True


def kind_for_path(path: Any) -> str:
    """Map a filename suffix to a supported kind ("" = unsupported)."""
    suffix = Path(str(path or "")).suffix.lower()
    for kind, extensions in KIND_EXTENSIONS.items():
        if suffix in extensions:
            return kind
    return ""


def pdf_backend() -> str:
    """Name of an installed optional PDF text extractor ("" = unavailable).

    ``stdlib`` is the always-available zero-dependency fallback: it reads
    uncompressed and FlateDecode content streams (the format most simple
    text PDFs use) when no third-party extractor is installed.
    """
    for module_name, attr in PDF_BACKENDS:
        if module_name == "stdlib":
            return "stdlib"
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        if hasattr(module, attr):
            return module_name
    return ""


def _pdf_guidance() -> str:
    return ("no PDF text extractor is installed; install one (for example "
            "`pip install pypdf`) to read the PDF text layer. Qubit will not "
            "fabricate text. Plain txt/md/log/transcript files still work.")


def _resolve(cfg: Any, path: str):
    from . import files as FI

    try:
        target = FI._resolve_inside(cfg, path)
        FI._refuse_special(target)
    except FI.FilesError as exc:
        mapping = {
            FI.OUTSIDE_ROOT_CODE: ESCAPE_CODE,
            FI.TOO_LARGE_CODE: TOO_LARGE_CODE,
            FI.BAD_INPUT_CODE: BAD_INPUT_CODE,
            FI.NOT_FOUND_CODE: BAD_INPUT_CODE,
            FI.BINARY_CODE: BAD_INPUT_CODE,
            FI.IO_CODE: BAD_INPUT_CODE,
        }
        code = mapping.get(exc.code, BAD_INPUT_CODE)
        raise DocExtractError(code, exc.detail) from exc
    return target, FI


def _bounded_limit(max_chars: Any) -> int:
    try:
        limit = int(max_chars)
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_CHARS
    return max(1, min(limit, MAX_MAX_CHARS))


def _stat_size(target: Path, FI: Any) -> int:
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise DocExtractError(BAD_INPUT_CODE, f"cannot stat file ({exc})")
    if size <= 0:
        raise DocExtractError(BAD_INPUT_CODE, "file is empty")
    if size > FI.MAX_FILE_BYTES:
        raise DocExtractError(
            TOO_LARGE_CODE,
            f"file is {size} bytes (limit {FI.MAX_FILE_BYTES})")
    return size


def _read_raw(target: Path) -> bytes:
    try:
        return target.read_bytes()
    except OSError as exc:
        raise DocExtractError(BAD_INPUT_CODE, f"cannot read file ({exc})")


def _parse_transcript(text: str) -> List[Dict[str, Any]]:
    """Parse a simple timestamped transcript into bounded segments.

    Handles ``[00:01:02] speaker: text`` / ``00:01:02 text`` lines and
    ``00:00:01,000 --> 00:00:03,000`` cue blocks (VTT/SRT). Text without a
    timestamp is not invented into a segment.
    """
    segments: List[Dict[str, Any]] = []
    pending_time: Optional[str] = None
    pending: List[str] = []

    def _flush() -> None:
        nonlocal pending_time, pending
        if pending_time is not None:
            body = " ".join(pending).strip()
            if body:
                segments.append({"time": pending_time,
                                 "text": body[:MAX_SEGMENT_CHARS]})
        pending_time, pending = None, []

    for raw_line in text.splitlines():
        range_match = _RANGE_TS.match(raw_line)
        if range_match:
            _flush()
            pending_time = range_match.group(1)
            continue
        simple = _SIMPLE_TS.match(raw_line)
        if simple and (":" in simple.group(1)) and simple.group(2).strip():
            _flush()
            pending_time = simple.group(1)
            pending = [simple.group(2).strip()]
        elif simple and (":" in simple.group(1)) and not simple.group(2).strip():
            _flush()
            pending_time = simple.group(1)
        elif pending_time is not None:
            if raw_line.strip():
                pending.append(raw_line.strip())
        if len(segments) >= MAX_SEGMENTS:
            break
    _flush()
    return segments[:MAX_SEGMENTS]


def _pdf_unescape(token: str) -> str:
    out: List[str] = []
    i = 0
    while i < len(token):
        ch = token[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= len(token):
            break
        nxt = token[i]
        if nxt in _PDF_ESCAPES:
            out.append(_PDF_ESCAPES[nxt])
            i += 1
        elif nxt in "01234567":
            j = i
            while j < len(token) and j < i + 3 and token[j] in "01234567":
                j += 1
            try:
                out.append(chr(int(token[i:j], 8)))
            except ValueError:
                pass
            i = j
        elif nxt == "\n":
            i += 1  # escaped line continuation
        else:
            out.append(nxt)
            i += 1
    return "".join(out)


def _pdf_text_from_stream(data: bytes) -> str:
    text = data.decode("latin-1", "replace")
    parts: List[str] = []
    for match in _PDF_TOKEN_RE.finditer(text):
        token = match.group(0)
        if token.startswith("("):
            parts.append(_pdf_unescape(token[1:-1]))
        elif token == "ET":
            parts.append("\n")
    return "".join(parts)


def _decode_stream(blob: bytes, flate: bool) -> bytes:
    if not flate:
        return blob
    for candidate in (blob, blob.lstrip(b"\r\n")):
        try:
            return zlib.decompress(candidate)
        except zlib.error:
            continue
    try:
        return zlib.decompressobj().decompress(blob)
    except Exception:
        return b""


def _pdf_streams(raw: bytes) -> List[Tuple[bytes, bool]]:
    streams: List[Tuple[bytes, bool]] = []
    for match in _STREAM_RE.finditer(raw):
        preceding = raw[max(0, match.start() - 400):match.start()]
        streams.append((match.group(1), b"/FlateDecode" in preceding))
    return streams


def _extract_pdf_stdlib(raw: bytes, limit: int, page: Any
                        ) -> Dict[str, Any]:
    streams = _pdf_streams(raw)
    if not streams:
        raise DocExtractError(BAD_INPUT_CODE,
                              "PDF has no extractable content streams")
    texts = [_pdf_text_from_stream(_decode_stream(blob, flate))
             for blob, flate in streams]
    page_count = len(texts)
    truncated = False
    if page is not None:
        try:
            wanted = int(page)
        except (TypeError, ValueError):
            raise DocExtractError(BAD_INPUT_CODE, "page must be an integer")
        if wanted < 1 or wanted > page_count:
            raise DocExtractError(
                BAD_INPUT_CODE, f"page {wanted} outside 1..{page_count}")
        selected = [texts[wanted - 1]]
    else:
        selected = texts[:MAX_PAGES]
        if page_count > MAX_PAGES:
            truncated = True
    text = _redact("\n".join(selected))
    if len(text) > limit:
        truncated = True
        text = text[:limit]
    return {"text": text, "char_count": len(text), "truncated": truncated,
            "page_count": page_count, "extractor": "stdlib"}


def _extract_pdf(target: Path, raw: bytes, limit: int, page: Any
                 ) -> Dict[str, Any]:
    backend = pdf_backend()
    if not backend:
        raise DocExtractError(UNAVAILABLE_CODE, _pdf_guidance())
    if backend == "stdlib":
        return _extract_pdf_stdlib(raw, limit, page)
    try:
        module = importlib.import_module(backend)
        reader = module.PdfReader(str(target))
        pages = list(reader.pages)
    except DocExtractError:
        raise
    except Exception as exc:
        raise DocExtractError(BAD_INPUT_CODE,
                              f"PDF could not be opened ({exc})")
    page_count = len(pages)
    if page_count <= 0:
        raise DocExtractError(BAD_INPUT_CODE, "PDF has no pages")
    truncated = False
    if page is not None:
        try:
            wanted = int(page)
        except (TypeError, ValueError):
            raise DocExtractError(BAD_INPUT_CODE, "page must be an integer")
        if wanted < 1 or wanted > page_count:
            raise DocExtractError(
                BAD_INPUT_CODE,
                f"page {wanted} outside 1..{page_count}")
        selected = [(wanted, pages[wanted - 1])]
    else:
        selected = list(enumerate(pages[:MAX_PAGES], 1))
        if page_count > MAX_PAGES:
            truncated = True
    parts: List[str] = []
    for _number, item in selected:
        try:
            parts.append(str(item.extract_text() or ""))
        except Exception:
            parts.append("")
    text = _redact("\n".join(parts))
    if len(text) > limit:
        truncated = True
        text = text[:limit]
    return {"text": text, "char_count": len(text), "truncated": truncated,
            "page_count": page_count, "extractor": backend}


def _extract_plain(kind: str, raw: bytes, limit: int) -> Dict[str, Any]:
    if b"\x00" in raw[:8192]:
        raise DocExtractError(BAD_INPUT_CODE,
                              "binary file; text extraction refused")
    text = _redact(raw.decode("utf-8", "replace"))
    truncated = len(text) > limit
    body = text[:limit]
    result: Dict[str, Any] = {"text": body, "char_count": len(body),
                              "truncated": truncated}
    if kind == "transcript":
        result["segments"] = _parse_transcript(body)
    return result


def status(cfg: Any) -> Dict[str, Any]:
    """Read-only extractor capability report (never reads or loads a file)."""
    backend = pdf_backend()
    return {"state": "OK", "schema": "qubit-doc-extract-status/1",
            "kinds": list(SUPPORTED_KINDS),
            "pdf": {"available": bool(backend),
                    "backend": backend or None,
                    "max_pages": MAX_PAGES,
                    "guidance": "" if backend else _pdf_guidance()},
            "max_chars": MAX_MAX_CHARS,
            "trust": "untrusted-file-text"}


def kinds(cfg: Any) -> Dict[str, Any]:
    """Supported document kinds for voice/CLI discovery."""
    return {"state": "OK", "schema": "qubit-doc-extract-kinds/1",
            "kinds": list(SUPPORTED_KINDS)}


def extract(cfg: Any, path: str, *, max_chars: int = DEFAULT_MAX_CHARS,
            page: Optional[int] = None, confirmed: bool = False
            ) -> Dict[str, Any]:
    """Extract one document inside the approved roots (bounded, redacted).

    Requires explicit confirmation, refuses while the emergency stop is
    latched, translates ``files.py`` confinement/size failures into typed
    ``DOC_EXTRACT_*`` codes, and returns a content hash of the raw bytes.
    """
    if _estop(cfg):
        raise DocExtractError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise DocExtractError(
            CONFIRM_CODE,
            "document extraction requires explicit confirmation")
    target, FI = _resolve(cfg, path)
    if target.is_dir():
        raise DocExtractError(BAD_INPUT_CODE,
                              "not a file; use the files listing")
    kind = kind_for_path(target)
    if not kind:
        raise DocExtractError(
            UNSUPPORTED_CODE,
            "unsupported document kind; supported: " +
            ", ".join(SUPPORTED_KINDS))
    size = _stat_size(target, FI)
    raw = _read_raw(target)
    limit = _bounded_limit(max_chars)
    if kind == "pdf":
        result = _extract_pdf(target, raw, limit, page)
    else:
        if page is not None:
            raise DocExtractError(BAD_INPUT_CODE,
                                  "page is only valid for PDF documents")
        result = _extract_plain(kind, raw, limit)
    digest = hashlib.sha256(raw).hexdigest()
    result.update({"state": "OK", "schema": "qubit-doc-extract/1",
                   "kind": kind, "path": _redact(str(target))[:300],
                   "bytes": size, "sha256": digest,
                   "trust": "untrusted-file-text"})
    _audit("extract", {"kind": kind, "sha256": digest,
                       "char_count": result["char_count"],
                       "truncated": result["truncated"]})
    return result


def render(result: Dict[str, Any]) -> str:
    """Render an extraction result for voice/CLI (bounded)."""
    body = str(result.get("text", "") or "")
    if result.get("kind") == "transcript":
        lines = [f"{seg.get('time', '')} {seg.get('text', '')}".strip()
                 for seg in result.get("segments", []) or []]
        return "\n".join(lines)[:DEFAULT_MAX_CHARS]
    return body
