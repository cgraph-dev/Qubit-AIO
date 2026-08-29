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
import urllib.request
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
}

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


def build_messages(text: str) -> list:
    tool_lines = "\n".join(
        f'- {name}: {spec["summary"]} args={json.dumps(spec["args"])}'
        for name, spec in sorted(PLANNER_TOOLS.items()))
    system = (
        "You convert a user request into ONE tool proposal as compact JSON "
        "matching the provided schema (tool, arguments, confidence). Pick a "
        "tool ONLY if the request clearly maps to it; otherwise answer with "
        '{"tool": "none"}. Never invent tools or argument names.')
    user = (f"Available tools:\n{tool_lines}\n\n"
            f"Request: {text[:400]}\n\nTool proposal:")
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def _validate(tool: Any, args: Any, confidence: Any,
             text: str) -> Optional[TypedProposal]:
    if tool in (None, "none", ""):
        return None                      # honest no-tool outcome
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


def plan(text: str, *, url: str = "http://127.0.0.1:11434",
         model: Optional[str] = None, timeout_s: float = 8.0,
         ) -> Optional[TypedProposal]:
    """One canonical-contract LLM call producing a validated proposal or a
    typed no-decision (None). Same runner options source as every other
    caller (Packet 4B contract); format = full JSON Schema, temp 0."""
    from .runner_contract import resolve
    _rc = resolve()
    model = model or _rc.alias
    payload = {
        "model": model,
        "messages": build_messages(text),
        "stream": False,
        "format": PLANNER_SCHEMA,
        "keep_alive": _rc.keep_alive,
        "options": {"temperature": 0.0, "num_ctx": _rc.num_ctx},
    }
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode())
    except Exception as exc:
        log.info("planner.unavailable error=%s", str(exc)[:120])
        return None
    raw = (body.get("message") or {}).get("content") or ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.info("planner.no_decision reason=unparseable")
        return None
    if not isinstance(parsed, dict):
        return None
    return _validate(parsed.get("tool"), parsed.get("arguments"),
                     parsed.get("confidence"), text)
