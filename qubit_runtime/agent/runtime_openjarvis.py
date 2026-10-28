"""OpenJarvis RuntimePort backend — out-of-process, pinned CLI adapter.

OpenJarvis is a separate product with its own dependencies. It never imports
into the Qubit process: this adapter spawns the pinned ``jarvis ask`` CLI and
maps its JSON result onto the port's event stream. The CLI is a one-shot
interface (it keeps no conversation state we may reuse), so the turn's system
prompt and history are rendered into the single prompt and Qubit stays the
owner of session state.

Pinned interface (``open-jarvis/OpenJarvis@05839014127772bd077ba9ba2a2393a007f63ac6``,
Apache-2.0; provenance in ``THIRD_PARTY_NOTICES.md``)::

    jarvis ask --json --no-stream --no-context [-m MODEL]
        [--agent NAME] [--tools a,b] -- <prompt>

The direct-to-engine result is an OpenAI-shaped object with ``content`` and
``usage``; agent mode returns ``content``, ``turns`` and ``tool_results``.
``--agent ""`` selects direct mode (the pinned CLI falls back to its configured
default agent when the flag is absent). ``OPENJARVIS_HOME`` isolates the child's
state from any user install.

Refusals are typed, never a silent fallback to native:
``openjarvis_unavailable`` (no runnable executable), ``openjarvis_commit_unverifiable``
(not a git checkout), ``openjarvis_checkout_dirty`` (uncommitted tracked edits),
``openjarvis_commit_mismatch`` (wrong source pin), ``openjarvis_binary_mismatch``
(the executable's build stamp names another commit), ``openjarvis_interface_mismatch``
(the CLI rejected a pinned flag), ``openjarvis_invalid_prompt`` (NUL byte),
``openjarvis_prompt_too_large`` (argument/byte budget) and
``openjarvis_home_unavailable`` (state directory cannot be created). Each is a
terminal ``error`` event carrying ``data["refusal"]`` and an actionable
``data["hint"]``.

The executable is operator configuration (an editable install normally lives
outside the source checkout), so the adapter records its absolute path in the
terminal event and verifies the checkout it came from: the source must be a
clean git checkout at the pinned commit.
"""
from __future__ import annotations

import errno
import json
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
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
_TERMINATE_GRACE_S = 2.0
_POST_EXIT_DRAIN_S = 1.0
_MAX_PROMPT_CHARS = 60_000
_MAX_PROMPT_BYTES = 120_000
_MAX_STDOUT_BYTES = 2_000_000
_MAX_STDERR_BYTES = 200_000
_MAX_STDERR_CHARS = 2_000
_MAX_TOOL_NAME_CHARS = 200
_READ_CHUNK = 65_536
# A direct ``resolve()`` caller (``qubit runtime engines``, diagnostics, tests)
# may repeat the git/version probes within milliseconds; that short cache keeps
# the one-shot CLI fast. Every turn revalidates explicitly (``force=True``) so
# a long-lived gateway notices an edited checkout, a moved pin or a swapped
# binary before it spawns a turn.
_DEFAULT_RESOLVE_CACHE_S = 5.0
_USAGE_KEYS = ("prompt_tokens", "prompt_tokens_evaluated", "completion_tokens", "total_tokens")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SECRET_HINT = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|PASSWD)", re.IGNORECASE)
_SECRET_SHAPE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-[A-Za-z0-9_\-]{8,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|"
    r"xox[abprs]-[A-Za-z0-9\-]{10,}|"
    r"AKIA[0-9A-Z]{16}"
    r")"
)


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
        resolve_cache_seconds: float | None = None,
    ) -> None:
        self._binary = binary or os.environ.get(BIN_ENV) or None
        self._source = source or os.environ.get(SOURCE_ENV) or None
        try:
            self._home_raw = str(home or os.environ.get(HOME_ENV) or self._default_home())
        except (OSError, RuntimeError, UnicodeError):
            self._home_raw = str(Path.home() / ".qubit" / "openjarvis")
        self._home = Path(self._home_raw)
        self._model = model if model is not None else os.environ.get(MODEL_ENV)
        self._agent = agent if agent is not None else os.environ.get(AGENT_ENV)
        self._tools = tools if tools is not None else os.environ.get(TOOLS_ENV)
        self._pinned_commit = pinned_commit
        if timeout_seconds is None:
            try:
                timeout_seconds = float(os.environ.get(TIMEOUT_ENV) or _DEFAULT_TIMEOUT_S)
            except ValueError:
                timeout_seconds = _DEFAULT_TIMEOUT_S
        timeout_seconds = float(timeout_seconds)
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            timeout_seconds = _DEFAULT_TIMEOUT_S
        self._timeout = max(1.0, timeout_seconds)
        if resolve_cache_seconds is None:
            resolve_cache_seconds = _DEFAULT_RESOLVE_CACHE_S
        try:
            cache_seconds = float(resolve_cache_seconds)
        except (TypeError, ValueError):
            cache_seconds = _DEFAULT_RESOLVE_CACHE_S
        self._resolve_cache_s = (
            cache_seconds if math.isfinite(cache_seconds) and cache_seconds >= 0 else 0.0)
        self._secret_values = self._collect_secret_values()
        self._resolved: ResolvedOpenJarvis | None = None
        self._resolved_at: float | None = None
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
            if not value or len(value) < 8:
                continue
            if _SECRET_HINT.search(key) or "://" in value:
                values.append(value)
        return tuple(sorted(set(values), key=len, reverse=True))

    def _redact(self, text: str) -> str:
        for secret in self._secret_values:
            text = text.replace(secret, "***")
        return _SECRET_SHAPE.sub("***", text)

    def _safe_text(self, value: Any, limit: int) -> str:
        """Redact BEFORE bounding so a cut can never expose a secret prefix."""
        return _bounded(self._redact(value if isinstance(value, str) else str(value or "")), limit)

    # ------------------------------------------------------------- pin/lifecycle

    def resolve(self, *, force: bool = False) -> ResolvedOpenJarvis:
        """Locate the pinned CLI; raise :class:`RuntimeRefusalError` otherwise.

        *force* re-runs every probe (path, checkout commit, dirty tree, binary
        build stamp). A turn always forces, so a long-lived backend never runs
        yesterday's verified pin. Without *force*, a result is reused for at
        most ``resolve_cache_seconds`` so the one-shot CLI and
        ``qubit runtime engines`` do not pay for repeated subprocess probes.
        """
        with self._resolve_lock:
            now = time.monotonic()
            if (
                not force
                and self._resolved is not None
                and self._resolved_at is not None
                and (now - self._resolved_at) < self._resolve_cache_s
            ):
                return self._resolved
            resolved = self._resolve_uncached()
            self._resolved = resolved
            self._resolved_at = time.monotonic()
            return resolved

    def _resolve_uncached(self) -> ResolvedOpenJarvis:
        self._home = self._resolve_home()
        binary = self._binary or shutil.which("jarvis")
        if not binary:
            raise RuntimeRefusalError(
                "openjarvis_unavailable",
                "the OpenJarvis 'jarvis' CLI is not installed or not on PATH",
                _INSTALL_HINT,
            )
        binary_path = self._absolute_binary(binary)
        if binary_path is None:
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
        status = self._git(["status", "--porcelain", "--untracked-files=no"], Path(source))
        if status is None:
            raise RuntimeRefusalError(
                "openjarvis_commit_unverifiable",
                f"cannot read the git status of OpenJarvis source at {source}",
                f"QUBIT_OPENJARVIS_SOURCE must be a readable git checkout. {_INSTALL_HINT}",
            )
        if status.strip():
            raise RuntimeRefusalError(
                "openjarvis_checkout_dirty",
                f"OpenJarvis source at {source} has uncommitted tracked changes",
                "Commit or stash them, then re-run; an edited checkout is not the "
                "reviewed interface.",
            )
        if commit != self._pinned_commit:
            raise RuntimeRefusalError(
                "openjarvis_commit_mismatch",
                f"OpenJarvis commit {commit[:12]} does not match the pinned "
                f"{self._pinned_commit[:12]}",
                f"Check out {self._pinned_commit} or point the pin at a reviewed "
                "interface change; refusing to run an unpinned interface.",
            )
        self._verify_binary_commit(binary_path)
        try:
            resolved_source = str(Path(source).resolve())
        except (OSError, RuntimeError, UnicodeError) as exc:
            raise RuntimeRefusalError(
                "openjarvis_commit_unverifiable",
                f"cannot resolve OpenJarvis source at {source}: {exc}",
                _INSTALL_HINT,
            ) from exc
        return ResolvedOpenJarvis(str(binary_path), resolved_source, commit)

    def _resolve_home(self) -> Path:
        try:
            home = Path(self._home_raw).expanduser()
            if not home.is_absolute():
                home = Path.cwd() / home
            return home.resolve()
        except (OSError, RuntimeError, UnicodeError) as exc:
            raise RuntimeRefusalError(
                "openjarvis_home_unavailable",
                f"cannot resolve the OpenJarvis home {self._home_raw!r}: {exc}",
                "Set QUBIT_OPENJARVIS_HOME to a resolvable directory.",
            ) from exc

    def _verify_binary_commit(self, binary: Path) -> None:
        """Bind the executable that will run to the pinned commit.

        The executable is operator config and an editable install normally lives
        outside the source checkout, so the checkout pin alone cannot prove what
        runs. ``jarvis --version`` carries a ``g<sha>`` build stamp; a missing or
        conflicting stamp is a refusal, never a silent pass.
        """
        output = self._probe_version(binary)
        if output is None:
            raise RuntimeRefusalError(
                "openjarvis_commit_unverifiable",
                f"cannot read a commit stamp from {binary} --version",
                "Use an executable installed from the pinned checkout; the build "
                "must report a g<sha> version stamp.",
            )
        match = re.search(r"g([0-9a-f]{7,40})", output)
        if not match:
            raise RuntimeRefusalError(
                "openjarvis_commit_unverifiable",
                f"{binary} --version carries no g<sha> commit stamp",
                "Install the pinned git checkout (a wheel without a commit stamp "
                "cannot be verified).",
            )
        stamp = match.group(1)
        if not self._pinned_commit.startswith(stamp):
            raise RuntimeRefusalError(
                "openjarvis_binary_mismatch",
                f"the 'jarvis' executable at {binary} reports commit {stamp}, "
                f"not the pinned {self._pinned_commit[:12]}",
                "Point QUBIT_OPENJARVIS_BIN at the executable installed from the "
                "pinned checkout, or reinstall it.",
            )

    def _probe_version(self, binary: Path) -> str | None:
        """Bounded, group-killing ``--version`` probe; ``None`` means no answer.

        Output goes to an unlinked temp file rather than a pipe: a descendant
        holding the inherited pipe can no longer block the read or leak a
        reader thread. Only the first 20 KiB is read, so a runaway banner is
        harmless.
        """
        env = dict(os.environ)
        env["OPENJARVIS_HOME"] = str(self._home)
        try:
            with tempfile.TemporaryFile(mode="w+b") as sink:
                proc = subprocess.Popen(
                    [str(binary), "--version"],
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                pgid = proc.pid
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    self._terminate(proc, pgid)
                    return None
                # Reap any probe descendant still holding the group alive.
                try:
                    os.killpg(pgid, 0)
                except ProcessLookupError:
                    pass
                else:
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                sink.seek(0)
                raw = sink.read(20_000)
        except (OSError, ValueError):
            return None
        if proc.returncode != 0:
            return None
        return raw.decode("utf-8", "replace")

    @staticmethod
    def _absolute_binary(binary: str) -> Path | None:
        """Resolve once, absolutely — never let Popen re-resolve a bare name on PATH."""
        try:
            path = Path(binary).expanduser()
            if not path.is_absolute():
                found = shutil.which(binary)
                path = Path(found) if found else Path.cwd() / path
            path = path.resolve()
        except (OSError, RuntimeError, UnicodeError):
            return None
        if not path.is_file() or not os.access(path, os.X_OK):
            return None
        return path

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
                capture_output=True, text=True, errors="replace",
                timeout=10, env=env, check=False,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError):
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
        if "\x00" in prompt:
            raise RuntimeRefusalError(
                "openjarvis_invalid_prompt",
                "the prompt contains a NUL byte",
                "Strip binary data before running a turn.",
            )
        try:
            prompt_bytes = len(prompt.encode("utf-8"))
        except UnicodeError as exc:
            raise RuntimeRefusalError(
                "openjarvis_invalid_prompt",
                f"the prompt is not valid UTF-8 text: {exc}",
                "Pass UTF-8 text; binary or surrogate-escaped argv cannot cross "
                "the CLI boundary.",
            ) from exc
        if len(prompt) > _MAX_PROMPT_CHARS or prompt_bytes > _MAX_PROMPT_BYTES:
            raise RuntimeRefusalError(
                "openjarvis_prompt_too_large",
                f"rendered prompt is {len(prompt)} characters / "
                f"{prompt_bytes} bytes "
                f"(limits {_MAX_PROMPT_CHARS} chars / {_MAX_PROMPT_BYTES} bytes)",
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
        """Never return zero events and never a second terminal event."""
        terminal_seen = False
        try:
            for event in self._run_turn(request, cancel):
                if event.kind in ("done", "error"):
                    terminal_seen = True
                yield event
        except RuntimeRefusalError as exc:
            if not terminal_seen:
                yield self._refusal_event(exc)
        except Exception as exc:  # noqa: BLE001 - the port owns the terminal event
            if not terminal_seen:
                yield TurnEvent(
                    "error",
                    text=self._safe_text(f"OpenJarvis adapter failure: {exc}", 2_000),
                    data={"engine": self.name, "adapter_error": self.name},
                )

    def _run_turn(
        self, request: TurnRequest, cancel: Any | None = None
    ) -> Iterator[TurnEvent]:
        try:
            resolved = self.resolve(force=True)
            prompt = self._render_prompt(request)
        except RuntimeRefusalError as exc:
            yield self._refusal_event(exc)
            return

        try:
            self._home.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            yield TurnEvent(
                "error",
                text=self._safe_text(f"cannot create the OpenJarvis home: {exc}", 2_000),
                data={
                    "refusal": "openjarvis_home_unavailable",
                    "hint": f"Set QUBIT_OPENJARVIS_HOME to a writable directory "
                            f"(currently {self._home}).",
                },
            )
            return

        try:
            proc = subprocess.Popen(
                self._argv(resolved, prompt),
                env=self._env(),
                cwd=resolved.source,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                start_new_session=True,
            )
        except OSError as exc:
            refusal = "openjarvis_unavailable"
            hint = _INSTALL_HINT
            if exc.errno == errno.E2BIG:
                refusal = "openjarvis_prompt_too_large"
                hint = "Shorten the prompt or its history."
            yield TurnEvent(
                "error",
                text=self._safe_text(f"cannot start OpenJarvis: {exc}", 2_000),
                data={"refusal": refusal, "hint": hint},
            )
            return
        except ValueError as exc:  # argv cannot carry a NUL — defence in depth
            yield TurnEvent(
                "error",
                text=self._safe_text(f"invalid OpenJarvis invocation: {exc}", 2_000),
                data={"refusal": "openjarvis_invalid_prompt",
                      "hint": "Strip binary data before running a turn."},
            )
            return

        pgid = proc.pid
        state = {"reaped": False}
        try:
            stdout, stderr, outcome = self._communicate(proc, pgid, cancel, state)
        except BaseException:
            # _communicate terminates in its own finally; this is only a guard
            # for an exception before that finally exists.
            if not state["reaped"]:
                self._terminate(proc, pgid)
                state["reaped"] = True
            raise

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
        if outcome == "output_overflow":
            yield TurnEvent(
                "error",
                text="OpenJarvis produced more output than the adapter accepts",
                data={"engine": self.name, "output_overflow": True},
            )
            return
        if proc.returncode != 0:
            detail = self._safe_text(stderr.strip(), _MAX_STDERR_CHARS)
            lowered = stderr.lower()
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
                text=self._safe_text(
                    "malformed OpenJarvis result: " + _bounded(stdout.strip(), _MAX_STDERR_CHARS),
                    2_000),
                data={"engine": self.name},
            )
            return

        error_text = payload.get("error") or payload.get("detail")
        # The answer is user content: diagnostic paths redact secrets, but a
        # model answer is returned verbatim (a user may legitimately ask about a
        # key). Only bounded.
        content = _bounded(payload.get("content", ""), 1_000_000)
        if error_text:
            # A payload that carries both content and an error is a failed turn:
            # surface the partial text, but never claim completion.
            if content:
                yield TurnEvent("delta", text=content)
            yield TurnEvent(
                "error",
                text=self._safe_text(error_text, _MAX_STDERR_CHARS),
                data={"engine": self.name},
            )
            return
        if payload.get("completed") is False:
            yield TurnEvent(
                "error",
                text="OpenJarvis reported an incomplete turn",
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
            "binary": _bounded(resolved.binary, 500),
            "tool_calls": len(tool_results),
        }
        if self._model:
            done_data["model"] = _bounded(self._model, 200)
        elif isinstance(payload.get("model"), str):
            done_data["model"] = _bounded(payload["model"], 200)
        if turns is not None:
            done_data["turns"] = turns
        yield TurnEvent("done", text=content, data=done_data)

    # ------------------------------------------------------------- process drain

    def _communicate(
        self,
        proc: subprocess.Popen,
        pgid: int,
        cancel: Any | None,
        state: dict[str, bool],
    ) -> tuple[str, str, str]:
        """Drain both pipes with hard byte caps, honour cancel/timeout, reap."""
        out_chunks: list[str] = []
        err_chunks: list[str] = []
        overflow = threading.Event()
        readers = [
            threading.Thread(
                target=self._drain, args=(proc.stdout, out_chunks, _MAX_STDOUT_BYTES, overflow),
                daemon=True, name="runtime-openjarvis-stdout"),
            threading.Thread(
                target=self._drain, args=(proc.stderr, err_chunks, _MAX_STDERR_BYTES, overflow),
                daemon=True, name="runtime-openjarvis-stderr"),
        ]
        for reader in readers:
            reader.start()

        deadline = time.monotonic() + self._timeout
        outcome = "completed"
        exited_at: float | None = None
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    outcome = "cancelled"
                    break
                if overflow.is_set():
                    outcome = "output_overflow"
                    break
                now = time.monotonic()
                if now >= deadline:
                    outcome = "timeout"
                    break
                if proc.poll() is not None:
                    if not any(r.is_alive() for r in readers):
                        break
                    # A descendant can inherit the pipes and keep them open
                    # after a successful exit. Drain briefly, then stop the
                    # group instead of waiting out the whole turn budget.
                    if exited_at is None:
                        exited_at = now
                    elif now - exited_at >= _POST_EXIT_DRAIN_S:
                        self._terminate(proc, pgid)
                        state["reaped"] = True
                        break
                time.sleep(0.05)
        except BaseException:
            outcome = "interrupted"
            raise
        finally:
            if outcome != "completed" and not state["reaped"]:
                self._terminate(proc, pgid)
                state["reaped"] = True
            for reader in readers:
                reader.join(timeout=_TERMINATE_GRACE_S)
            try:
                proc.wait(timeout=_TERMINATE_GRACE_S)
            except subprocess.TimeoutExpired:
                if not state["reaped"]:
                    self._terminate(proc, pgid)
                    state["reaped"] = True
                try:
                    proc.wait(timeout=_TERMINATE_GRACE_S)
                except subprocess.TimeoutExpired:  # pragma: no cover - unkillable child
                    pass
        return "".join(out_chunks), "".join(err_chunks), outcome

    @staticmethod
    def _drain(stream: Any, sink: list[str], cap: int, overflow: threading.Event) -> None:
        total = 0
        try:
            while True:
                chunk = stream.read(_READ_CHUNK)
                if not chunk:
                    break
                total += len(chunk.encode("utf-8", "replace"))
                if total > cap:
                    overflow.set()
                    break
                sink.append(chunk)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _terminate(self, proc: subprocess.Popen, pgid: int) -> None:
        """Terminate the child's whole group, then reap it (no orphan survives).

        The direct child can exit on SIGTERM while a descendant ignores it and
        keeps the inherited pipes open, so the group gets SIGKILL after a
        bounded grace regardless of the direct child's status.
        """
        self._signal_group(proc, pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=_TERMINATE_GRACE_S)
        except subprocess.TimeoutExpired:
            pass
        self._signal_group(proc, pgid, signal.SIGKILL)
        try:
            proc.wait(timeout=_TERMINATE_GRACE_S)
        except subprocess.TimeoutExpired:  # pragma: no cover - unkillable kernel child
            pass

    @staticmethod
    def _signal_group(proc: subprocess.Popen, pgid: int, sig: int) -> None:
        try:
            os.killpg(pgid, sig)
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
        # Tolerate trailing banner/status text after the JSON object.
        for candidate in candidates:
            try:
                parsed, _ = json.JSONDecoder().raw_decode(candidate)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    def _refusal_event(self, exc: RuntimeRefusalError) -> TurnEvent:
        return TurnEvent(
            "error",
            text=exc.message,
            data={"refusal": exc.code, "hint": exc.hint, "engine": self.name},
        )
