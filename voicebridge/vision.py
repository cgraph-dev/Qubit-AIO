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
endpoint (same governor path as chat), and nothing executes outside the
gateway response.

Model authority (single source). The effective vision model is resolved by
:func:`voicebridge.providers.resolver.default_vision_profile` — the SAME
``role="vision"`` ladder the agent tool path uses, so this surface and the
resolver can never disagree. That ladder, highest precedence first, is:

1. an explicit ``model=`` / ``--model`` argument to :func:`describe`;
2. ``auxiliary.vision.model`` (the reference per-task aux pin);
3. ``intent.vision.model`` (the Qubit-native vision surface's own pin);
4. a registered profile that declares ``supports_vision``;
5. a known vision-capable local model that is actually installed;
6. the shipped local VLM (``DEFAULT_VISION_MODEL``).

:func:`find_vision_model` then binds that configured id to the concrete
installed tag: the configured exact id/tag first, then the same family prefix
(preferring ``:latest``), then — for a recognised VLM family or an unset id —
any installed vision-capable model. A configured tag suffix is never silently
dropped for whichever family sibling ``/api/tags`` happens to list first, and a
custom-named configured model that is not installed is a typed refusal rather
than a silent substitution for a different family.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
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
#: A remote (API) vision target needs explicit owner approval before any image
#: leaves the machine. This is the typed refusal when that approval is absent —
#: never a silent local substitution and never a silent upload.
EGRESS_APPROVAL_CODE = "VISION_EGRESS_APPROVAL_REQUIRED"
#: The resolved remote vision profile is not executable under the frontier
#: policy (disabled / not allowlisted / egress denied). Reusing the gateway's
#: own code keeps the refusal consistent with every other frontier surface.
EGRESS_DENIED_CODE = "EGRESS_DENIED"

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


def _vision_authority_model(cfg: Any) -> str:
    """Effective vision model id from the one resolver (the authority).

    Reads :func:`voicebridge.providers.resolver.default_vision_profile` so the
    CLI/dashboard vision surface and the resolver's ``role="vision"`` report the
    SAME model (precedence documented in the module docstring). Best-effort: a
    resolution failure yields ``""`` and the caller falls back to
    ``intent.vision.model`` — this function never raises.
    """
    try:
        from .providers import resolver as _resolver

        profile = _resolver.default_vision_profile(cfg)
        model = str(getattr(profile, "model", "") or "").strip()
        if model:
            return model
    except Exception:  # noqa: BLE001 - resolution failure falls back below
        pass
    return ""


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
    # The configured model comes from the ONE resolver vision ladder, so this
    # surface and ``role="vision"`` agree; ``intent.vision.model`` remains the
    # fallback when resolution is unavailable.
    configured = _vision_authority_model(cfg)
    model = configured or str(get("intent", "vision", "model",
                                  default=DEFAULT_VISION_MODEL)
                              or DEFAULT_VISION_MODEL)
    return {
        "model": model,
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


#: Family prefixes that mark a locally installed model as vision-capable. The
#: last-resort rung of :func:`find_vision_model` when the configured family is
#: not installed at all. Deliberately conservative (a false positive would load
#: a text model for image analysis); the ``vl``/``vlm``/``vision`` name token
#: check catches the rest.
_VISION_FAMILY_HINTS = (
    "qwen3-vl", "qwen2.5-vl", "qwen2-vl", "qwen2.5vl", "qwen3vl",
    "llava", "bakllava", "llama3.2-vision", "minicpm-v", "moondream",
    "cogvlm", "internvl",
)
_VISION_NAME_TOKENS = ("vl", "vlm", "vision")


def _vision_family(name: str) -> str:
    """The family prefix before the first ``:`` (lower-cased)."""
    return str(name or "").split(":", 1)[0].strip().lower()


def _is_known_vision_family(name: str) -> bool:
    """True when the family prefix names a recognised VLM family."""
    family = _vision_family(name)
    return bool(family) and any(
        family == hint or family.startswith(hint) for hint in _VISION_FAMILY_HINTS)


def _looks_vision_capable(name: str) -> bool:
    """True when *name* looks like an installed vision model (family or token)."""
    if _is_known_vision_family(name):
        return True
    tokens = (t for t in re.split(r"[^a-z0-9]+", str(name).lower()) if t)
    return any(token in _VISION_NAME_TOKENS for token in tokens)


def _vision_match_rank(name: str, want: str) -> Optional[int]:
    """Rank an installed *name* against the configured *want* (lower = better).

    ``0`` — the configured exact id/tag (including the implicit ``:latest``);
    ``2`` — the same family prefix; ``3`` — any installed vision-capable model,
    but only when the configured model is itself a recognised VLM family (or
    unset) so a custom-named configured model is never silently swapped for a
    different family; ``None`` — no match. This is the precedence contract: the
    configured tag is honoured before a family sibling, which is honoured
    before a known-VLM fallback.
    """
    nl = str(name or "").strip().lower()
    if not nl:
        return None
    wl = str(want or "").strip().lower()
    if wl:
        if nl == wl:
            return 0
        if ":" not in wl and nl == f"{wl}:latest":
            return 0
        want_family = _vision_family(wl)
        if want_family and _vision_family(nl) == want_family:
            return 2
        # A custom configured family that is not installed is a typed
        # NO_BACKEND, never a silent substitution for a different model.
        if not _is_known_vision_family(wl):
            return None
    if _looks_vision_capable(nl):
        return 3
    return None


def find_vision_model(url: str, want: str,
                      timeout_s: float = 10.0) -> Dict[str, Any]:
    """Locate the best installed match for *want* (read-only, never loads).

    Precedence: the CONFIGURED exact model id/tag first, then the same family
    prefix (preferring ``:latest``), then — for a recognised VLM family or an
    unset id — any installed vision-capable model. A configured tag suffix is
    therefore honoured instead of being dropped for whichever family sibling
    ``/api/tags`` lists first, and a custom configured model that is not
    installed is a typed refusal rather than a silent substitution. Raises the
    typed ``VISION_NO_BACKEND`` only when the endpoint answers but nothing
    matches, and ``VISION_UPSTREAM`` when the endpoint cannot be reached.
    """
    want = (want or "").strip()
    best_rank: Optional[int] = None
    best: Optional[Dict[str, Any]] = None
    for entry in _tags(url, timeout_s):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "") or "").strip()
        rank = _vision_match_rank(name, want)
        if rank is None:
            continue
        # Lower rank wins; within a rank, prefer the implicit ``:latest`` tag,
        # then keep the endpoint's own order (stable and deterministic).
        prefer_latest = name.lower().endswith(":latest")
        best_latest = bool(best) and str(best["name"]).lower().endswith(":latest")
        if best is None or rank < best_rank or (
            rank == best_rank and prefer_latest and not best_latest
        ):
            try:
                size_mib = float(entry.get("size", 0) or 0) / (1024 * 1024)
            except (TypeError, ValueError):
                size_mib = 0.0
            best = {"name": name, "size_mib": size_mib,
                    "digest": str(entry.get("digest", ""))[:40]}
            best_rank = rank
    if best is not None:
        return best
    raise VisionError(NO_BACKEND_CODE,
                      f"vision model {want!r} not installed; pull it with "
                      f"`ollama pull {want}` on a machine with headroom, or "
                      f"point intent.vision.model at an installed "
                      f"vision-capable model")


def _guard(size_mib: float, url: str = "") -> int:
    """VRAM headroom check (seam for hermetic tests).

    The deep-tier cycle expires the resident fast model immediately after
    this check, so its current GPU footprint is spendable here. Without
    counting it, a vision model larger than the gap left by the resident
    tier is refused forever even though the swap exists to make that load
    possible (the RTX 3070 Ti case: 5900 MiB vision model, ~1900 MiB free,
    ~1800 MiB reclaimable).
    """
    from .vram_guard import guard_load, resident_mib

    reclaimable = resident_mib(url) if url else 0
    return guard_load(size_mib, what="vision model", url=url,
                      reclaimable_mib=reclaimable)


def _extract_reply(body: Any) -> str:
    from .local_llm import _extract_reply as _content_only

    try:
        return _content_only(body) or ""
    except Exception:
        return ""


def vision_target(cfg: Any, model: str = "") -> Dict[str, Any]:
    """Describe where the effective vision call would run (never sends).

    Resolves the ``role="vision"`` EffectiveProfile through the one resolver
    and reports whether it is LOCAL (no egress) or REMOTE (API/frontier), plus
    the frontier-policy gate a remote target must pass. This is what the UI
    consults to decide whether the approve/decline popup is required BEFORE any
    image bytes move.

    Never raises: a resolution failure yields ``remote=True`` with
    ``executable=False`` and a typed reason, so the surface asks for approval
    and then fails closed rather than silently assuming local.
    """
    want = (model or "").strip() or _settings(cfg)["model"]
    try:
        from .providers import resolver as _resolver

        eff = _resolver.resolve(cfg, role=_resolver.VISION)
    except Exception as exc:  # noqa: BLE001 - fail closed to "remote+denied"
        return {"remote": True, "executable": False, "model": want,
                "provider": "", "profile_id": "", "kind": "",
                "locality": "external", "egress_required": True,
                "egress_allowed": False, "policy_enabled": False,
                "allowlisted": False, "reason": f"resolver:{type(exc).__name__}"}
    remote = not eff.is_local
    return {
        "remote": remote, "executable": bool(eff.executable),
        "model": eff.model or want, "provider": eff.provider,
        "profile_id": eff.profile_id, "kind": eff.kind,
        "locality": eff.locality, "egress_required": bool(eff.egress_required),
        "egress_allowed": bool(eff.egress_allowed),
        "policy_enabled": bool(eff.policy_enabled),
        "allowlisted": bool(eff.allowlisted),
        "source": eff.source, "base_url": eff.base_url,
        "reason": "",
    }


def _analyze(cfg: Any, model: str, image_b64: str, prompt: str,
             timeout_s: float, *, mime: str = "",
             approve_remote: bool = False) -> str:
    """Run one image analysis through the canonical provider authority.

    The effective ``vision`` profile is resolved by ``providers.resolver``:

    * **Local** (Ollama) — the pre-existing path, unchanged: the image is sent
      as the Ollama ``images: [b64]`` message via ``local_mode="chat_raw"`` and
      ``keep_alive=0`` so the multi-GB model is never left resident.
    * **Remote** (API/frontier) — the image is translated to the OpenAI
      content-block ``image_url`` shape and sent on the frontier route. Egress
      is gated twice: (1) ``approve_remote`` must be true (the in-UI approval),
      else a typed ``VISION_EGRESS_APPROVAL_REQUIRED``; (2) the frontier policy
      (enabled / allowlist / egress) must make the profile executable, else the
      gateway's typed ``EGRESS_DENIED``. There is never a silent local
      substitution for a denied remote target.
    """
    from .providers import gateway as _gateway
    from .providers import vision_payload as _vp

    text = (prompt or "").strip()[:MAX_PROMPT_CHARS] or _vp.DEFAULT_VISION_PROMPT
    target = vision_target(cfg, model)
    remote = bool(target["remote"])
    if remote:
        if not approve_remote:
            raise VisionError(
                EGRESS_APPROVAL_CODE,
                f"vision profile {target['profile_id']!r} "
                f"({target['provider']}/{target['model']}) sends the image "
                f"off-machine; explicit approval is required before upload")
        shape = "openai"
    else:
        shape = "ollama"
    messages = _vp.build_vision_messages(shape, text, image_b64, mime)
    # ``model`` names the resolved profile so run_role executes the SAME
    # target the resolver reported (local alias or remote model id).
    kwargs: Dict[str, Any] = {}
    if not remote:
        kwargs = {"local_mode": "chat_raw", "keep_alive": 0}
    res = _gateway.run_role(
        text, cfg, role="vision", model=target["model"] or model,
        max_tokens=512, timeout_s=timeout_s, messages=messages, **kwargs)
    if not res.ok:
        msg = str(res.message or "")
        if res.code == EGRESS_DENIED_CODE:
            raise VisionError(EGRESS_DENIED_CODE, f"vision egress denied: {msg}")
        if res.code == "TIMEOUT" or "timed out" in msg.lower():
            raise VisionError(TIMEOUT_CODE,
                              f"vision request timed out after {timeout_s:.0f}s")
        raise VisionError(UPSTREAM_CODE, f"vision provider failed: {msg}")
    reply = res.text
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
    from .vram_guard import free_vram_mib, resident_mib

    free = free_vram_mib()
    reclaimable = resident_mib(url)
    verdict = "READY"
    if free is None:
        verdict = "UNMEASURED"
    elif free + reclaimable < found["size_mib"] + 512:
        verdict = "VRAM_SHORT"
    return {"state": verdict, "schema": "qubit-vision-status/1",
            "model": found["name"], "size_mib": round(found["size_mib"], 1),
            "digest": found["digest"], "free_vram_mib": free,
            "resident_reclaimable_mib": reclaimable,
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
    free = _guard(found["size_mib"], url)
    with open(meta["path"], "rb") as fh:
        image_b64 = base64.b64encode(fh.read()).decode("ascii")

    from .vram_guard import deep_tier_cycle

    def _work() -> str:
        return _analyze(cfg, found["name"], image_b64, prompt,
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


# ---------------------------------------------------------------------------
# OC-65: media understanding beyond single-image vision (multi-attachment)
# ---------------------------------------------------------------------------
BATCH_SCHEMA = "qubit-vision-batch/1"
MAX_BATCH_IMAGES = 8


def describe_batch(cfg: Any, images: Any, *, prompt: str = "",
                   model: str = "", requested_by: str = "cli:user",
                   confirmed: bool = False,
                   timeout_s: Optional[float] = None,
                   analyzer: Any = None) -> Dict[str, Any]:
    """Analyze up to :data:`MAX_BATCH_IMAGES` images in one confirmed call.

    Each attachment is validated through the same :func:`validate_image`
    boundary and analyzed via :func:`describe`; a per-attachment failure is
    recorded as a typed per-item error and never aborts the remaining items.
    An empty or oversized batch fails closed.

    ``analyzer`` is an optional injected boundary ``(path, prompt) ->
    description`` used by an operator/host that already owns a vision model;
    it lets the multi-attachment contract be driven without the local runtime.
    """
    if isinstance(images, str):
        items = [p.strip() for p in images.split(",") if p.strip()]
    else:
        try:
            items = [str(p) for p in images]
        except TypeError:
            raise VisionError(BAD_IMAGE_CODE,
                              "images must be a sequence of paths") from None
    if not items:
        raise VisionError(BAD_IMAGE_CODE, "at least one image is required")
    if len(items) > MAX_BATCH_IMAGES:
        raise VisionError(BAD_IMAGE_CODE,
                          f"at most {MAX_BATCH_IMAGES} images per batch")
    results: List[Dict[str, Any]] = []
    for path in items:
        if callable(analyzer):
            try:
                res = analyzer(path, prompt)
                if isinstance(res, dict):
                    desc = str(res.get("description") or res.get("text") or "")
                    sha = str(res.get("sha256", ""))[:128]
                else:
                    desc = str(res)
                    sha = ""
                results.append({"state": "OK", "image": path,
                                "description": _redact(desc)[:8000],
                                "sha256": sha})
            except Exception as exc:
                results.append({"state": "FAILED", "image": path,
                                "code": MEDIA_UNAVAILABLE_CODE,
                                "detail": _redact(str(exc))[:200]})
            continue
        try:
            out = describe(cfg, path, prompt=prompt, model=model,
                           requested_by=requested_by, confirmed=confirmed,
                           timeout_s=timeout_s)
            results.append({"state": "OK", "image": path,
                            "description": out.get("description", ""),
                            "sha256": out.get("image", {}).get("sha256", "")})
        except VisionError as exc:
            results.append({"state": "FAILED", "image": path,
                            "code": exc.code,
                            "detail": _redact(exc.detail)[:200]})
    ok = sum(1 for r in results if r["state"] == "OK")
    return {"state": "OK" if ok else "FAILED", "schema": BATCH_SCHEMA,
            "count": len(results), "succeeded": ok,
            "failed": len(results) - ok, "results": results}


# ---------------------------------------------------------------------------
# OC-67: realtime transcription / voice-call / meeting-bot adapter boundary
# ---------------------------------------------------------------------------
MEDIA_UNAVAILABLE_CODE = "VISION_MEDIA_UNAVAILABLE"
TRANSCRIBE_SCHEMA = "qubit-transcribe/1"
MEETING_SCHEMA = "qubit-meeting-bot/1"


def transcribe(cfg: Any, audio: Any, *, transport: Any = None,
               model: str = "", language: str = "",
               requested_by: str = "cli:user",
               confirmed: bool = False) -> Dict[str, Any]:
    """Transcribe an audio attachment over an injected provider transport.

    ``transport`` is the external ASR boundary: a callable
    ``(request: dict) -> mapping``. Without it the call is a typed
    ``VISION_MEDIA_UNAVAILABLE`` (no fabricated transcript). With it the
    returned text is redacted and bounded. This is the batch/realtime
    transcription contract shared by ``voice_call`` and ``meeting_bot``.
    """
    if estop_engaged(cfg):
        return refuse_estop("transcribe")
    if transport is None:
        return {"state": "UNAVAILABLE", "code": MEDIA_UNAVAILABLE_CODE,
                "schema": TRANSCRIBE_SCHEMA,
                "detail": "no ASR transport injected; use a supervised "
                          "asr-test/tts-test path or inject a provider",
                "owner": "qubit-policy",
                "rollback": "nothing recorded; nothing to roll back"}
    if not confirmed:
        return {"state": "denied", "code": CONFIRM_CODE,
                "schema": TRANSCRIBE_SCHEMA,
                "detail": "transcription requires explicit --confirm",
                "owner": "qubit-policy",
                "rollback": "nothing recorded; nothing to roll back"}
    raw = audio if isinstance(audio, (bytes, bytearray)) else str(audio)
    request = {"kind": "transcribe", "model": str(model)[:64],
               "language": str(language)[:16],
               "audio_bytes": len(raw) if isinstance(raw, (bytes, bytearray))
               else 0,
               "audio_ref": raw if isinstance(raw, str) else ""}
    try:
        response = transport(request)
    except Exception as exc:
        return {"state": "FAILED", "code": MEDIA_UNAVAILABLE_CODE,
                "schema": TRANSCRIBE_SCHEMA,
                "detail": _redact(str(exc))[:200], "owner": "qubit-policy",
                "rollback": "nothing recorded; nothing to roll back"}
    if not isinstance(response, dict):
        return {"state": "FAILED", "code": MEDIA_UNAVAILABLE_CODE,
                "schema": TRANSCRIBE_SCHEMA,
                "detail": "ASR transport returned a non-mapping",
                "owner": "qubit-policy",
                "rollback": "nothing recorded; nothing to roll back"}
    text = _redact(str(response.get("text", "")))[:8000]
    _audit("transcribe", requested_by, {"bytes": len(text)})
    return {"state": "OK", "schema": TRANSCRIBE_SCHEMA, "text": text,
            "language": str(response.get("language", language))[:16],
            "segments": response.get("segments", [])[:200],
            "authority": "injected ASR transport; no fabricated transcript"}


def meeting_bot(cfg: Any, *, transport: Any = None, join_url: str = "",
                display_name: str = "Qubit",
                requested_by: str = "cli:user") -> Dict[str, Any]:
    """Meeting-bot / voice-call adapter contract (OC-67).

    The adapter requires an injected telephony transport; without one it is
    a typed ``VISION_MEDIA_UNAVAILABLE``. It never joins a call or opens a
    socket on its own, and never stores raw audio.
    """
    if estop_engaged(cfg):
        return refuse_estop("meeting_bot")
    if transport is None:
        return {"state": "UNAVAILABLE", "code": MEDIA_UNAVAILABLE_CODE,
                "schema": MEETING_SCHEMA,
                "detail": "no telephony/meeting transport injected; Qubit "
                          "runs no meeting bot without one",
                "owner": "qubit-policy",
                "rollback": "no call joined; nothing to roll back"}
    if not str(join_url or "").strip():
        return {"state": "FAILED", "code": "VISION_MEETING_BAD_URL",
                "schema": MEETING_SCHEMA,
                "detail": "a meeting join URL is required",
                "owner": "qubit-policy",
                "rollback": "no call joined; nothing to roll back"}
    try:
        response = transport({"kind": "meeting.join",
                              "join_url": str(join_url)[:1024],
                              "display_name": str(display_name)[:64]})
    except Exception as exc:
        return {"state": "FAILED", "code": MEDIA_UNAVAILABLE_CODE,
                "schema": MEETING_SCHEMA,
                "detail": _redact(str(exc))[:200], "owner": "qubit-policy",
                "rollback": "no call joined; nothing to roll back"}
    if not isinstance(response, dict) or response.get("joined") is not True:
        return {"state": "FAILED", "code": MEDIA_UNAVAILABLE_CODE,
                "schema": MEETING_SCHEMA, "detail": "transport did not join",
                "owner": "qubit-policy",
                "rollback": "no call joined; nothing to roll back"}
    return {"state": "OK", "schema": MEETING_SCHEMA,
            "session": _redact(str(response.get("session", "")))[:120],
            "live_transcription": bool(response.get("transcription", False)),
            "authority": "injected meeting transport; no raw audio retained"}
