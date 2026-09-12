"""Qubit media generation: image / music / video / speech adapters (typed).

OpenClaw ships provider-backed media generation under ``src/image-generation``,
``src/music-generation``, ``src/video-generation`` and the shared
``src/media-generation`` registry. Qubit implements the code-side contract the
same way it implements messaging transports: the registry, request shaping and
the credential gate are real; the live network is the last mile and stays
credential-gated. With no provider credential resolved, every call returns a
typed ``MEDIA_GEN_UNAVAILABLE`` (never a fabricated artifact, never a fake
``state: OK``).

Honest boundaries:

* :data:`KINDS` covers ``image``, ``music``, ``video`` and ``speech``.
  ``speech`` deliberately overlaps an existing Qubit capability: local TTS
  already lives in :mod:`voicebridge.tts` (piper / kokoro / edge / openai).
  This module's ``speech`` rows are provider-backed *only*; the ``local``
  provider row points back at that path and reports ``UNAVAILABLE`` here.
* :data:`PROVIDER_CAPS` is the capability matrix. A per-kind ``available`` of
  ``False`` means Qubit has no transport adapter for that pair: it is reported
  honestly and refused with a typed code even if a credential is present. The
  only shipped live shaper/transport is OpenAI images; everything else is a
  named, typed gap until a real adapter exists.
* Credentials are always references (``env:NAME`` / ``file:PATH``) resolved
  through :mod:`voicebridge.providers.credentials`. Values never enter receipts,
  logs or errors; :func:`generate` returns the provider's own asset reference
  and never synthesizes bytes.

Public API:

* :func:`status` — provider/kind readiness derived from credential references.
* :func:`catalog` — flat, honest ``{kind, provider, state, reason}`` rows.
* :func:`generate` — validated, e-stop aware, confirmation-gated request that
  returns a typed success only when a real request shaper and transport exist.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.request
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "qubit-media-gen/1"
STATUS_SCHEMA = "qubit-media-gen-status/1"
CATALOG_SCHEMA = "qubit-media-gen-catalog/1"

MAX_PROMPT_CHARS = 4000
MAX_OPTION_TEXT = 64
MAX_OPTIONS_BYTES = 2000
MAX_OPTIONS_KEYS = 12
MAX_SOURCE_CHARS = 40
MAX_CONFIRMATION_ID_CHARS = 128
TRANSPORT_TIMEOUT_S = 60.0

# Typed, wire-stable refusal codes (mirrors messaging.py / vision.py style).
CONFIRM_CODE = "MEDIA_CONFIRM_REQUIRED"
ESTOP_CODE = "MEDIA_ESTOP"
KIND_UNKNOWN_CODE = "MEDIA_KIND_UNKNOWN"
PROVIDER_UNKNOWN_CODE = "MEDIA_PROVIDER_UNKNOWN"
UNAVAILABLE_CODE = "MEDIA_GEN_UNAVAILABLE"
BAD_INPUT_CODE = "MEDIA_BAD_INPUT"
UPSTREAM_CODE = "MEDIA_UPSTREAM"

# Kinds are the OpenClaw media-generation surface plus the overlapping speech.
KINDS = ("image", "music", "video", "speech")

KIND_META: Dict[str, Dict[str, Any]] = {
    "image": {"label": "Image", "costly": False, "async": True, "overlap": ""},
    "music": {"label": "Music", "costly": True, "async": True, "overlap": ""},
    "video": {"label": "Video", "costly": True, "async": True, "overlap": ""},
    "speech": {
        "label": "Speech", "costly": False, "async": False,
        "overlap": "Local speech synthesis already exists in voicebridge/tts "
                   "(piper/kokoro/edge/openai); the speech rows here are the "
                   "optional provider-backed network path only, not a "
                   "replacement for the local TTS pipeline.",
    },
}

# Per-provider capability matrix. ``credential_env`` names the canonical env
# reference (a REFERENCE, never a value). ``kinds`` maps kind -> cap:
#   transport   — Qubit-side path name ("openai-images" is real; "none" is a gap)
#   available   — True only when a real shaper+transport exists in this module
#   detail      — the honest reason a pair is a typed gap
#   overlap     — True when an existing Qubit path already covers the kind
PROVIDER_CAPS: Dict[str, Dict[str, Any]] = {
    "openai": {
        "label": "OpenAI",
        "credential_kind": "bearer-token",
        "credential_env": "OPENAI_API_KEY",
        "kinds": {
            "image": {
                "transport": "openai-images",
                "available": True,
                "detail": "POST https://api.openai.com/v1/images/generations "
                          "(gpt-image-1); provider asset reference returned",
            },
            "speech": {
                "transport": "none",
                "available": False,
                "overlap": True,
                "detail": "OpenAI speech already exists via "
                          "voicebridge/tts/openai_tts.py; not routed here",
            },
        },
    },
    "fal": {
        "label": "fal",
        "credential_kind": "api-key",
        "credential_env": "FAL_KEY",
        "kinds": {
            "image": {"transport": "none", "available": False,
                      "detail": "no Qubit transport adapter for fal image "
                                "generation yet"},
            "music": {"transport": "none", "available": False,
                      "detail": "no Qubit transport adapter for fal music "
                                "generation yet"},
            "video": {"transport": "none", "available": False,
                      "detail": "no Qubit transport adapter for fal video "
                                "generation yet"},
        },
    },
    "google": {
        "label": "Google",
        "credential_kind": "api-key",
        "credential_env": "GOOGLE_API_KEY",
        "kinds": {
            "image": {"transport": "none", "available": False,
                      "detail": "no Qubit transport adapter for Google/Imagen"},
            "music": {"transport": "none", "available": False,
                      "detail": "no Qubit transport adapter for Google music"},
            "video": {"transport": "none", "available": False,
                      "detail": "no Qubit transport adapter for Google/Veo"},
        },
    },
    "minimax": {
        "label": "MiniMax",
        "credential_kind": "api-key",
        "credential_env": "MINIMAX_API_KEY",
        "kinds": {
            "image": {"transport": "none", "available": False,
                      "detail": "no Qubit transport adapter for MiniMax image"},
            "music": {"transport": "none", "available": False,
                      "detail": "no Qubit transport adapter for MiniMax music"},
            "video": {"transport": "none", "available": False,
                      "detail": "no Qubit transport adapter for MiniMax video"},
        },
    },
    "elevenlabs": {
        "label": "ElevenLabs",
        "credential_kind": "api-key",
        "credential_env": "ELEVENLABS_API_KEY",
        "kinds": {
            "music": {"transport": "none", "available": False,
                      "detail": "no Qubit transport adapter for ElevenLabs "
                                "music"},
            "speech": {"transport": "none", "available": False,
                       "detail": "no Qubit transport adapter for ElevenLabs "
                                 "speech"},
        },
    },
    "runway": {
        "label": "Runway",
        "credential_kind": "api-key",
        "credential_env": "RUNWAY_API_KEY",
        "kinds": {
            "video": {"transport": "none", "available": False,
                      "detail": "no Qubit transport adapter for Runway video"},
        },
    },
    "together": {
        "label": "Together",
        "credential_kind": "api-key",
        "credential_env": "TOGETHER_API_KEY",
        "kinds": {
            "video": {"transport": "none", "available": False,
                      "detail": "no Qubit transport adapter for Together video"},
        },
    },
    "xai": {
        "label": "xAI",
        "credential_kind": "api-key",
        "credential_env": "XAI_API_KEY",
        "kinds": {
            "image": {"transport": "none", "available": False,
                      "detail": "no Qubit transport adapter for xAI image"},
            "video": {"transport": "none", "available": False,
                      "detail": "no Qubit transport adapter for xAI video"},
            "speech": {"transport": "none", "available": False,
                       "detail": "no Qubit transport adapter for xAI speech"},
        },
    },
    "openrouter": {
        "label": "OpenRouter",
        "credential_kind": "api-key",
        "credential_env": "OPENROUTER_API_KEY",
        "kinds": {
            "image": {"transport": "none", "available": False,
                      "detail": "no Qubit transport adapter for OpenRouter "
                                "image"},
            "music": {"transport": "none", "available": False,
                      "detail": "no Qubit transport adapter for OpenRouter "
                                "music"},
            "video": {"transport": "none", "available": False,
                      "detail": "no Qubit transport adapter for OpenRouter "
                                "video"},
            "speech": {"transport": "none", "available": False,
                       "detail": "no Qubit transport adapter for OpenRouter "
                                 "speech"},
        },
    },
    # Explicit overlap row: the existing Qubit-local TTS pipeline. It is a
    # recognized provider id so callers get an honest typed answer instead of
    # provider_unknown, but it is never routed through this adapter.
    "local": {
        "label": "Local (existing TTS)",
        "credential_kind": "none",
        "credential_env": "",
        "kinds": {
            "speech": {
                "transport": "voicebridge-tts",
                "available": False,
                "overlap": True,
                "detail": "local speech already exists at voicebridge/tts "
                          "(piper/kokoro/edge/openai); use that path",
            },
        },
    },
}

# Single source of truth for provider membership (order preserved).
PROVIDERS = tuple(PROVIDER_CAPS)
PROVIDER_KINDS: Dict[str, Tuple[str, ...]] = {
    pid: tuple(caps["kinds"]) for pid, caps in PROVIDER_CAPS.items()}

# Request shapers are real but deliberately minimal: only pairs with
# ``available: True`` may reach the transport. Adding a provider means adding a
# shaper here plus its fixed endpoint in :data:`ENDPOINTS`.
ENDPOINTS = {
    "openai-images": "https://api.openai.com/v1/images/generations",
}
_ALLOWED_HOSTS = frozenset({"api.openai.com"})

_OPTION_KEYS = {
    "image": frozenset({"size", "n", "model"}),
    "music": frozenset({"duration_s", "model", "format"}),
    "video": frozenset({"duration_s", "resolution", "model"}),
    "speech": frozenset({"voice", "format", "model"}),
}

_SIZE_RE = re.compile(r"^(\d{2,5})x(\d{2,5})$")


class MediaGenError(Exception):
    """Typed media-generation failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def _now() -> float:
    return time.time()


def _get(cfg: Any, *path: str, default: Any = None) -> Any:
    getter = getattr(cfg, "get", None)
    if callable(getter):
        try:
            return getter(*path, default=default)
        except TypeError:
            pass
    node: Any = cfg
    for p in path:
        if isinstance(node, dict) and p in node:
            node = node[p]
        else:
            return default
    return node


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"media_gen.{op}", actor=actor, **detail)
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
                           "is latched; reset it before media generation.",
            "owner": "qubit-policy",
            "rollback": "nothing generated; no provider request attempted"}


# ---------------------------------------------------------------- references
def _ref_kind(ref: str) -> str:
    if ref.startswith("env:"):
        return "env"
    if ref.startswith("file:"):
        return "file"
    return "missing"


def reference_for(cfg: Any, provider: str) -> str:
    """Credential reference for a provider (env:/file:), never a value.

    Explicit ``media_gen.providers.<provider>.key_ref`` wins; otherwise the
    provider's canonical env reference is used. The returned reference is safe
    to inspect but must not be logged with its resolved value.
    """
    caps = PROVIDER_CAPS.get(provider, {})
    ref = str(_get(cfg, "media_gen", "providers", provider, "key_ref",
                   default="") or "").strip()
    if ref:
        return ref
    env = str(caps.get("credential_env") or "").strip()
    return f"env:{env}" if env else ""


def _provider_configured(cfg: Any, provider: str) -> Tuple[bool, str]:
    """(configured, reference kind). Resolves the reference; value discarded."""
    ref = reference_for(cfg, provider)
    kind = _ref_kind(ref)
    if not ref or kind == "missing":
        return False, "missing"
    from .providers.credentials import resolve_key

    key, code = resolve_key(provider, ref)
    return (bool(key) and not code), kind


def _kind_cap(provider: str, kind: str) -> Optional[Dict[str, Any]]:
    return PROVIDER_CAPS.get(provider, {}).get("kinds", {}).get(kind)


def _is_costly(kind: str) -> bool:
    return bool(KIND_META.get(kind, {}).get("costly", False))


def _unavailable_reason(provider: str, kind: str, configured: bool,
                        cap: Dict[str, Any]) -> str:
    if cap.get("overlap"):
        return str(cap.get("detail") or "covered by an existing Qubit path")
    if not cap.get("available", False):
        return str(cap.get("detail") or
                   f"{provider} has no Qubit transport adapter for {kind}")
    if not configured:
        return ("credential reference not configured; set "
                "media_gen.providers.%s.key_ref (env:/file:) or the provider "
                "env reference" % provider)
    return "unavailable"


# ---------------------------------------------------------------- status/catalog
def _pair_state(cfg: Any, provider: str, kind: str
                ) -> Tuple[bool, Dict[str, Any], bool, str]:
    cap = _kind_cap(provider, kind) or {}
    configured, ref_kind = _provider_configured(cfg, provider)
    ready = bool(configured and cap.get("available", False))
    return ready, cap, configured, ref_kind


def status(cfg: Any) -> Dict[str, Any]:
    """Typed readiness for every provider/kind pair (no credential values).

    ``configured`` is derived exclusively from credential REFERENCES: an
    ``env:``/``file:`` reference that resolves at call time. Values are read
    only to test presence and are discarded, never returned or logged.
    """
    providers: List[Dict[str, Any]] = []
    configured_pairs: List[str] = []
    unavailable_pairs: List[str] = []
    for pid in PROVIDERS:
        caps = PROVIDER_CAPS[pid]
        provider_configured, ref_kind = _provider_configured(cfg, pid)
        kinds_out: Dict[str, Any] = {}
        any_ready = False
        for kind in caps["kinds"]:
            ready, cap, configured, _ = _pair_state(cfg, pid, kind)
            pair = f"{pid}:{kind}"
            if ready:
                any_ready = True
                configured_pairs.append(pair)
            else:
                unavailable_pairs.append(pair)
            kinds_out[kind] = {
                "transport": cap.get("transport", "none"),
                "adapter": bool(cap.get("available", False)),
                "configured": configured,
                "overlap": bool(cap.get("overlap", False)),
                "state": "AVAILABLE" if ready else "UNAVAILABLE",
                "code": "" if ready else UNAVAILABLE_CODE,
                "reason": "" if ready else _unavailable_reason(
                    pid, kind, configured, cap),
            }
        providers.append({
            "provider": pid,
            "label": caps.get("label", pid),
            "credential_kind": caps.get("credential_kind", "unknown"),
            "credential_ref_kind": ref_kind,
            "configured": provider_configured,
            "state": "AVAILABLE" if any_ready else "UNAVAILABLE",
            "code": "" if any_ready else UNAVAILABLE_CODE,
            "kinds": kinds_out,
        })
    kinds_out = {}
    for kind in KINDS:
        rows = [(p["provider"], p["kinds"][kind])
                for p in providers if kind in p["kinds"]]
        kinds_out[kind] = {
            "label": KIND_META[kind]["label"],
            "costly": _is_costly(kind),
            "async": bool(KIND_META[kind].get("async", True)),
            "overlap": KIND_META[kind].get("overlap", ""),
            "available": [pid for pid, r in rows if r["state"] == "AVAILABLE"],
            "unavailable": [pid for pid, r in rows
                            if r["state"] == "UNAVAILABLE"],
        }
    return {
        "state": "OK",
        "schema": STATUS_SCHEMA,
        "estop": estop_engaged(cfg),
        "providers": providers,
        "kinds": kinds_out,
        "configured": configured_pairs,
        "unavailable": unavailable_pairs,
        "speech_overlap": KIND_META["speech"]["overlap"],
    }


def catalog(cfg: Any) -> Dict[str, Any]:
    """Flat honest rows: ``{kind, provider, state, reason}`` per pair.

    A row is ``AVAILABLE`` only when a credential reference resolves *and* a
    real Qubit transport adapter exists. Unconfigured, adapter-less and
    overlapping rows are ``UNAVAILABLE`` with a typed ``MEDIA_GEN_UNAVAILABLE``
    code and a human reason.
    """
    rows: List[Dict[str, Any]] = []
    for pid in PROVIDERS:
        caps = PROVIDER_CAPS[pid]
        for kind in caps["kinds"]:
            ready, cap, configured, ref_kind = _pair_state(cfg, pid, kind)
            rows.append({
                "kind": kind,
                "provider": pid,
                "label": caps.get("label", pid),
                "state": "AVAILABLE" if ready else "UNAVAILABLE",
                "code": "" if ready else UNAVAILABLE_CODE,
                "reason": "" if ready else _unavailable_reason(
                    pid, kind, configured, cap),
                "transport": cap.get("transport", "none"),
                "adapter": bool(cap.get("available", False)),
                "configured": configured,
                "credential_ref_kind": ref_kind,
                "costly": _is_costly(kind),
                "overlap": bool(cap.get("overlap", False)),
            })
    return {
        "state": "OK",
        "schema": CATALOG_SCHEMA,
        "rows": rows,
        "available": [f"{r['provider']}:{r['kind']}" for r in rows
                      if r["state"] == "AVAILABLE"],
        "unavailable": [f"{r['provider']}:{r['kind']}" for r in rows
                        if r["state"] == "UNAVAILABLE"],
    }


# ---------------------------------------------------------------- validation
def _check_int(value: Any, low: int, high: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MediaGenError(BAD_INPUT_CODE, f"option {name!r} must be an integer")
    if value < low or value > high:
        raise MediaGenError(BAD_INPUT_CODE,
                            f"option {name!r} must be in [{low}, {high}]")
    return value


def _check_num(value: Any, low: float, high: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MediaGenError(BAD_INPUT_CODE, f"option {name!r} must be a number")
    val = float(value)
    if val < low or val > high:
        raise MediaGenError(BAD_INPUT_CODE,
                            f"option {name!r} must be in [{low}, {high}]")
    return val


def _check_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise MediaGenError(BAD_INPUT_CODE, f"option {name!r} must be text")
    text = value.strip()
    if not text or len(text) > MAX_OPTION_TEXT:
        raise MediaGenError(
            BAD_INPUT_CODE,
            f"option {name!r} must be 1..{MAX_OPTION_TEXT} chars")
    if any(ord(ch) < 32 for ch in text):
        raise MediaGenError(BAD_INPUT_CODE,
                            f"option {name!r} must not contain control chars")
    return text


def _check_size(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise MediaGenError(BAD_INPUT_CODE, f"option {name!r} must be text")
    match = _SIZE_RE.match(value.strip())
    if not match:
        raise MediaGenError(
            BAD_INPUT_CODE,
            f"option {name!r} must look like WIDTHxHEIGHT (e.g. 1024x1024)")
    width, height = int(match.group(1)), int(match.group(2))
    if not (16 <= width <= 8192 and 16 <= height <= 8192):
        raise MediaGenError(BAD_INPUT_CODE,
                            f"option {name!r} dimensions must be 16..8192")
    return f"{width}x{height}"


def validate_options(kind: str, options: Any) -> Dict[str, Any]:
    """Validate and normalize per-kind options (bounded, strict keys)."""
    if options is None:
        return {}
    if not isinstance(options, dict):
        raise MediaGenError(BAD_INPUT_CODE, "options must be a mapping")
    if len(options) > MAX_OPTIONS_KEYS:
        raise MediaGenError(BAD_INPUT_CODE,
                            f"too many options (max {MAX_OPTIONS_KEYS})")
    allowed = _OPTION_KEYS.get(kind, frozenset())
    unknown = sorted(set(options) - allowed)
    if unknown:
        raise MediaGenError(BAD_INPUT_CODE,
                            f"unknown option(s) for {kind}: {unknown}")
    clean: Dict[str, Any] = {}
    for key, value in options.items():
        if key == "n":
            clean[key] = _check_int(value, 1, 4, key)
        elif key == "duration_s":
            high = 120.0 if kind == "video" else 600.0
            clean[key] = _check_num(value, 1.0, high, key)
        elif key in ("size", "resolution"):
            clean[key] = _check_size(value, key)
        else:
            clean[key] = _check_text(value, key)
    if len(json.dumps(clean, sort_keys=True)) > MAX_OPTIONS_BYTES:
        raise MediaGenError(BAD_INPUT_CODE, "options payload too large")
    return clean


# ---------------------------------------------------------------- shaping
def _shape_openai_image(prompt: str, options: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "method": "POST",
        "endpoint": "openai-images",
        "url": ENDPOINTS["openai-images"],
        "body": {
            "model": options.get("model", "gpt-image-1"),
            "prompt": prompt,
            "n": options.get("n", 1),
            "size": options.get("size", "1024x1024"),
        },
    }


SHAPERS = {
    ("openai", "image"): _shape_openai_image,
}


def build_request(provider: str, kind: str, prompt: str,
                  options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Pure provider request shaping (no I/O). Typed gap when unsupported."""
    shaper = SHAPERS.get((provider, kind))
    if shaper is None:
        raise MediaGenError(
            UNAVAILABLE_CODE,
            f"no Qubit request shaper for {provider}/{kind}; nothing built")
    return shaper((prompt or "").strip(), dict(options or {}))


def shape_media_request(provider: str, kind: str, prompt: str,
                        options: Optional[Dict[str, Any]] = None
                        ) -> Dict[str, Any]:
    """Shape a bounded request for any supported kind (OC-64/OC-D18).

    Uses the shipped shaper when one exists; otherwise returns a normalized,
    provider-neutral request for an operator-injected transport. This is the
    adapter contract for the kinds without a shipped live adapter
    (music/video and non-OpenAI providers): no network is touched and no
    endpoint is invented. The injected transport owns the vendor call.
    """
    shaper = SHAPERS.get((provider, kind))
    if shaper is not None:
        return shaper((prompt or "").strip(), dict(options or {}))
    if kind not in KINDS:
        raise MediaGenError(KIND_UNKNOWN_CODE, f"kind must be one of {KINDS}")
    clean = dict(options or {})
    body: Dict[str, Any] = {"provider": provider, "kind": kind,
                            "prompt": (prompt or "").strip()}
    for key in _OPTION_KEYS.get(kind, ()):  # only bounded, validated keys
        if key in clean:
            body[key] = clean[key]
    return {"method": "POST", "endpoint": f"{provider}:{kind}", "url": "",
            "body": body}


def _mime_for(kind: str) -> str:
    return {"image": "image/png", "music": "audio/mpeg", "video": "video/mp4",
            "speech": "audio/mpeg"}.get(kind, "application/octet-stream")


def _extract_asset(kind: str, data: Any) -> Dict[str, Any]:
    """Provider's own asset reference; raw bytes are hashed, never stored."""
    if not isinstance(data, dict):
        raise MediaGenError(UPSTREAM_CODE, "provider response was not JSON")
    candidates = data.get("data")
    first: Dict[str, Any] = {}
    if isinstance(candidates, list) and candidates:
        if not isinstance(candidates[0], dict):
            raise MediaGenError(UPSTREAM_CODE, "provider asset shape unknown")
        first = candidates[0]
    else:
        first = data
    url = str(first.get("url") or first.get("output_url") or "")
    b64 = first.get("b64_json") or first.get("b64") or ""
    if not url and not b64:
        raise MediaGenError(UPSTREAM_CODE,
                            "provider returned no asset reference")
    asset: Dict[str, Any] = {
        "provider_asset_id": str(first.get("id") or ""),
        "url": url,
        "mime": _mime_for(kind),
    }
    if b64:
        asset["encoding"] = "base64"
        asset["b64_sha256"] = hashlib.sha256(str(b64).encode()).hexdigest()
    return asset


def _transport(request: Dict[str, Any], key: str,
               transport: Any = None) -> Dict[str, Any]:
    """Bounded HTTPS POST; key travels in the Authorization header only.

    When ``transport`` is supplied it is the operator-injected boundary: the
    shaped request is handed to ``transport(request)`` and Qubit opens no
    socket. The secret is never part of the request mapping.
    """
    if transport is not None:
        try:
            data = transport(request)
        except MediaGenError:
            raise
        except Exception as exc:
            detail = str(exc)
            if key:
                detail = detail.replace(key, "<redacted>")
            raise MediaGenError(
                UPSTREAM_CODE,
                f"provider request failed: {detail}") from exc
        return data
    url = str(request.get("url") or "")
    host = urlparse(url).hostname or ""
    if host not in _ALLOWED_HOSTS:
        raise MediaGenError(UNAVAILABLE_CODE,
                            "provider endpoint is not allowlisted")
    payload = json.dumps(request.get("body", {})).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=TRANSPORT_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = getattr(resp, "status", 200)
    except MediaGenError:
        raise
    except Exception as exc:
        raise MediaGenError(UPSTREAM_CODE,
                            f"provider request failed: {exc}") from exc
    if status != 200:
        raise MediaGenError(UPSTREAM_CODE, f"provider HTTP {status}")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        raise MediaGenError(UPSTREAM_CODE, "provider bad JSON") from None
    return data


# ---------------------------------------------------------------- generate
def _default_provider(cfg: Any, kind: str) -> str:
    for pid in PROVIDERS:
        cap = _kind_cap(pid, kind)
        if not cap or not cap.get("available", False):
            continue
        configured, _ = _provider_configured(cfg, pid)
        if configured:
            return pid
    return ""


def generate(cfg: Any, *, kind: str, provider: Optional[str] = None,
             prompt: str, options: Optional[Dict[str, Any]] = None,
             confirmation_id: str = "", confirmed: bool = False,
             source: str = "user",
             transport: Any = None) -> Dict[str, Any]:
    """Validate, gate and (only with a real transport) execute generation.

    Gate order mirrors :func:`voicebridge.messaging.send`: emergency stop →
    kind/provider known → prompt/option bounds → confirmation for costly kinds
    → credential reference resolvable → transport adapter present → bounded
    provider request. Any missing link raises a typed :class:`MediaGenError`;
    an unconfigured provider is never reported as a fake success and no
    artifact is ever fabricated or written.
    """
    if estop_engaged(cfg):
        raise MediaGenError(ESTOP_CODE, "emergency stop is latched")

    clean_kind = (kind or "").strip().lower()
    if clean_kind not in KINDS:
        raise MediaGenError(KIND_UNKNOWN_CODE,
                            f"kind must be one of {KINDS}")

    pid = (provider or "").strip().lower()
    if pid:
        if pid not in PROVIDERS:
            raise MediaGenError(PROVIDER_UNKNOWN_CODE,
                                f"provider must be one of {PROVIDERS}")
    else:
        pid = _default_provider(cfg, clean_kind)
        if not pid:
            raise MediaGenError(
                UNAVAILABLE_CODE,
                f"no configured provider with a transport adapter for "
                f"{clean_kind}; configure a credential reference first")

    cap = _kind_cap(pid, clean_kind)
    if cap is None:
        raise MediaGenError(
            UNAVAILABLE_CODE,
            f"{PROVIDER_CAPS[pid].get('label', pid)} does not support "
            f"{clean_kind}; nothing was generated")

    clean_prompt = (prompt or "").strip()
    if not clean_prompt or len(clean_prompt) > MAX_PROMPT_CHARS:
        raise MediaGenError(
            BAD_INPUT_CODE, f"prompt must be 1..{MAX_PROMPT_CHARS} chars")
    clean_options = validate_options(clean_kind, options)

    if _is_costly(clean_kind) and not confirmed:
        raise MediaGenError(
            CONFIRM_CODE,
            f"{clean_kind} generation is costly and requires explicit "
            f"confirmation; nothing was generated")

    configured, _ = _provider_configured(cfg, pid)
    if not configured:
        raise MediaGenError(
            UNAVAILABLE_CODE,
            f"{pid} credential reference is not configured; set "
            f"media_gen.providers.{pid}.key_ref (env:/file:) or the provider "
            f"env reference; nothing attempted")
    if not cap.get("available", False) and transport is None:
        raise MediaGenError(
            UNAVAILABLE_CODE,
            f"{PROVIDER_CAPS[pid].get('label', pid)} has no Qubit transport "
            f"adapter ({cap.get('detail', 'transport unavailable')}); nothing "
            f"was generated")

    ref = reference_for(cfg, pid)
    from .providers.credentials import resolve_key

    key, code = resolve_key(pid, ref)
    if code or not key:
        raise MediaGenError(
            UNAVAILABLE_CODE,
            "credential reference unresolvable at call time; nothing attempted")

    request = (shape_media_request(pid, clean_kind, clean_prompt,
                                   clean_options)
               if transport is not None
               else build_request(pid, clean_kind, clean_prompt, clean_options))
    data = _transport(request, key, transport)
    asset = _extract_asset(clean_kind, data)

    clean_source = (source or "user").strip()[:MAX_SOURCE_CHARS] or "user"
    clean_conf = (confirmation_id or "").strip()[:MAX_CONFIRMATION_ID_CHARS]
    result: Dict[str, Any] = {
        "state": "OK",
        "schema": SCHEMA,
        "kind": clean_kind,
        "provider": pid,
        "transport": cap.get("transport", "none"),
        "prompt_preview": clean_prompt[:160],
        "prompt_sha256": hashlib.sha256(clean_prompt.encode()).hexdigest(),
        "options": clean_options,
        "asset": asset,
        "confirmation_id": clean_conf,
        "confirmed": bool(confirmed),
        "costly": _is_costly(clean_kind),
        "source": clean_source,
        "created_at": _now(),
    }
    _audit("generate", clean_source,
           {"kind": clean_kind, "provider": pid,
            "prompt_sha256": result["prompt_sha256"],
            "asset_url": _redact(asset.get("url", ""))[:160],
            "confirmation_id": _redact(clean_conf)[:80]})
    from .redaction import redact_obj

    return redact_obj(result)
