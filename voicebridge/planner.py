"""Bounded typed L2 planner (Packet 4D).

The planner proposes; only the capability broker decides and executes.
Smallest matching tool schemas: a curated shortlist of whitelisted local
actions with minimal typed arguments. Unknown tools, undeclared arguments,
malformed output, or low confidence fail closed to a typed no-decision.

The proposal object carries DATA ONLY — it has no execution path. Broker
bypass is structurally impossible from this module.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

log = logging.getLogger("voicebridge.planner")

PLANNER_SCHEMA_VERSION = "planner/1"

# Smallest matching tool schemas: ONLY whitelisted local.* actions, each with
# the minimum arguments the underlying action fn actually reads.
PLANNER_TOOLS: Dict[str, Dict[str, Any]] = {
    "local.volume_up": {"args": {}, "summary": "raise speaker volume"},
    "local.volume_down": {"args": {}, "summary": "lower speaker volume"},
    "local.mic_mute": {"args": {}, "summary": "toggle microphone mute"},
    "local.screenshot": {"args": {}, "summary": "take a screenshot"},
    "local.open_app": {
        "args": {"app": {"type": "string"}},
        "required": ["app"],
        "summary": "launch an application by name",
    },
    "local.play_youtube": {
        "args": {"query": {"type": "string"}},
        "required": ["query"],
        "summary": "play a YouTube song/video search",
    },
    "local.youtube_pause": {
        "args": {},
        "summary": "pause YouTube playback",
    },
    "web.fetch": {
        "args": {"url": {"type": "string"}},
        "required": ["url"],
        "summary": "fetch a public web page (read-only, SSRF-guarded)",
    },
    "web.search": {
        "args": {"query": {"type": "string"}},
        "required": ["query"],
        "summary": "search the web via the configured provider",
    },
}

# File-build tools for headless build tasks ONLY. These are MUTATING
# broker capabilities (confirm-gated, path-confined, reversible) — never
# part of the reversible-only shortlist above. The narrowest reusable op
# is ``local.files_upload`` (:func:`voicebridge.file_patch.write_bytes`
# behind ``broker.register_file_patch``): one whole-file write inside an
# approved project root, bounded base64 payload, durable backup. The
# backend (orchestrator ``_task_tool_backend``) accepts exactly these
# names; keep both sides in sync.
PLANNER_BUILD_TOOLS: Dict[str, Dict[str, Any]] = {
    "local.files_upload": {
        "args": {
            "target_path": {"type": "string"},
            "content_base64": {"type": "string"},
        },
        "required": ["target_path", "content_base64"],
        "summary": "create one file with the given bytes (base64) inside "
                   "the approved project folder (confirm-gated, reversible)",
        "requires_confirmation": True,
    },
}

# A build task is a page-build request. The gate runs on the REQUEST text
# (the headless frame appended by the orchestrator mentions files in its
# instructions, so the frame tail is stripped first — otherwise every
# framed turn would qualify).
_BUILD_TASK_RE = re.compile(
    r"\b(webs?ite|web\s?pages?|web\s?apps?|landing\s?pages?|"
    r"one[- ]pagers?|\.html?)\b",
    re.IGNORECASE)
_FRAME_MARKER = "\n\nHeadless build instructions:"

# Decoded file-payload ceiling for a planner proposal (256 KiB — enough
# for a one-page site; the broker enforces its own larger cap).
MAX_BUILD_BYTES = 256 * 1024


def _request_text(text: str) -> str:
    """The user request without the headless build-frame tail."""
    return str(text or "").split(_FRAME_MARKER)[0]


def is_build_task(text: str) -> bool:
    """True when the request asks for a page-build (file tools allowed)."""
    return bool(_BUILD_TASK_RE.search(_request_text(text)))

MIN_CONFIDENCE = 0.60

# "none" MUST be a valid enum choice: without it, structured outputs force
# a real tool on every request and honest abstention becomes impossible
# (measured Packet 4D-alignment: this alone caused 2-3 out-of-scope
# acceptances per candidate on trivially-unrelated requests).
PLANNER_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": "string",
                 "enum": sorted(PLANNER_TOOLS) + ["none"]},
        "arguments": {"type": "object"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["tool", "confidence"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class TypedProposal:
    """Data-only planner output. No execute(), no broker access."""
    tool: str
    version: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    requires_confirmation: bool = False


def build_messages(text: str,
                   extra: Optional[Dict[str, Dict[str, Any]]] = None) -> list:
    tools = dict(sorted(PLANNER_TOOLS.items()))
    if extra:
        tools.update(sorted(extra.items()))
    tool_lines = "\n".join(
        f'- {name}: {spec["summary"]} args={json.dumps(spec["args"])}'
        for name, spec in tools.items())
    system = (
        "You convert a user request into ONE tool proposal as compact JSON "
        "matching the provided schema (tool, arguments, confidence). Pick a "
        "tool ONLY if the request clearly maps to it; otherwise answer with "
        '{"tool": "none"}. Never invent tools or argument names. '
        "The local.files_upload tool creates a real file: put the destination "
        "filename in target_path and the complete file bytes as base64 in "
        "content_base64.")
    user = (f"Available tools:\n{tool_lines}\n\n"
            f"Request: {_request_text(text)[:400]}\n\nTool proposal:")
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def _schema_for(text: str) -> Dict[str, Any]:
    """Planner schema: build tools listed only for build tasks."""
    names = sorted(PLANNER_TOOLS)
    if is_build_task(text):
        names = sorted(set(names) | set(PLANNER_BUILD_TOOLS))
    return {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": names + ["none"]},
            "arguments": {"type": "object"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["tool", "confidence"],
        "additionalProperties": False,
    }


def _check_build_args(tool: str, args: Dict[str, Any]) -> bool:
    """Shape-check a file-build proposal (base64, bounded, non-empty)."""
    import base64 as _b64

    for k in ("target_path", "content_base64"):
        v = args.get(k)
        if not isinstance(v, str) or not v.strip():
            log.info("planner.no_decision reason=missing_required "
                     "tool=%s arg=%s", tool, k)
            return False
    try:
        raw = _b64.b64decode(args["content_base64"], validate=True)
    except Exception:
        log.info("planner.no_decision reason=bad_base64 tool=%s", tool)
        return False
    if not raw or len(raw) > MAX_BUILD_BYTES:
        log.info("planner.no_decision reason=build_size tool=%s", tool)
        return False
    return True


def _validate(tool: Any, args: Any, confidence: Any,
              text: str) -> Optional[TypedProposal]:
    if tool in (None, "none", ""):
        return None                      # honest no-tool outcome
    if tool in PLANNER_BUILD_TOOLS:
        if not is_build_task(text):
            log.info("planner.no_decision reason=build_tool_non_build "
                     "tool=%s", tool)
            return None                  # file tools need a build request
        spec = PLANNER_BUILD_TOOLS[tool]
        args = args if isinstance(args, dict) else {}
        allowed = set(spec["args"])
        extra = set(args) - allowed
        if extra:
            log.info("planner.no_decision reason=undeclared_args tool=%s",
                     tool)
            return None
        for k in spec.get("required", []):
            v = args.get(k)
            if not isinstance(v, str) or not v.strip():
                log.info("planner.no_decision reason=missing_required "
                         "tool=%s arg=%s", tool, k)
                return None
        if not _check_build_args(tool, args):
            return None
        try:
            conf = float(confidence)
        except (TypeError, ValueError):
            conf = MIN_CONFIDENCE
        if conf < MIN_CONFIDENCE:
            return None                  # low confidence never acts
        return TypedProposal(
            tool=tool, version="1.0.0",
            arguments={k: v for k, v in args.items()},
            confidence=round(min(conf, 1.0), 3),
            requires_confirmation=True)
    if tool not in PLANNER_TOOLS:
        log.info("planner.no_decision reason=unknown_tool tool=%s", tool)
        return None                      # unknown/out-of-scope: fail closed
    spec = PLANNER_TOOLS[tool]
    args = args if isinstance(args, dict) else {}
    allowed = set(spec["args"])
    extra = set(args) - allowed
    if extra:
        log.info("planner.no_decision reason=undeclared_args tool=%s", tool)
        return None
    for k in spec.get("required", []):
        v = args.get(k)
        if not isinstance(v, str) or not v.strip():
            log.info("planner.no_decision reason=missing_required "
                     "tool=%s arg=%s", tool, k)
            return None
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = MIN_CONFIDENCE
    if conf < MIN_CONFIDENCE:
        return None                      # low confidence never acts
    return TypedProposal(
        tool=tool, version="1.0.0",
        arguments={k: v for k, v in args.items()},
        confidence=round(min(conf, 1.0), 3),
        # destructive/financial tiers would require confirmation; this
        # shortlist is reversible-only, kept explicit for future growth:
        requires_confirmation=False)


def plan(text: str, *, cfg: Any = None,
         url: str = "http://127.0.0.1:11434",
         model: Optional[str] = None, timeout_s: float = 8.0,
         ) -> Optional[TypedProposal]:
    """One canonical-contract LLM call producing a validated proposal or a
    typed no-decision (None). The effective ``tool`` profile is resolved by
    ``providers.resolver`` and executed through ``providers.gateway.run``
    (local route, full JSON Schema, temp 0)."""
    from .providers import gateway as _gateway

    res = _gateway.run_role(
        text, cfg, role="tool", max_tokens=1024, timeout_s=timeout_s,
        local_mode="chat_raw", messages=build_messages(
            text, PLANNER_BUILD_TOOLS if is_build_task(text) else None),
        structured_schema=_schema_for(text), options={"temperature": 0.0})
    if not res.ok:
        log.info("planner.unavailable error=%s code=%s",
                 res.message[:120], res.code)
        return None
    try:
        parsed = json.loads(res.text)
    except json.JSONDecodeError:
        log.info("planner.no_decision reason=unparseable")
        return None
    if not isinstance(parsed, dict):
        return None
    return _validate(parsed.get("tool"), parsed.get("arguments"),
                     parsed.get("confidence"), text)
