"""``qubit chat -q --engine <non-native>``: one single-query turn through the RuntimePort.

The classic ``-q`` path builds a vendored ``AIAgent`` and therefore cannot honour a
different engine. For a non-native engine this module skips that construction
entirely and runs the turn through the P2 backend (``-z``'s port helpers), so an
absent/unpinned engine refuses cleanly instead of silently falling back to native.

The native engine keeps the classic chat path (including its streamed output and
session summary); its single turn is routed through the port by
``qubit_cli.oneshot.run_agent_through_port``.

A native-store resume supplies history but the P2 adapter is stateless: the new
turn is not written to the native session store (same contract as ``qubit -z``).
Interactive clarify, native toolsets/model routing and image attachments are
native-only and are refused rather than silently dropped.

``--format stream-json`` carries the port's ``tool`` events: each becomes a
``tool_use``/``tool_result`` pair (the engine reports a completed-call summary,
so no input/output is invented). The schema can represent them, so this path
never silently drops a tool call.
"""
from __future__ import annotations

import sys
from typing import Any, Iterable

_INSTALL_HINT = (
    "Configure the engine with its own environment (QUBIT_OPENJARVIS_MODEL / "
    "QUBIT_OPENJARVIS_TOOLS) or omit these flags."
)

# Interactive clarify has no route through the P2 request/event contract. Rather
# than silently dropping the feature, the prompt carries the same bound `-z`'s
# clarify callback gives the native agent.
_NON_INTERACTIVE_NOTE = (
    "Single-query mode: no interactive user is available to answer clarify "
    "prompts. Choose the most reasonable defaults and continue."
)


def native_only_flags(args) -> tuple[str, ...]:
    """Flags present on *args* that the native engine consumes and P2 cannot.

    Only explicitly supplied values are listed; parser defaults (``SUPPRESS``)
    leave the attribute absent so a profile's own model/toolset defaults do not
    turn into a refusal.
    """
    checks = (
        ("--model", getattr(args, "model", None)),
        ("--provider", getattr(args, "provider", None)),
        ("--reasoning", getattr(args, "reasoning", None)),
        ("--toolsets", getattr(args, "toolsets", None)),
        ("--base-url", getattr(args, "base_url", None)),
        ("--api-key", getattr(args, "api_key", None)),
        ("--max-turns", getattr(args, "max_turns", None)),
        ("--run-budget", getattr(args, "run_budget", None)),
        ("--checkpoints", True if getattr(args, "checkpoints", False) else None),
        ("--ignore-rules", True if getattr(args, "ignore_rules", False) else None),
        ("--safe-mode", True if getattr(args, "safe_mode", False) else None),
        ("--ignore-user-config", True if getattr(args, "ignore_user_config", False) else None),
        ("--yolo", True if getattr(args, "yolo", False) else None),
        ("--accept-hooks", True if getattr(args, "accept_hooks", False) else None),
        ("--pass-session-id", True if getattr(args, "pass_session_id", False) else None),
        ("--worktree", True if getattr(args, "worktree", False) else None),
        ("--source", getattr(args, "source", None)),
    )
    return tuple(flag for flag, value in checks if value not in (None, "", [], ()))


def _port_exit_code(response: str, result: dict) -> int:
    """Map a non-native port outcome onto the single-query exit contract (1/130)."""
    if result.get("interrupted"):
        return 130
    if result.get("failed") or result.get("partial") or result.get("completed") is False:
        return 1
    return 0 if (response or "").strip() else 1


def _write_text(text: str) -> None:
    if not text:
        return
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


def _write_refusal(result: dict, *, program: str) -> None:
    refusal = str(result.get("refusal") or "")
    error = str(result.get("error") or refusal)
    prefix = f"[{refusal}] " if refusal and error != refusal else ""
    sys.stderr.write(f"{program}: {prefix}{error}\n")
    hint = str(result.get("hint") or "")
    if hint:
        sys.stderr.write(hint if hint.endswith("\n") else hint + "\n")
    sys.stderr.flush()


def run_non_native_single_query(
    prompt: str,
    *,
    engine: str,
    resume: str | None = None,
    skills: object = None,
    quiet: bool = False,
    stream_json: bool = False,
    flags: Iterable[str] = (),
    program: str = "qubit chat",
) -> int:
    """Run ``chat -q`` on a non-native engine; returns the process exit code.

    ``program`` only prefixes diagnostics. Deliberately does not import
    ``run_agent``/``AIAgent``: a refusal must never construct the native engine.
    """
    from qubit_cli.oneshot import _run_port_oneshot, _validate_engine

    engine = (engine or "native").strip() or "native"
    engine_error = _validate_engine(engine)
    if engine_error:
        sys.stderr.write(f"{program}: {engine_error}\n")
        return 2

    refused = tuple(dict.fromkeys(flags))
    if refused:
        sys.stderr.write(
            f"{program}: --engine {engine} does not accept {', '.join(refused)} yet; "
            f"{_INSTALL_HINT}\n"
        )
        return 2

    emitter = None
    streamed: list[str] = []
    sink = None
    tool_sink = None
    if stream_json:
        from qubit_cli.stream_json import StreamJsonEmitter

        emitter = StreamJsonEmitter(model="", session_id="")
        sink = emitter.on_text_delta
        tool_sink = emitter.on_port_tool_event
    elif not quiet:
        def sink(text: str) -> None:  # noqa: F811 - non-streaming/quiet paths leave it None
            if not text:
                return
            streamed.append(str(text))
            sys.stdout.write(str(text))
            sys.stdout.flush()

    try:
        response, result = _run_port_oneshot(
            prompt, engine=engine, skills=skills, resume=resume, stream=sink,
            extra_system=_NON_INTERACTIVE_NOTE, tool_sink=tool_sink,
        )
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - a refused/failed start is an exit code, not a crash
        sys.stderr.write(f"{program}: agent failed: {exc}\n")
        return 1

    session_id = str(result.get("session_id") or "")
    if result.get("refusal"):
        if emitter is not None:
            emitter.emit_result(result, session_id=session_id, exit_code=2)
        _write_refusal(result, program=program)
        return 2

    exit_code = _port_exit_code(response or "", result)
    if emitter is not None:
        return emitter.emit_result(result, session_id=session_id, exit_code=exit_code)

    if sink is not None:
        emitted = "".join(streamed)
        if response and response != emitted:
            # The backend streamed a prefix (or nothing); print only what it did not.
            tail = response[len(emitted):] if response.startswith(emitted) else response
            _write_text(tail)
        elif response and not emitted.endswith("\n"):
            sys.stdout.write("\n")
            sys.stdout.flush()
    elif response:
        _write_text(response)

    if result.get("failed") or result.get("partial") or result.get("completed") is False:
        error = str(result.get("error") or "")
        if error:
            sys.stderr.write(f"{program}: {error}\n")
            sys.stderr.flush()

    if quiet and session_id:
        # Same stderr contract as ``-Q`` so automation can attach to the session it resumed.
        print(f"\nSession: {session_id}", file=sys.stderr)
        print(f"session_id: {session_id}", file=sys.stderr)

    return exit_code
