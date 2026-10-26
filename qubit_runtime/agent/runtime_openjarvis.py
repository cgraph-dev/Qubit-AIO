"""OpenJarvis RuntimePort backend — out-of-process, pinned CLI adapter.

OpenJarvis is a separate product with its own dependencies. It never imports
into the Qubit process: this adapter spawns the pinned ``jarvis ask`` CLI and
maps its JSON result onto the port's event stream. The CLI is a one-shot
interface (it keeps no conversation state we may reuse), so the turn's system
prompt and history are rendered into the single prompt and Qubit stays the
owner of session state.

Pinned interface (``open-jarvis/OpenJarvis@05839014127772bd077ba9ba2a2393a007f63ac6``,
Apache-2.0; provenance in ``THIRD_PARTY_NOTICES.md``)::

    jarvis ask <prompt> --json --no-stream --no-context [-m MODEL]
        [--agent NAME] [--tools a,b]

The direct-to-engine result is an OpenAI-shaped object with ``content`` and
``usage``; agent mode returns ``content``, ``turns`` and ``tool_results``.
``OPENJARVIS_HOME`` isolates the child's state from any user install.

Refusals are typed, never a silent fallback to native:
``openjarvis_unavailable`` (no executable), ``openjarvis_commit_unverifiable``
(not a git checkout), ``openjarvis_commit_mismatch`` (wrong pin),
``openjarvis_interface_mismatch`` (the CLI rejected a pinned flag) and
``openjarvis_prompt_too_large``. Each is a terminal ``error`` event carrying
``data["refusal"]`` and an actionable ``data["hint"]``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from agent.runtime_port import (
    RuntimeCapabilities,
    RuntimeRefusalError,
    TurnEvent,
    TurnRequest,
)

PINNED_REPO = "https://github.com/open-jarvis/OpenJarvis"
PINNED_COMMIT = "05839014127772bd077ba9ba2a2393a007f63ac6"

BIN_ENV = "QUBIT_OPENJARVIS_BIN"
SOURCE_ENV = "QUBIT_OPENJARVIS_SOURCE"
HOME_ENV = "QUBIT_OPENJARVIS_HOME"
MODEL_ENV = "QUBIT_OPENJARVIS_MODEL"
AGENT_ENV = "QUBIT_OPENJARVIS_AGENT"
TOOLS_ENV = "QUBIT_OPENJARVIS_TOOLS"
TIMEOUT_ENV = "QUBIT_OPENJARVIS_TIMEOUT_S"

_INSTALL_HINT = (
    "Install the pinned OpenJarvis checkout and point QUBIT_OPENJARVIS_BIN at "
    f"its 'jarvis' executable plus QUBIT_OPENJARVIS_SOURCE at the checkout "
    f"({PINNED_REPO} @ {PINNED_COMMIT[:12]})."
)

_DEFAULT_TIMEOUT_S = 300.0
_TERMINATE_GRACE_S = 5.0
_MAX_PROMPT_CHARS = 60_000
_MAX_STDERR_CHARS = 2_000
_MAX_TOOL_NAME_CHARS = 200
_USAGE_KEYS = ("prompt_tokens", "prompt_tokens_evaluated", "completion_tokens", "total_tokens")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SECRET_HINT = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.IGNORECASE)


def _bounded(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else str(value or "")
    return text if len(text) <= limit else text[:limit] + "…"


def _bounded_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


@dataclass(frozen=True)
class ResolvedOpenJarvis:
    binary: str
    source: str
    commit: str


class OpenJarvisRuntime:
    """Runs one turn through a pinned, out-of-process ``jarvis`` CLI."""

    name = "openjarvis"
    capabilities = RuntimeCapabilities(streaming=False, cancel=True, tools=True, images=False)

    def __init__(
        self,
        *,
        binary: str | None = None,
        source: str | None = None,
        home: str | Path | None = None,
        model: str | None = None,
        agent: str | None = None,
        tools: str | None = None,
        pinned_commit: str = PINNED_COMMIT,
        timeout_seconds: float | None = None,
    ) -> None:
        self._binary = binary or os.environ.get(BIN_ENV) or None
        self._source = source or os.environ.get(SOURCE_ENV) or None
        self._home = Path(home or os.environ.get(HOME_ENV) or self._default_home())
        self._model = model if model is not None else os.environ.get(MODEL_ENV)
        self._agent = agent if agent is not None else os.environ.get(AGENT_ENV)
        self._tools = tools if tools is not None else os.environ.get(TOOLS_ENV)
        self._pinned_commit = pinned_commit
        if timeout_seconds is None:
            try:
                timeout_seconds = float(os.environ.get(TIMEOUT_ENV) or _DEFAULT_TIMEOUT_S)
            except ValueError:
                timeout_seconds = _DEFAULT_TIMEOUT_S
        self._timeout = max(1.0, float(timeout_seconds))
        self._secret_values = self._collect_secret_values()
        self._resolved: ResolvedOpenJarvis | None = None
        self._resolve_lock = threading.Lock()

    # ------------------------------------------------------------------ config

    @staticmethod
    def _default_home() -> Path:
        try:
            from qubit_constants import get_qubit_home

            return Path(get_qubit_home()) / "openjarvis"
        except Exception:  # noqa: BLE001 - isolation is best-effort, never a crash
            return Path.home() / ".qubit" / "openjarvis"

    @staticmethod
    def _collect_secret_values() -> tuple[str, ...]:
        values = []
        for key, value in os.environ.items():
            if value and len(value) >= 8 and _SECRET_HINT.search(key):
                values.append(value)
        return tuple(values)

    def _redact(self, text: str) -> str:
        for secret in self._secret_values:
            text = text.replace(secret, "***")
        return text

    # ------------------------------------------------------------- pin/lifecycle

    def resolve(self) -> ResolvedOpenJarvis:
        """Locate the pinned CLI; raise :class:`RuntimeRefusalError` otherwise."""
        with self._resolve_lock:
            if self._resolved is not None:
                return self._resolved
            self._resolved = self._resolve_uncached()
            return self._resolved

    def _resolve_uncached(self) -> ResolvedOpenJarvis:
        binary = self._binary or shutil.which("jarvis")
        if not binary:
            raise RuntimeRefusalError(
                "openjarvis_unavailable",
                "the OpenJarvis 'jarvis' CLI is not installed or not on PATH",
                _INSTALL_HINT,
            )
        binary_path = Path(binary).expanduser()
        if not binary_path.is_file() or not os.access(binary_path, os.X_OK):
            raise RuntimeRefusalError(
                "openjarvis_unavailable",
                f"OpenJarvis executable is not runnable: {binary}",
                _INSTALL_HINT,
            )
        source = self._source or self._git_toplevel(binary_path.parent)
        if not source:
            raise RuntimeRefusalError(
                "openjarvis_commit_unverifiable",
                "cannot determine the OpenJarvis source checkout to verify its commit",
                f"Set QUBIT_OPENJARVIS_SOURCE to the pinned checkout. {_INSTALL_HINT}",
            )
        commit = self._git_head(source)
        if not commit:
            raise RuntimeRefusalError(
                "openjarvis_commit_unverifiable",
                f"cannot read a commit from OpenJarvis source at {source}",
                f"QUBIT_OPENJARVIS_SOURCE must be a git checkout. {_INSTALL_HINT}",
            )
        if commit != self._pinned_commit:
            raise RuntimeRefusalError(
                "openjarvis_commit_mismatch",
                f"OpenJarvis commit {commit[:12]} does not match the pinned "
                f"{self._pinned_commit[:12]}",
                f"Check out {self._pinned_commit} or point the pin at a reviewed "
                "interface change; refusing to run an unpinned interface.",
            )
        return ResolvedOpenJarvis(str(binary_path), str(Path(source).resolve()), commit)

    @staticmethod
    def _git_toplevel(start: Path) -> str | None:
        out = OpenJarvisRuntime._git(["rev-parse", "--show-toplevel"], start)
        return out.strip() if out else None

    @staticmethod
    def _git_head(source: str) -> str | None:
        out = OpenJarvisRuntime._git(["rev-parse", "HEAD"], Path(source))
        commit = (out or "").strip().lower()
        return commit if _COMMIT_RE.match(commit) else None

    @staticmethod
    def _git(args: list[str], cwd: Path) -> str | None:
        env = dict(os.environ)
        env["GIT_OPTIONAL_LOCKS"] = "0"
        env["GIT_TERMINAL_PROMPT"] = "0"
        try:
            proc = subprocess.run(
                ["git", "-C", str(cwd), *args],
                capture_output=True, text=True, timeout=10, env=env, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return proc.stdout if proc.returncode == 0 else None

    # ------------------------------------------------------------------- prompt

    def _render_prompt(self, request: TurnRequest) -> str:
        if not request.system and not request.history:
            prompt = request.prompt
        else:
            blocks: list[str] = []
            if request.system:
                blocks.append(f"System:\n{_bounded(request.system, _MAX_PROMPT_CHARS)}")
            if request.history:
                lines = []
                for message in request.history[-200:]:
                    role = _bounded(message.get("role") or "user", 40)
                    content = _bounded(message.get("content") or "", 4_000)
                    lines.append(f"{role}: {content}")
                blocks.append("\n".join(lines))
            blocks.append(request.prompt)
            prompt = "\n\n".join(blocks)
        if len(prompt) > _MAX_PROMPT_CHARS:
            raise RuntimeRefusalError(
                "openjarvis_prompt_too_large",
                f"rendered prompt is {len(prompt)} characters "
                f"(limit {_MAX_PROMPT_CHARS})",
                "Shorten the history or use a backend that accepts structured "
                "history; the pinned OpenJarvis CLI takes one positional query.",
            )
        return prompt

    def _argv(self, resolved: ResolvedOpenJarvis, prompt: str) -> list[str]:
        # Options first, then ``--`` so a prompt that starts with "-" is never
        # parsed as a flag by Click.
        argv = [
            resolved.binary, "ask", "--json", "--no-stream", "--no-context",
        ]
        if self._model:
            argv.extend(["-m", self._model])
        # The pinned CLI falls back to `agent.default_agent` when --agent is
        # absent. Qubit owns turn semantics, so direct mode is explicit unless
        # the owner configures an OpenJarvis agent.
        argv.extend(["--agent", self._agent or ""])
        if self._tools:
            argv.extend(["--tools", self._tools])
        argv.extend(["--", prompt])
        return argv

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["OPENJARVIS_HOME"] = str(self._home)
        env.setdefault("PYTHONUNBUFFERED", "1")
        return env

    # --------------------------------------------------------------- turn/event

    def run_turn(self, request: TurnRequest, cancel: Any | None = None) -> Iterator[TurnEvent]:
        try:
            resolved = self.resolve()
            prompt = self._render_prompt(request)
        except RuntimeRefusalError as exc:
            yield self._refusal_event(exc)
            return

        self._home.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.Popen(
                self._argv(resolved, prompt),
                env=self._env(),
                cwd=resolved.source,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            yield TurnEvent(
                "error",
                text=self._redact(f"cannot start OpenJarvis: {exc}"),
                data={"refusal": "openjarvis_unavailable", "hint": _INSTALL_HINT},
            )
            return

        stdout, stderr, outcome = self._communicate(proc, cancel)
        if outcome == "cancelled":
            yield TurnEvent(
                "error",
                text="openjarvis turn cancelled",
                data={"cancelled": True, "interrupted": True, "engine": self.name},
            )
            return
        if outcome == "timeout":
            yield TurnEvent(
                "error",
                text=f"openjarvis turn timed out after {self._timeout:g}s",
                data={"cancelled": True, "timeout": True, "engine": self.name},
            )
            return
        if proc.returncode != 0:
            detail = self._redact(_bounded(stderr.strip(), _MAX_STDERR_CHARS))
            lowered = detail.lower()
            if "no such option" in lowered or "unrecognized arguments" in lowered:
                yield TurnEvent(
                    "error",
                    text=f"OpenJarvis rejected the pinned CLI interface: {detail}",
                    data={
                        "refusal": "openjarvis_interface_mismatch",
                        "hint": f"Use the pinned commit {self._pinned_commit[:12]}.",
                        "exit_code": proc.returncode,
                    },
                )
                return
            yield TurnEvent(
                "error",
                text=f"OpenJarvis exited with status {proc.returncode}: {detail}",
                data={"exit_code": proc.returncode, "engine": self.name},
            )
            return

        payload = self._parse(stdout)
        if payload is None:
            yield TurnEvent(
                "error",
                text=self._redact(
                    "malformed OpenJarvis result: " + _bounded(stdout.strip(), _MAX_STDERR_CHARS)),
                data={"engine": self.name},
            )
            return

        error_text = payload.get("error") or payload.get("detail")
        content = _bounded(payload.get("content", ""), 1_000_000)
        if error_text and not content:
            yield TurnEvent(
                "error",
                text=self._redact(_bounded(error_text, _MAX_STDERR_CHARS)),
                data={"engine": self.name},
            )
            return

        tool_results = payload.get("tool_results")
        tool_results = tool_results if isinstance(tool_results, list) else []
        if content:
            # One-shot CLI, no token stream: emit the whole answer as a single
            # delta so delta-only consumers still see text; capabilities.streaming
            # stays False to advertise that honestly.
            yield TurnEvent("delta", text=content)
        for result in tool_results[:64]:
            if not isinstance(result, Mapping):
                continue
            tool_name = _bounded(
                result.get("tool_name") or result.get("name") or "", _MAX_TOOL_NAME_CHARS)
            yield TurnEvent(
                "tool",
                text=tool_name,
                data={"tool": tool_name, "success": bool(result.get("success", True))},
            )

        usage = payload.get("usage")
        if isinstance(usage, Mapping):
            bounded_usage = {
                key: value for key in _USAGE_KEYS
                if (value := _bounded_int(usage.get(key))) is not None
            }
            if bounded_usage:
                yield TurnEvent("usage", data=bounded_usage)

        turns = _bounded_int(payload.get("turns"))
        done_data: dict[str, Any] = {
            "completed": True,
            "engine": self.name,
            "commit": resolved.commit,
            "tool_calls": len(tool_results),
        }
        if self._model:
            done_data["model"] = _bounded(self._model, 200)
        elif isinstance(payload.get("model"), str):
            done_data["model"] = _bounded(payload["model"], 200)
        if turns is not None:
            done_data["turns"] = turns
        yield TurnEvent("done", text=content, data=done_data)

    def _communicate(
        self, proc: subprocess.Popen, cancel: Any | None
    ) -> tuple[str, str, str]:
        """Drain the child while honoring cancel/timeout; always reaps it."""
        deadline = time.monotonic() + self._timeout
        while True:
            if cancel is not None and cancel.is_set():
                self._terminate(proc)
                return "", "", "cancelled"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._terminate(proc)
                return "", "", "timeout"
            try:
                stdout, stderr = proc.communicate(timeout=min(0.1, remaining))
                return stdout or "", stderr or "", "completed"
            except subprocess.TimeoutExpired:
                continue

    def _terminate(self, proc: subprocess.Popen) -> None:
        """Terminate the child's whole group, then reap it (no orphan survives)."""
        if proc.poll() is not None:
            return
        self._signal_group(proc, signal.SIGTERM)
        try:
            proc.communicate(timeout=_TERMINATE_GRACE_S)
            return
        except subprocess.TimeoutExpired:
            pass
        self._signal_group(proc, signal.SIGKILL)
        try:
            proc.communicate(timeout=_TERMINATE_GRACE_S)
        except subprocess.TimeoutExpired:  # pragma: no cover - unkillable kernel child
            pass

    @staticmethod
    def _signal_group(proc: subprocess.Popen, sig: int) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError):
            try:
                proc.send_signal(sig)
            except (ProcessLookupError, OSError):
                pass

    @staticmethod
    def _parse(stdout: str) -> dict[str, Any] | None:
        text = (stdout or "").strip()
        if not text:
            return None
        candidates = [text]
        start = text.find("{")
        if start > 0:
            candidates.append(text[start:])
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(parsed, dict):
                return parsed
        if start > 0:
            # Tolerate trailing banner/status text after the JSON object.
            try:
                parsed, _ = json.JSONDecoder().raw_decode(text[start:])
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                return parsed
        return None

    def _refusal_event(self, exc: RuntimeRefusalError) -> TurnEvent:
        return TurnEvent(
            "error",
            text=exc.message,
            data={"refusal": exc.code, "hint": exc.hint, "engine": self.name},
        )
