"""Qubit approved vision analysis path.

Qubit ``vision describe IMAGE`` attaches a local image to a single query
(native vision model or pre-description). Qubit has no multimodal
plumbing in the resident runner — and the 4B contract forbids silent
model swaps — so the Qubit-native path runs approved vision work inside
the sanctioned 4D ``deep_tier_cycle`` (expire resident → analyze →
restore + warm probe, always restored via ``finally``):

* ``describe`` — validate (exists, magic bytes PNG/JPEG/GIF/WEBP/BMP,
  size cap) → capability check (vision model present in ``/api/tags``
  with digest) → VRAM guard (refuse before any load) → explicit,
  confirmed analysis on the vision model with ``keep_alive: 0`` (never
  left resident) → redacted reply + audit (never image bytes) → resident
  restore report in the receipt. No temp files by design (input is read
  in place, base64 bounded by the size cap).
* ``status`` — read-only backend probe (model presence, VRAM verdict;
  never loads anything).
* Emergency stop is checked before the swap and after analysis (a
  mid-run latch discards the reply; the resident is still restored).
* Cancellation: bounded request timeout; nothing persists, so rollback
  is the resident-restore report (automatic) plus no state written.

Model routing stays brokered: the call uses the configured Ollama
endpoint (same governor path as chat), the model comes from
``intent.vision.model`` (default ``qwen3-vl:8b`` — also the ``turbo``
tier), and nothing executes outside the gateway response.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_VISION_MODEL = "qwen3-vl:8b"
DEFAULT_MAX_MB = 20
DEFAULT_TIMEOUT_S = 120.0
MIN_TIMEOUT_S = 10.0
MAX_TIMEOUT_S = 600.0
MAX_PROMPT_CHARS = 2000

CONFIRM_CODE = "VISION_CONFIRM_REQUIRED"
ESTOP_CODE = "VISION_ESTOP"
BAD_IMAGE_CODE = "VISION_BAD_IMAGE"
NO_BACKEND_CODE = "VISION_NO_BACKEND"
VRAM_CODE = "VISION_VRAM_SHORT"
TIMEOUT_CODE = "VISION_TIMEOUT"
UPSTREAM_CODE = "VISION_UPSTREAM"

# magic-byte prefixes (and RIFF/WEBP special case below).
MAGIC = {
    "png": bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)),
    "jpeg": bytes((0xFF, 0xD8, 0xFF)),
    "gif87": b"GIF87a",
    "gif89": b"GIF89a",
    "bmp": b"BM",
}


class VisionError(Exception):
    """Typed vision failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def _settings(cfg: Any) -> Dict[str, Any]:
    get = (lambda *p, default=None: cfg.get(*p, default=default)) \
        if hasattr(cfg, "get") else (lambda *p, default=None: default)
    try:
        timeout = float(get("intent", "vision", "timeout_s",
                            default=DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_S
    try:
        max_mb = float(get("intent", "vision", "max_mb", default=DEFAULT_MAX_MB))
    except (TypeError, ValueError):
        max_mb = DEFAULT_MAX_MB
    return {
        "model": str(get("intent", "vision", "model",
                         default=DEFAULT_VISION_MODEL) or DEFAULT_VISION_MODEL),
        "max_mb": max(1.0, min(max_mb, 100.0)),
        "timeout_s": max(MIN_TIMEOUT_S, min(timeout, MAX_TIMEOUT_S)),
    }


def _runner(cfg: Any) -> Tuple[str, str, str, int]:
    """(base_url, fast_alias, canonical_ctx, fast_url)."""
    from .runner_contract import DEFAULT_URL, resolve

    try:
        rc = resolve(cfg)
        return rc.url or DEFAULT_URL, rc.alias, rc.num_ctx, rc.url or DEFAULT_URL
    except Exception:
        return DEFAULT_URL, "qubit-brain4", 4096, DEFAULT_URL


def detect_format(head: bytes) -> Optional[str]:
    """Image format from magic bytes (None = unsupported/unknown)."""
    for fmt, magic in MAGIC.items():
        if head.startswith(magic):
            return "gif" if fmt.startswith("gif") else fmt
    if head.startswith(b"RIFF") and len(head) >= 12 and head[8:12] == b"WEBP":
        return "webp"
    return None


def validate_image(path: str, max_mb: float) -> Dict[str, Any]:
    """Validate an image file without loading a model (bounded reads)."""
    raw = (path or "").strip()
    if not raw:
        raise VisionError(BAD_IMAGE_CODE, "image path is required")
    p = Path(os.path.expanduser(raw))
    try:
        if not p.is_file() or p.is_symlink():
            raise VisionError(BAD_IMAGE_CODE,
                              f"not a regular file: {raw[:160]}")
        size = p.stat().st_size
    except OSError as exc:
        raise VisionError(BAD_IMAGE_CODE,
                          f"image unreadable: {exc}") from exc
    if size <= 0 or size > max_mb * 1024 * 1024:
        raise VisionError(BAD_IMAGE_CODE,
                          f"image is {size} bytes (limit {max_mb:.0f}MB)")
    try:
        with open(p, "rb") as fh:
            head = fh.read(16)
    except OSError as exc:
        raise VisionError(BAD_IMAGE_CODE,
                          f"image unreadable: {exc}") from exc
    fmt = detect_format(head)
    if fmt is None:
        raise VisionError(BAD_IMAGE_CODE,
                          "unsupported image format (png/jpeg/gif/webp/bmp only)")
    digest = hashlib.sha256()
    try:
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
    except OSError as exc:
        raise VisionError(BAD_IMAGE_CODE,
                          f"image unreadable: {exc}") from exc
    return {"path": str(p), "bytes": size, "format": fmt,
            "sha256": digest.hexdigest()}


def _tags(url: str, timeout_s: float = 10.0) -> List[Dict[str, Any]]:
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/tags", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode())
    except Exception as exc:
        raise VisionError(UPSTREAM_CODE,
                          f"model endpoint unreachable: {exc}") from exc
    models = body.get("models", [])
    return models if isinstance(models, list) else []


def find_vision_model(url: str, want: str,
                      timeout_s: float = 10.0) -> Dict[str, Any]:
    """Locate the vision model in /api/tags (read-only, never loads)."""
    base = (want or "").strip().split(":")[0].lower()
    for entry in _tags(url, timeout_s):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", ""))
        if name.split(":")[0].lower() == base and base:
            try:
                size_mib = float(entry.get("size", 0) or 0) / (1024 * 1024)
            except (TypeError, ValueError):
                size_mib = 0.0
            return {"name": name, "size_mib": size_mib,
                    "digest": str(entry.get("digest", ""))[:40]}
    raise VisionError(NO_BACKEND_CODE,
                      f"vision model {want!r} not installed; pull it with "
                      f"`ollama pull {want}` on a machine with headroom, or "
                      f"point intent.vision.model at an installed "
                      f"vision-capable model")


def _guard(size_mib: float) -> int:
    """VRAM headroom check (seam for hermetic tests)."""
    from .vram_guard import guard_load

    return guard_load(size_mib, what="vision model")


def _extract_reply(body: Any) -> str:
    from .local_llm import _extract_reply as _content_only

    try:
        return _content_only(body) or ""
    except Exception:
        return ""


def _analyze(url: str, model: str, image_b64: str, prompt: str,
             timeout_s: float) -> str:
    text = (prompt or "").strip()[:MAX_PROMPT_CHARS] or \
        "Describe this image factually: main subjects, text visible, setting."
    payload = {"model": model, "stream": False, "keep_alive": 0,
               "messages": [{"role": "user", "content": text,
                             "images": [image_b64]}]}
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode())
    except TimeoutError as exc:
        raise VisionError(TIMEOUT_CODE,
                          f"vision request timed out after {timeout_s:.0f}s") from exc
    except Exception as exc:
        # urllib raises URLError (wrapping socket.timeout) on timeouts.
        if "timed out" in str(exc).lower():
            raise VisionError(TIMEOUT_CODE,
                              f"vision request timed out after {timeout_s:.0f}s"
                              ) from exc
        raise VisionError(UPSTREAM_CODE,
                          f"vision provider failed: {exc}") from exc
    reply = _extract_reply(body)
    if not reply:
        raise VisionError(UPSTREAM_CODE, "vision model returned no content")
    return reply


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"vision.{op}", actor=actor, **detail)
    except Exception:
        pass


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": ESTOP_CODE, "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop "
                           "is latched; reset it before vision analysis.",
            "owner": "qubit-policy",
            "rollback": "resident model untouched; nothing to analyze"}


def status(cfg: Any, timeout_s: float = 10.0) -> Dict[str, Any]:
    """Vision backend state (read-only; never loads a model)."""
    settings = _settings(cfg)
    url, _, _, _ = _runner(cfg)
    try:
        timeout = max(1.0, min(float(timeout_s), 30.0))
    except (TypeError, ValueError):
        timeout = 10.0
    try:
        found = find_vision_model(url, settings["model"], timeout)
    except VisionError as exc:
        if exc.code == NO_BACKEND_CODE:
            return {"state": "NO_BACKEND", "code": exc.code,
                    "schema": "qubit-vision-status/1",
                    "model": settings["model"],
                    "detail": _redact(exc.detail)[:300]}
        return {"state": "UNAVAILABLE", "code": exc.code,
                "schema": "qubit-vision-status/1",
                "model": settings["model"],
                "detail": _redact(exc.detail)[:200]}
    from .vram_guard import free_vram_mib

    free = free_vram_mib()
    verdict = "READY"
    if free is None:
        verdict = "UNMEASURED"
    elif free < found["size_mib"] + 512:
        verdict = "VRAM_SHORT"
    return {"state": verdict, "schema": "qubit-vision-status/1",
            "model": found["name"], "size_mib": round(found["size_mib"], 1),
            "digest": found["digest"], "free_vram_mib": free,
            "max_mb": settings["max_mb"]}


def describe(cfg: Any, image: str, *, prompt: str = "", model: str = "",
             requested_by: str = "cli:user", confirmed: bool = False,
             timeout_s: Optional[float] = None) -> Dict[str, Any]:
    """Analyze one image on the approved vision model (explicit + audited)."""
    if not confirmed:
        raise VisionError(CONFIRM_CODE,
                          "vision describe requires explicit --confirm "
                          "(loads a multi-GB model with resident restore)")
    if estop_engaged(cfg):
        raise VisionError(ESTOP_CODE, "emergency stop is latched")
    settings = _settings(cfg)
    if timeout_s is not None:
        try:
            settings["timeout_s"] = max(MIN_TIMEOUT_S,
                                        min(float(timeout_s), MAX_TIMEOUT_S))
        except (TypeError, ValueError):
            pass
    meta = validate_image(image, settings["max_mb"])
    url, fast_alias, canonical_ctx, _ = _runner(cfg)
    want = (model or "").strip() or settings["model"]
    found = find_vision_model(url, want)
    if found["size_mib"] <= 0:
        raise VisionError(NO_BACKEND_CODE,
                          "vision model reports no size; refusing blind load")
    free = _guard(found["size_mib"])
    with open(meta["path"], "rb") as fh:
        image_b64 = base64.b64encode(fh.read()).decode("ascii")

    from .vram_guard import deep_tier_cycle

    def _work() -> str:
        return _analyze(url, found["name"], image_b64, prompt,
                        settings["timeout_s"])

    t0 = time.monotonic()
    try:
        reply, report = deep_tier_cycle(url, fast_alias, canonical_ctx, _work)
    except VisionError:
        raise
    except Exception as exc:
        raise VisionError(UPSTREAM_CODE,
                          f"vision run failed: {exc}") from exc
    elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
    if estop_engaged(cfg):
        _audit("estop_discard", requested_by,
               {"image_sha256": meta["sha256"], "model": found["name"]})
        raise VisionError(ESTOP_CODE,
                          "emergency stop latched during analysis; reply "
                          "discarded (resident restore: "
                          f"{report.restored})")
    clean_reply = _redact(reply)
    _audit("describe", requested_by,
           {"image_sha256": meta["sha256"], "bytes": meta["bytes"],
            "format": meta["format"], "model": found["name"],
            "prompt": _redact(prompt or "")[:200],
            "restored": report.restored, "restore_ms": report.restore_ms,
            "elapsed_ms": elapsed_ms})
    return {"state": "OK", "schema": "qubit-vision-describe/1",
            "description": clean_reply,
            "image": {"bytes": meta["bytes"], "format": meta["format"],
                      "sha256": meta["sha256"]},
            "model": found["name"], "model_digest": found["digest"],
            "free_vram_mib_before": free,
            "resident_restored": report.restored,
            "restore_ms": report.restore_ms, "elapsed_ms": elapsed_ms}
