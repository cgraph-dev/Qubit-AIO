"""`/chat` registry slash commands resolve through the real slash worker (audit D-24).

Audit D-24 (``docs/generated/QUBIT_DASHBOARD_AUDIT_2026-09-23.json``) reported that
``/toolsets``, ``/context``, ``/whoami`` and ``/version`` returned ``error: not a
quick/plugin/bundle/skill command: …`` (JSON-RPC 4018) in the live ``/chat``
terminal. That text is ``tui_gateway/methods_tools.py:command.dispatch``'s final
refusal — the branch the TUI client only reaches when ``slash.exec`` has already
failed.

The commands are registry commands (``qubit_cli/commands.py``), not
quick/plugin/bundle/skill commands, so ``command.dispatch`` refuses them **by
design** — the reference backend (``hermes-agent`` 0.21.4) returns the identical
4018 (verified in-process). What broke in Qubit was ``slash.exec`` itself: the
``/chat`` TUI spawns ``python -m tui_gateway.slash_worker`` from the caller's cwd
with a sanitizer-stripped PYTHONPATH, so before the D-1 fix the worker died on
``ModuleNotFoundError: No module named 'tui_gateway'`` and ``slash.exec`` errored,
pushing every registry command into the 4018 fallback.

This test drives the exact production worker (``_slash_worker_env`` +
``tui_gateway.slash_worker``) with no live session and asserts each command
returns its real output. It locks the working path the live ``/chat`` uses and
would fail if the PYTHONPATH seed regressed.

No live model turn is required: the worker's ``QubitCLI`` answers these from the
registry / live-session read path without an agent.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "qubit_runtime"
sys.path.insert(0, str(RUNTIME_ROOT))

# Importing tui_gateway.server reassigns sys.stdout for the JSON-RPC transport;
# restore it so the hermetic gate keeps capturing test output.
_stdout = sys.stdout
from tui_gateway import server as tui_server  # noqa: E402

sys.stdout = _stdout


def _run_commands(commands: list[str], tmp_path: Path) -> dict[str, str]:
    """Drive the production slash worker exactly as ``_SlashWorker`` spawns it."""
    env = tui_server._slash_worker_env()
    env["QUBIT_HOME"] = str(tmp_path / "home")
    Path(env["QUBIT_HOME"]).mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [sys.executable, "-m", "tui_gateway.slash_worker",
         "--session-key", "audit-d24-probe"],
        cwd=tmp_path, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1,
    )
    out: dict[str, str] = {}
    try:
        for i, command in enumerate(commands):
            proc.stdin.write(json.dumps({"id": i, "command": command}) + "\n")
            proc.stdin.flush()
            reply = json.loads(proc.stdout.readline())
            assert reply.get("ok") is True, reply
            out[command] = reply.get("output", "")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=20)
        except Exception:
            proc.kill()
            proc.wait(timeout=20)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                pipe.close()
            except Exception:
                pass
    return out


def test_registry_slash_commands_return_real_output(tmp_path):
    outputs = _run_commands(["/version", "/whoami", "/toolsets", "/context"], tmp_path)

    assert "Qubit Agent v" in outputs["/version"]
    assert "Install directory:" in outputs["/version"]

    who = outputs["/whoami"]
    assert "User:" in who and "Tier:" in who and "Slash commands:" in who

    assert "Toolsets" in outputs["/toolsets"]
    assert "tools]" in outputs["/toolsets"]

    # No live agent on a fresh worker: /context reports that honestly instead of
    # erroring (the 4018 the audit saw came from the failed slash.exec fallback).
    ctx = outputs["/context"]
    assert "No active agent" in ctx or "Conversation" in ctx


def test_no_command_returns_the_dispatch_refusal(tmp_path):
    """None of the four may surface the ``command.dispatch`` 4018 text."""
    outputs = _run_commands(["/version", "/whoami", "/toolsets", "/context"], tmp_path)
    for command, output in outputs.items():
        assert "not a quick/plugin/bundle/skill command" not in output, (command, output)
