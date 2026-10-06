"""Voice wake routing: map a spoken trigger phrase to a target.

Ports the semantics (not the code) of OpenClaw's ``voicewake-routing`` store:
a small JSON document under the canonical state dir maps normalized wake
phrases to an agent, a session key, or the current session. Every route
target is **exactly one** of ``{"mode": "current"}``, ``{"agentId": ...}`` or
``{"sessionKey": ...}``.

Like :mod:`voicebridge.orb.options`, this is a pure, testable store: all
validation/normalization is side-effect free, reads fail-closed to the
default, and writes are atomic (tmp + replace) and never raise. The only I/O
in this module is :func:`load_config` / :func:`save_config`.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional

ROUTING_FILENAME = "wake_routing.json"

#: Hard limits mirroring the upstream store (invalid entries are dropped,
#: never surfaced as errors).
MAX_ROUTES = 32
MAX_TRIGGER_LENGTH = 64

#: Sentinel used when a route/target cannot be normalized.
_MODE_CURRENT = "current"


def state_dir() -> Path:
    """Canonical Qubit state dir (never the repo); fallback never raises."""
    try:
        from voicebridge.config import canonical_state_dir
        return Path(canonical_state_dir())
    except Exception:
        return Path(os.path.expanduser("~/.local/state/qubit-voicebridge"))


def routing_path() -> Path:
    return state_dir() / ROUTING_FILENAME


@dataclass(frozen=True)
class RouteTarget:
    """A single resolved destination.

    Exactly one of ``mode``/``agent_id``/``session_key`` is set. ``mode`` is
    only ever the literal ``"current"``; malformed combinations normalize to
    the current-session target.
    """

    mode: Optional[str] = None
    agent_id: Optional[str] = None
    session_key: Optional[str] = None

    @classmethod
    def current(cls) -> "RouteTarget":
        return cls(mode=_MODE_CURRENT)

    @classmethod
    def for_agent(cls, agent_id: Any) -> "RouteTarget":
        return cls(agent_id=str(agent_id))

    @classmethod
    def for_session(cls, session_key: Any) -> "RouteTarget":
        return cls(session_key=str(session_key))

    @property
    def kind(self) -> str:
        """``"current"`` | ``"agent"`` | ``"session"`` (best-effort)."""
        if self.mode == _MODE_CURRENT:
            return "current"
        if self.agent_id:
            return "agent"
        if self.session_key:
            return "session"
        return "current"

    def to_dict(self) -> Dict[str, Any]:
        if self.mode == _MODE_CURRENT:
            return {"mode": _MODE_CURRENT}
        if self.agent_id:
            return {"agentId": self.agent_id}
        if self.session_key:
            return {"sessionKey": self.session_key}
        return {"mode": _MODE_CURRENT}


@dataclass(frozen=True)
class RouteRule:
    """A normalized trigger phrase bound to a target."""

    trigger: str
    target: RouteTarget

    def to_dict(self) -> Dict[str, Any]:
        return {"trigger": self.trigger, "target": self.target.to_dict()}


@dataclass(frozen=True)
class WakeRoutingConfig:
    """The persisted routing document (upstream shape-compatible)."""

    version: int = 1
    default_target: RouteTarget = field(default_factory=RouteTarget.current)
    routes: List[RouteRule] = field(default_factory=list)
    updated_at_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "defaultTarget": self.default_target.to_dict(),
            "routes": [r.to_dict() for r in self.routes],
            "updatedAtMs": self.updated_at_ms,
        }


# ---------------------------------------------------------------------------
# Normalization (pure, never raises)
# ---------------------------------------------------------------------------
def _strip_edge_punct(token: str) -> str:
    """Drop leading/trailing Unicode punctuation/symbols from one token.

    Mirrors upstream ``token.replace(/^[\\p{P}\\p{S}]+|[\\p{P}\\p{S}]+$/gu,
    "")`` without the ``\\p{...}`` regex extension.
    """
    start, end = 0, len(token)
    while start < end and unicodedata.category(token[start])[0] in ("P", "S"):
        start += 1
    while end > start and unicodedata.category(token[end - 1])[0] in ("P", "S"):
        end -= 1
    return token[start:end]


def normalize_trigger(value: Any) -> str:
    """Normalize a wake trigger phrase for matching and duplicate checks.

    Delegates to :func:`voicebridge.voice_edit.wake_words.normalize_trigger`
    (the single wake-word normalizer) when present, falling back to the
    identical local implementation so this module stands alone. Lowercase,
    collapse whitespace, strip edge punctuation from every token, drop
    empties, join with single spaces. Never raises; junk -> ``""``.
    """
    try:
        from .wake_words import normalize_trigger as _shared
        return _shared(value)
    except Exception:
        return _normalize_trigger_local(value)


def _normalize_trigger_local(value: Any) -> str:
    """Local fallback: identical to the shared wake-word normalizer."""
    try:
        text = value if isinstance(value, str) else str(value if value is not None else "")
    except Exception:
        return ""
    try:
        tokens: List[str] = []
        for raw in text.lower().split():
            tok = _strip_edge_punct(raw)
            if tok:
                tokens.append(tok)
        return " ".join(tokens)
    except Exception:
        return ""


def _optional_str(value: Any) -> Optional[str]:
    """Trimmed non-empty string, else ``None`` (never raises)."""
    if value is None:
        return None
    try:
        s = value if isinstance(value, str) else str(value)
    except Exception:
        return None
    s = s.strip()
    return s or None


def normalize_route_target(value: Any) -> Optional[RouteTarget]:
    """Validate a target payload to exactly one of mode/agentId/sessionKey.

    Returns ``None`` for anything malformed so the caller drops it.
    """
    if not isinstance(value, dict):
        return None
    mode = _optional_str(value.get("mode"))
    if mode == _MODE_CURRENT:
        return RouteTarget.current()
    agent_id = _optional_str(value.get("agentId"))
    session_key = _optional_str(value.get("sessionKey"))
    if agent_id and not session_key:
        return RouteTarget(agent_id=agent_id)
    if session_key and not agent_id:
        return RouteTarget(session_key=session_key)
    return None


def _normalize_route_rule(value: Any) -> Optional[RouteRule]:
    if not isinstance(value, dict):
        return None
    trigger_raw = _optional_str(value.get("trigger"))
    if not trigger_raw:
        return None
    if len(trigger_raw) > MAX_TRIGGER_LENGTH:
        return None
    trigger = normalize_trigger(trigger_raw)
    if not trigger:
        return None
    target = normalize_route_target(value.get("target"))
    if target is None:
        return None
    return RouteRule(trigger=trigger, target=target)


def _config_to_dict(cfg: Any) -> Dict[str, Any]:
    """Accept a config dataclass or a plain dict; else ``{}``. Never raises."""
    try:
        if isinstance(cfg, WakeRoutingConfig):
            return cfg.to_dict()
        if isinstance(cfg, dict):
            return cfg
    except Exception:
        pass
    return {}


def _coerce_target(target: Any) -> Optional[RouteTarget]:
    try:
        if isinstance(target, RouteTarget):
            return normalize_route_target(target.to_dict())
        return normalize_route_target(target)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Parse / persistence
# ---------------------------------------------------------------------------
def parse_config(data: Any) -> WakeRoutingConfig:
    """Build a validated config from parsed JSON.

    Invalid rules/targets are dropped, routes are capped at
    :data:`MAX_ROUTES`, and anything malformed (including a non-dict input)
    falls back to the default ``{mode: current}`` config. Never raises.
    """
    try:
        if isinstance(data, WakeRoutingConfig):
            data = data.to_dict()
        if not isinstance(data, dict):
            return WakeRoutingConfig()
        default_target = (normalize_route_target(data.get("defaultTarget"))
                          or RouteTarget.current())
        routes: List[RouteRule] = []
        raw_routes = data.get("routes")
        if isinstance(raw_routes, list):
            for entry in raw_routes:
                rule = _normalize_route_rule(entry)
                if rule is not None:
                    routes.append(rule)
                if len(routes) >= MAX_ROUTES:
                    break
        updated_at_ms = _coerce_ms(data.get("updatedAtMs"))
        return WakeRoutingConfig(
            version=1, default_target=default_target, routes=routes,
            updated_at_ms=updated_at_ms)
    except Exception:
        return WakeRoutingConfig()


def _coerce_ms(value: Any) -> int:
    """Non-negative integer milliseconds; non-finite/bool/junk -> 0."""
    try:
        if isinstance(value, bool):
            return 0
        if isinstance(value, (int, float)) and math.isfinite(float(value)) and value > 0:
            return int(value)
    except Exception:
        return 0
    return 0


def load_config(path: Any = None) -> WakeRoutingConfig:
    """Read persisted routing; missing/corrupt -> default. Never raises."""
    try:
        raw = Path(path) if path is not None else routing_path()
        return parse_config(json.loads(raw.read_text(encoding="utf-8")))
    except Exception:
        return WakeRoutingConfig()


def save_config(cfg: Any, path: Any = None) -> WakeRoutingConfig:
    """Atomically persist a normalized config; never raises.

    Returns the normalized config that was written (with a fresh
    ``updated_at_ms``); on any I/O failure the normalized value is still
    returned so callers hold the in-memory truth.
    """
    normalized = parse_config(_config_to_dict(cfg))
    stamped = replace(normalized, updated_at_ms=int(time.time() * 1000))
    try:
        target = Path(path) if path is not None else routing_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(stamped.to_dict(), indent=2, sort_keys=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent),
                                   prefix=".wake-routing-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, target)
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
    except Exception:
        pass
    return stamped


# ---------------------------------------------------------------------------
# Resolution (pure)
# ---------------------------------------------------------------------------
def _trigger_matches(utterance: str, trigger: str) -> bool:
    """True when ``trigger`` is a word-boundary substring of ``utterance``.

    Both inputs are pre-normalized (lowercase, single spaces), so padding
    with spaces gives exact token-boundary containment: ``"open"`` matches
    ``"please open youtube"`` but never ``"opened"``.
    """
    if not trigger:
        return False
    return f" {trigger} " in f" {utterance} "


def resolve_target(trigger_text: Any, cfg: Any) -> RouteTarget:
    """Resolve the target for an utterance.

    Picks the longest route whose normalized trigger appears as a
    (word-boundary) substring of the normalized utterance; ties fall to the
    first declared route. No match — or any malformed input — returns the
    config's ``defaultTarget``. Never raises.
    """
    try:
        config = cfg if isinstance(cfg, WakeRoutingConfig) else parse_config(cfg)
        utterance = normalize_trigger(trigger_text)
        if utterance:
            best: Optional[RouteRule] = None
            best_rank = (-1, -1)
            for rule in config.routes:
                trigger = rule.trigger
                if not _trigger_matches(utterance, trigger):
                    continue
                rank = (len(trigger.split()), len(trigger))
                if rank > best_rank:
                    best_rank = rank
                    best = rule
            if best is not None:
                return best.target
        return config.default_target
    except Exception:
        return RouteTarget.current()


def add_route(cfg: Any, trigger: Any, target: Any) -> WakeRoutingConfig:
    """Return a new config with ``trigger -> target`` added/replaced.

    Pure: no I/O, never raises. An invalid trigger/target or a config already
    at :data:`MAX_ROUTES` (and no existing trigger to replace) returns the
    normalized input unchanged.
    """
    base = parse_config(_config_to_dict(cfg))
    try:
        norm_trigger = normalize_trigger(trigger)
        if not norm_trigger:
            return base
        trigger_raw = _optional_str(trigger)
        if trigger_raw is None or len(trigger_raw) > MAX_TRIGGER_LENGTH:
            return base
        norm_target = _coerce_target(target)
        if norm_target is None:
            return base
        routes = list(base.routes)
        for i, rule in enumerate(routes):
            if rule.trigger == norm_trigger:
                routes[i] = RouteRule(trigger=norm_trigger, target=norm_target)
                break
        else:
            if len(routes) >= MAX_ROUTES:
                return base
            routes.append(RouteRule(trigger=norm_trigger, target=norm_target))
        return replace(base, routes=routes)
    except Exception:
        return base


def remove_route(cfg: Any, trigger: Any) -> WakeRoutingConfig:
    """Return a new config without any rule matching ``trigger``.

    Pure: no I/O, never raises. An empty/normalized-empty trigger returns the
    normalized input unchanged.
    """
    base = parse_config(_config_to_dict(cfg))
    try:
        norm_trigger = normalize_trigger(trigger)
        if not norm_trigger:
            return base
        routes = [r for r in base.routes if r.trigger != norm_trigger]
        return replace(base, routes=routes)
    except Exception:
        return base


__all__ = [
    "ROUTING_FILENAME",
    "MAX_ROUTES",
    "MAX_TRIGGER_LENGTH",
    "RouteTarget",
    "RouteRule",
    "WakeRoutingConfig",
    "state_dir",
    "routing_path",
    "normalize_trigger",
    "parse_config",
    "load_config",
    "save_config",
    "resolve_target",
    "add_route",
    "remove_route",
]
