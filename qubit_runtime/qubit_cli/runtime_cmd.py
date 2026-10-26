"""``qubit runtime`` handler: diagnostics only, no product turn path."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

from agent.runtime_port import TurnRequest, UnknownRuntimeError


def _prompt(args) -> tuple[str | None, str | None]:
    """``(prompt, error)``; a missing file is reported, not flattened to "no prompt"."""
    if args.prompt:
        return args.prompt, None
    if args.query_file:
        try:
            text = Path(args.query_file).read_text(encoding="utf-8-sig").strip()
        except OSError as exc:
            return None, f"runtime run: cannot read {args.query_file}: {exc}"
        return (text or None), None
    return None, None


def _native_factory(request: TurnRequest) -> Any:
    from qubit_cli.config import load_config
    from qubit_cli.runtime_provider import resolve_runtime_provider
    from run_agent import AIAgent

    model_cfg = load_config().get("model") or {}
    requested = model_cfg.get("provider")
    target_model = model_cfg.get("default")
    runtime = resolve_runtime_provider(requested=requested, target_model=target_model)
    return AIAgent(
        api_key=runtime.get("api_key"),
        base_url=runtime.get("base_url"),
        provider=runtime.get("provider"),
        requested_provider=runtime.get("requested_provider"),
        api_mode=runtime.get("api_mode"),
        model=target_model or runtime.get("model"),
        enabled_toolsets=[],
        quiet_mode=True,
        platform="cli",
        skip_context_files=True,
        skip_memory=True,
        max_iterations=1,
    )


def _availability_suffix(registry, name: str) -> str:
    """Annotate engines that can be registered but cannot run on this host."""
    from agent.runtime_port import RuntimeRefusalError

    resolve = getattr(registry.get(name), "resolve", None)
    if not callable(resolve):
        return ""
    try:
        resolve()
    except RuntimeRefusalError as exc:
        return f" (unavailable: {exc.code})"
    except Exception:  # noqa: BLE001 - listing must never crash on a probe
        return " (unavailable)"
    return ""


def cmd_runtime(args, *, registry=None) -> int:
    if registry is None:
        from agent.runtime_registry import build_registry
        registry = build_registry(_native_factory)

    if args.runtime_action == "engines":
        for name in registry.available():
            print(name + _availability_suffix(registry, name))
        return 0

    try:
        backend = registry.get(args.engine)
    except UnknownRuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    prompt, prompt_error = _prompt(args)
    if prompt_error:
        print(prompt_error, file=sys.stderr)
        return 2
    if not prompt:
        print("runtime run: --prompt or --query-file is required", file=sys.stderr)
        return 2

    request = TurnRequest(prompt=prompt, session_id=f"runtime-port-{uuid.uuid4().hex[:12]}")
    try:
        events = [
            {"kind": event.kind, "text": event.text, "data": event.data}
            for event in backend.run_turn(request)
        ]
    except KeyboardInterrupt:
        # A backend that spawns a child reaps it before re-raising; keep the
        # conventional SIGINT exit code instead of a traceback.
        print("runtime run: interrupted", file=sys.stderr)
        return 130
    if args.json:
        print(json.dumps({"engine": backend.name, "events": events},
                         ensure_ascii=False, default=str))
    else:
        for event in events:
            if event["text"]:
                print(f"[{event['kind']}] {event['text']}")
    terminal = events[-1] if events else None
    if terminal and terminal["kind"] == "error" and terminal["data"].get("refusal"):
        # A typed refusal means no turn was attempted; keep it distinct from a
        # failed turn (exit 1) and print the actionable hint.
        hint = terminal["data"].get("hint")
        if hint and not args.json:
            print(hint, file=sys.stderr)
        return 2
    return 0 if terminal and terminal["kind"] == "done" else 1
