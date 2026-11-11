"""Live-DB test-isolation guard and the per-process "last init error" record.
Every SessionDB construction resolves its path through _ensure_test_isolation
so a pytest-context process (env OR ancestry) can never open a production
state.db; env-based so subprocess children are protected too."""

import os
import sys
import threading
import weakref
from pathlib import Path
from typing import Any, Optional

try:  # Hard dependency, but tolerate scaffold-phase imports before pip install.
    import psutil
except ImportError:  # pragma: no cover - stripped/scaffold installs only
    psutil = None  # type: ignore[assignment]

# Field evidence: pytest fixture rows landed in the production state.db and a
# pytest-spawned child flipped the journal mode under the live WAL writer.

#: Env twin of ``_STATE_DB_GUARD_BYPASS`` for child processes (a module global
#: cannot cross a process boundary, and ancestry arms the guard there).
_STATE_DB_GUARD_BYPASS_ENV = "HERMES_STATE_DB_GUARD_BYPASS"


def _real_platform_state_root() -> Optional[Path]:
    """The REAL platform-default Qubit root (canonical ``.qubit``). Avoids
    ``Path.home()`` / ``qubit_constants`` (tests monkeypatch Path.home to a
    tempdir); ``expanduser`` reads HOME/passwd, which the conftest never rewrites."""
    return _platform_state_root_for(".qubit", "qubit")


def _legacy_platform_state_root() -> Optional[Path]:
    """The pre-rebrand platform default (``.hermes``), still accepted read-old and
    therefore still a production root the test guard must refuse."""
    return _platform_state_root_for(".hermes", "hermes")


def _platform_state_root_for(dir_name: str, win_name: str) -> Optional[Path]:
    try:
        home = Path(os.path.expanduser("~"))
        if sys.platform == "win32":
            base = os.environ.get("LOCALAPPDATA", "").strip()
            root = Path(base) / win_name if base else home / "AppData" / "Local" / win_name
        else:
            root = home / dir_name
        return root.resolve()
    except Exception:
        return None


#: Exported by the hermetic conftest alongside the QUBIT_HOME redirect. Unlike
#: PYTEST_* it is OURS and inherits by default, so a child carrying it that
#: resolves a production DB is by definition an isolation escape.
# : Env marker exported by the hermetic test conftest at the same moment it : redirects ``QUBIT_HOME`` to
# the per-session tmp isolation root. Unlike ``PYTEST_*`` (owned by pytest, and : routinely scrubbed by
# tests that rebuild a child environment), this marker : is OURS: it declares "this process tree is running
# under Qubit test : isolation", and it inherits into subprocess children by default — so a : child that
# received the patched ``QUBIT_HOME`` also received the marker, : and a child that resolves a production DB
# while carrying it is, by : definition, an isolation escape (#82770).
_TEST_ISOLATION_MARKER_ENV = "HERMES_TEST_ISOLATION"

#: The root ``tests/conftest.py`` marker. Kept separate from
#: ``HERMES_TEST_ISOLATION`` because that name also flips strict wire-contract
#: validation (``tui_gateway.contracts.registry.STRICT``); the root suite must
#: not silently change contract strictness. Both count as test context here.
_ROOT_TEST_ISOLATION_MARKER_ENV = "QUBIT_TEST_ISOLATION"


def _running_under_pytest() -> bool:
    """True when this process (or a parent test process) is a pytest run."""
    return bool(
        os.environ.get("PYTEST_CURRENT_TEST")
        or os.environ.get("PYTEST_VERSION")
        or os.environ.get(_TEST_ISOLATION_MARKER_ENV)
        or os.environ.get(_ROOT_TEST_ISOLATION_MARKER_ENV)
    )


class _ProcAncestor:
    """Minimal psutil-like ancestor used when psutil is unavailable (Linux /proc)."""

    __slots__ = ("pid",)

    def __init__(self, pid: int) -> None:
        self.pid = pid

    def cmdline(self) -> list:
        try:
            with open(f"/proc/{self.pid}/cmdline", "rb") as fh:
                raw = fh.read()
        except Exception:
            return []
        return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]

    def environ(self) -> dict:
        try:
            with open(f"/proc/{self.pid}/environ", "rb") as fh:
                raw = fh.read()
        except Exception:
            return {}
        env: dict = {}
        for item in raw.split(b"\0"):
            if b"=" in item:
                key, value = item.split(b"=", 1)
                env[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
        return env


def _proc_ppid(pid: int) -> int:
    """Parent pid from ``/proc/<pid>/stat`` (field 4; ``comm`` may contain spaces/parens)."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
        rest = data.rsplit(b")", 1)[1].split()
        return int(rest[1])
    except Exception:
        return 0


def _iter_ancestors():
    """Yield ancestor processes nearest-first. psutil when present; Linux ``/proc`` fallback.

    Ancestry is the one test/agent signal that survives a child's rebuilt environment
    (``PYTEST_*``, ``QUBIT_HOME`` and harness markers all live in the same env carrier and
    are lost together — #82770). The ``/proc`` path keeps that signal available on hosts
    without psutil instead of silently failing open.
    """
    if psutil is not None:
        try:
            yield from psutil.Process().parents()
            return
        except Exception:
            return
    pid, seen = os.getpid(), {os.getpid()}
    while True:
        ppid = _proc_ppid(pid)
        if not ppid or ppid in seen:
            return
        seen.add(ppid)
        yield _ProcAncestor(ppid)
        pid = ppid


def _current_cmdline() -> list:
    """This process's argv (psutil, Linux /proc, then ``sys.argv``)."""
    if psutil is not None:
        try:
            return list(psutil.Process().cmdline() or [])
        except Exception:
            pass
    try:
        with open("/proc/self/cmdline", "rb") as fh:
            return [part.decode("utf-8", "replace") for part in fh.read().split(b"\0") if part]
    except Exception:
        return list(sys.argv)


def _argv_basename(arg: Any) -> str:
    try:
        return str(arg).strip('"').strip("'").replace("\\", "/").rsplit("/", 1)[-1].lower()
    except Exception:
        return ""


#: pytest launcher names, matched against each argv token's *basename* so
#: ``/tmp/pytest-of-dev/...`` paths cannot false-positive.
_PYTEST_LAUNCHER_NAMES = frozenset({"pytest", "py.test", "pytest.exe", "py.test.exe"})

#: Memoised ancestry answer: the tree above us doesn't change; keep the hot path free.
_PYTEST_ANCESTOR: Optional[bool] = None


def _process_looks_like_pytest(proc: Any) -> bool:
    """True when *proc*'s command line is a pytest invocation. Unreadable cmdline
    => not pytest: guessing the other way would refuse production opens."""
    try:
        cmdline = proc.cmdline() or []
    except Exception:
        return False
    # Split on both separators on every host so the answer is platform-independent.
    return any(_argv_basename(arg) in _PYTEST_LAUNCHER_NAMES for arg in cmdline)


def _has_pytest_ancestor() -> bool:
    """True when an ancestor process is a pytest run: a child spawned with a
    rebuilt env loses PYTEST_* and the QUBIT_HOME redirect together, ancestry
    survives that. Uses psutil when present, Linux ``/proc`` otherwise; fails
    open only when neither can be read.

    ``_running_under_pytest`` reads ``PYTEST_*`` env vars, which a child spawned with a rebuilt environment
    loses at the same moment it loses the ``QUBIT_HOME`` redirect: that child aims at the production DB
    *and* disarms the guard in one step (#82770). Ancestry is the one test-context signal that survives an
    env rebuild, so it backs the env check up.
    """
    global _PYTEST_ANCESTOR
    if _PYTEST_ANCESTOR is not None:
        return _PYTEST_ANCESTOR
    try:
        found = any(_process_looks_like_pytest(p) for p in _iter_ancestors())
    except Exception:
        found = False
    _PYTEST_ANCESTOR = found
    return found


def _in_test_context() -> bool:
    """Test run by environment or ancestry (memoised; env checked first)."""
    return _running_under_pytest() or _has_pytest_ancestor()


# --- Agent-subprocess signal -------------------------------------------------
# A test/agent subprocess may open the live ``state.db`` directly (a shell tool
# command, a spawned audit, a worker) even though it is not the live service.
# Writes from that shape are refused (reads stay allowed) so a rebuilt child env
# cannot mutate the operator's store (#2026-11-10 profile-delete incident).
#
# The marker below is OURS and is exported by the Qubit terminal/shell wrapper at
# the point a command is spawned, so it inherits into grandchildren. External
# coding-agent harnesses advertise themselves with their own markers; ancestry
# backs both up when a child rebuilds its environment and loses them.
_AGENT_SUBPROCESS_MARKER_ENV = "QUBIT_AGENT_SUBPROCESS"

#: Markers set by external coding-agent harnesses for their subprocesses.
_EXTERNAL_AGENT_ENV_MARKERS = (
    "OPENCODE", "CLAUDE_CODE", "CURSOR_AGENT", "CODEX", "AIDER", "GOOSE", "AGENT",
)

#: Agent launcher basenames; matched against argv tokens, never path prefixes.
_EXTERNAL_AGENT_NAMES = frozenset({
    "opencode", "claude", "claude-code", "codex", "cursor-agent", "aider", "goose",
})

#: Memoised ancestry answer for the agent signal.
_AGENT_ANCESTOR: Optional[bool] = None


def _process_looks_like_agent(proc: Any) -> bool:
    """True when *proc* is an external agent harness (cmdline basename or marker)."""
    try:
        cmdline = proc.cmdline() or []
    except Exception:
        cmdline = []
    if any(_argv_basename(arg) in _EXTERNAL_AGENT_NAMES for arg in cmdline):
        return True
    try:
        env = proc.environ() or {}
    except Exception:
        env = {}
    return any(key in env for key in _EXTERNAL_AGENT_ENV_MARKERS)


def _has_agent_ancestor() -> bool:
    """True when an ancestor is an external agent harness (survives env rebuilds)."""
    global _AGENT_ANCESTOR
    if _AGENT_ANCESTOR is not None:
        return _AGENT_ANCESTOR
    try:
        found = any(_process_looks_like_agent(p) for p in _iter_ancestors())
    except Exception:
        found = False
    _AGENT_ANCESTOR = found
    return found


def _in_agent_subprocess() -> bool:
    """True when this process is (or descends from) an agent-spawned subprocess.

    Deliberately NOT keyed on ``HERMES_AGENT``: the live gateway advertises that
    marker for its own children, so it cannot distinguish the service from a
    tool command. The Qubit-specific marker is set only by the shell wrapper.
    """
    if os.environ.get(_AGENT_SUBPROCESS_MARKER_ENV):
        return True
    if any(os.environ.get(key) for key in _EXTERNAL_AGENT_ENV_MARKERS):
        return True
    return _has_agent_ancestor()


def _looks_like_live_service() -> bool:
    """True for the live Qubit service/CLI entry points.

    A dev service launched from an agent shell must not be blocked by the
    agent-subprocess tripwire: its process (not its descendants) is exempt.
    Matches argv tokens, never arbitrary substrings (``pytest tests/test_voicebridge.py``
    must not look like the service).
    """
    cmdline = [str(arg) for arg in _current_cmdline()]
    bases = [_argv_basename(arg) for arg in cmdline]
    if "-m" in cmdline:
        try:
            module = cmdline[cmdline.index("-m") + 1].strip().lower()
        except IndexError:
            module = ""
        if module == "voicebridge" or module.startswith("voicebridge."):
            return True
        if module in {"qubit_cli.web_server", "qubit_cli.dashboard_procs"}:
            return True
        if module == "qubit_cli.main" and any(
            word in {"serve", "dashboard", "gateway", "webui"} for word in bases
        ):
            return True
    if bases and bases[0] in {"voicebridge", "voicebridge.exe", "qubit", "qubit.exe"}:
        return True
    return any(name in {"gateway_daemon.py", "web_server.py"} for name in bases)


class LiveStateDbWriteRefused(RuntimeError):
    """Typed refusal: a test/agent subprocess tried to WRITE the live state.db.

    Subclasses ``RuntimeError`` (the historical guard type) and keeps the
    ``live-system guard`` phrase so existing handlers keep matching. ``code``
    is stable for callers that need to distinguish it.
    """

    code = "LIVE_STATE_DB_WRITE_REFUSED"

    def __init__(self, detail: str, *, path: Any = None, root: Any = None) -> None:
        super().__init__(detail)
        self.detail = str(detail)
        self.path = str(path) if path is not None else ""
        self.root = str(root) if root is not None else ""

    def as_dict(self) -> dict:
        return {"state": "REFUSED", "code": self.code, "detail": self.detail,
                "path": self.path, "root": self.root}


def _is_production_state_db(resolved: Path, root: Path) -> bool:
    """*resolved* is ``<root>/state.db`` or ``<root>/profiles/<name>/state.db``;
    deeper scratch paths (repo worktrees) are deliberately NOT matched."""
    if resolved.parent == root:
        return True
    try:
        parts = resolved.relative_to(root).parts
    except ValueError:
        return False
    return len(parts) == 3 and parts[0] == "profiles"


# Test-only SessionDB instance registry. Under the hermetic suite every
# successfully constructed SessionDB is added to this WeakSet so the autouse
# teardown in tests/conftest.py (_close_leaked_session_dbs) can close whatever
# a test forgot to close. Dozens of tests build SessionDB() directly and never
# close it; each instance holds a writer connection plus pooled readers, and a
# single-process run over tests/qubit_cli/ accumulated 16-25 GB RSS (OOM
# incident 20260816). The per-file runner masks this in CI; the registry fixes
# the class at the source instead of patching ~40 test files.
#
# Population is gated on the isolation marker (exported by tests/conftest.py
# before any test module imports). Production processes never populate it;
# do not "simplify" the gate away. WeakSet membership never pins an instance.
_test_instance_registry: weakref.WeakSet[Any] = weakref.WeakSet()


def _register_test_instance(db: Any) -> None:
    """Track *db* for suite-level teardown closing (test-isolation runs only)."""
    if os.environ.get(_TEST_ISOLATION_MARKER_ENV):
        try:
            _test_instance_registry.add(db)
        except Exception:  # pragma: no cover — registry must never break init
            pass


# Last SessionDB() init error, per-process; surfaced by /resume-style slash
# commands so users know WHY. Only SessionDB.__init__ writes it.
_last_init_error: Optional[str] = None
_last_init_error_lock = threading.Lock()


def _set_last_init_error(msg: Optional[str]) -> None:
    """Record (or clear with None) the most recent init failure. __init__ never
    clears on success: a concurrent open would erase the cause another thread's
    /resume is about to format."""
    global _last_init_error
    with _last_init_error_lock:
        _last_init_error = msg


def get_last_init_error() -> Optional[str]:
    """Most recent state.db init failure (None if none/never attempted)."""
    return _last_init_error
