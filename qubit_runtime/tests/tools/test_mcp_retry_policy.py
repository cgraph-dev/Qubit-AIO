"""Bounded MCP failure handling: unresolved env refs and auth rejection are terminal for the
process, and the child-stderr log cannot grow without bound.

Measured live: two broken servers were retried every ~5 minutes forever (an HTTP 401 endpoint
falling back to SSE, and a stdio server whose ``${env:PIXELLAB_API_TOKEN}`` was unset), while
``logs/mcp-stderr.log`` reached 206 MB.
"""

import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from qubit_constants import qubit_home_key, reset_qubit_home_override, set_qubit_home_override


def _cleanup_home(tmp_path):
    from tools import mcp_tool_config
    from tools.mcp_tool_lifecycle import shutdown_mcp_servers

    shutdown_mcp_servers(scope=qubit_home_key(tmp_path), timeout=2.0)
    mcp_tool_config._close_mcp_stderr_logs()


# ── unresolved required env reference ────────────────────────────────────────

@pytest.mark.parametrize("config, ref", [
    ({"command": "node", "args": ["server.js"], "env": {"TOKEN": "${env:W36_MISSING_TOKEN}"}},
     "env:W36_MISSING_TOKEN"),
    ({"url": "https://example.test/mcp", "headers": {"Authorization": "Bearer ${env:W36_MISSING_TOKEN}"}},
     "env:W36_MISSING_TOKEN"),
    ({"command": "${env:W36_MISSING_CMD}"}, "env:W36_MISSING_CMD"),
])
def test_unresolved_env_ref_fails_closed(config, ref):
    from tools import mcp_tool_config

    with pytest.raises(ValueError) as caught:
        mcp_tool_config._require_rendered_remote("pixellab", config)
    assert getattr(caught.value, "failure_class", None) == "missing_credentials"
    assert ref in str(caught.value)


def test_missing_env_is_recorded_unavailable_and_not_retried(monkeypatch, tmp_path):
    token = set_qubit_home_override(tmp_path)
    try:
        from tools import mcp_tool, mcp_tool_config, mcp_tool_discovery

        mcp_tool._server_unavailable.clear()
        mcp_tool._server_connect_retry_after.clear()
        mcp_tool._server_connect_failures.clear()

        config = {"command": "node", "env": {"TOKEN": "${env:W36_MISSING_TOKEN}"}}
        try:
            mcp_tool_config._require_rendered_remote("pixellab", config)
        except ValueError as exc:
            mcp_tool_discovery._note_connect_failure("pixellab", exc)

        from tools.mcp_tool_scope import _server_key
        scope = mcp_tool._mcp_registry_scope()
        key = _server_key("pixellab")
        assert "W36_MISSING_TOKEN" in mcp_tool_discovery._server_unavailable_reason(key)
        # Discovery must not select it again in this process.
        assert mcp_tool_discovery._select_new_servers({"pixellab": config}) == {}
        assert mcp_tool_discovery._awaiting_connect({"pixellab"}, scope) == []
        status = {e["name"]: e for e in mcp_tool_discovery.get_mcp_status({"pixellab": config})}
        assert status["pixellab"]["status"] == "unavailable"
        assert "W36_MISSING_TOKEN" in status["pixellab"]["error"]
    finally:
        from tools import mcp_tool
        mcp_tool._server_unavailable.clear()
        reset_qubit_home_override(token)


# ── auth rejection: bounded probes, then terminal ────────────────────────────

@pytest.mark.no_isolate
def test_auth_rejection_self_probes_are_bounded_then_terminal(monkeypatch, tmp_path):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    httpx = pytest.importorskip("httpx")

    from tools import mcp_tool, mcp_tool_config
    from tools.mcp_tool import MCPServerTask

    monkeypatch.setattr(mcp_tool, "_PARKED_RETRY_INTERVAL", 0.02)
    monkeypatch.setattr(mcp_tool, "_AUTH_PARK_MAX_SELF_PROBES", 2)
    monkeypatch.setattr(mcp_tool, "_AUTH_PARK_MAX_BACKOFF_SEC", 0.1)
    monkeypatch.setattr(mcp_tool_config, "_load_mcp_config",
                        lambda: {"authy": {"command": "x"}})
    real_sleep = asyncio.sleep

    def _auth_error():
        request = httpx.Request("POST", "https://mcp.example.test/mcp")
        response = httpx.Response(401, request=request)
        return httpx.HTTPStatusError("401", request=request, response=response)

    state = {"calls": 0, "parked": 0}
    mcp_tool._server_unavailable.clear()

    class _Task(MCPServerTask):
        def _is_http(self):
            return False

        def _deregister_tools(self):
            state["parked"] += 1
            self._registered_tool_names = []

        async def _run_stdio(self, config):
            state["calls"] += 1
            raise _auth_error()

    async def _scenario():
        task = _Task("authy")
        run_task = asyncio.ensure_future(task.run({"command": "x"}))
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                await real_sleep(0.01)
                if "authy" in mcp_tool._server_unavailable:
                    break
            assert not run_task.done(), "terminal server must stay parked, not exit"
            # one initial connect + the bounded number of self-probes, no more
            assert state["calls"] == 1 + 2, state
            assert "authentication rejected" in mcp_tool._server_unavailable["authy"]
            assert task._auth_park_terminal is True
            calls_at_terminal = state["calls"]

            # A quiet client cannot trigger another unattended probe.
            await real_sleep(0.3)
            assert state["calls"] == calls_at_terminal

            # An explicit reconnect is still allowed and starts a fresh bounded budget.
            task._reconnect_event.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                await real_sleep(0.01)
                if state["calls"] > calls_at_terminal:
                    return
            raise AssertionError("explicit reconnect did not attempt a fresh connection")
        finally:
            task._shutdown_event.set()
            task._reconnect_event.set()
            try:
                await asyncio.wait_for(run_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                run_task.cancel()

    try:
        asyncio.run(_scenario())
    finally:
        mcp_tool._server_unavailable.clear()


def test_discovery_does_not_nudge_a_non_oauth_auth_park(monkeypatch):
    """Discovery runs on every agent build. Waking a non-OAuth 401 park from there would
    bypass the parked task's bounded self-probe budget and retry the endpoint forever."""
    from tools import mcp_tool, mcp_tool_discovery, mcp_tool_loop
    from tools.mcp_tool import MCPServerTask

    nudged = []
    monkeypatch.setattr(mcp_tool_loop, "_signal_reconnect", lambda srv: nudged.append(srv.name))
    mcp_tool._servers.clear()
    mcp_tool._server_scope_keys.clear()
    mcp_tool._server_unavailable.clear()

    task = MCPServerTask("authy")
    task._was_parked = True
    task._park_reason = "after permanent initial failure"
    task._auth_park_rejection = True
    monkeypatch.setitem(mcp_tool._servers, "authy", task)
    monkeypatch.setitem(mcp_tool._server_scope_keys, "authy", None)
    try:
        mcp_tool_discovery._select_new_servers({"authy": {"command": "x"}})
        assert nudged == []

        # A transient park still gets the normal discovery nudge.
        task._auth_park_rejection = False
        mcp_tool_discovery._select_new_servers({"authy": {"command": "x"}})
        assert nudged == ["authy"]
    finally:
        mcp_tool._servers.clear()
        mcp_tool._server_scope_keys.clear()


# ── the REAL SDK shape: a ConnectionError whose cause holds the HTTP 401 ──────
#
# mcp 2.0 raises the transport failure as a plain ConnectionError and keeps the
# httpx2.HTTPStatusError (with the 401 response) on ``__cause__``; wave 36 inspected only the
# outermost ``response``, so a real 401 took the generic path (300 s self-probes forever).

def _http_status_error(status: int):
    httpx = pytest.importorskip("httpx")
    request = httpx.Request("POST", "https://mcp.example.test/mcp")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"Client error '{status}'", request=request, response=response)


def _sdk_shaped_auth_error(status: int = 401, link: str = "cause"):
    """The measured production shape: ConnectionError wrapping HTTPStatusError."""
    inner = _http_status_error(status)
    outer = ConnectionError(
        "MCP server 'wrapped': both Streamable HTTP and SSE transports failed "
        f"(Streamable HTTP: Server returned an error response; SSE: {inner})")
    if link == "cause":
        outer.__cause__ = inner
    else:
        outer.__context__ = inner
    return outer


@pytest.mark.parametrize("link", ["cause", "context"])
@pytest.mark.parametrize("status", [401, 403])
def test_auth_rejection_walks_wrapped_sdk_causes(link, status):
    from tools.mcp_tool_errors import _classify_mcp_failure, _is_auth_rejection

    exc = _sdk_shaped_auth_error(status, link)
    assert _is_auth_rejection(exc) is True
    assert _classify_mcp_failure(exc) == "permanent"


def test_auth_rejection_finds_wrapped_error_inside_group():
    from tools.mcp_tool_errors import _classify_mcp_failure, _is_auth_rejection

    group = BaseExceptionGroup(
        "unhandled errors in a TaskGroup",
        [ConnectionError("connection refused"), _sdk_shaped_auth_error(401)])
    assert _is_auth_rejection(group) is True
    assert _classify_mcp_failure(group) == "permanent"


@pytest.mark.parametrize("message", [
    "HTTP 401 Unauthorized",
    "HTTP/1.1 403 Forbidden",
    "status 401",
    "status_code: 403",
    "401 Client Error for url 'https://host/mcp'",
    "Unauthorized (401)",
])
def test_auth_rejection_recognizes_status_in_message(message):
    from tools.mcp_tool_errors import _classify_mcp_failure, _is_auth_rejection

    exc = ConnectionError(message)
    assert _is_auth_rejection(exc) is True
    assert _classify_mcp_failure(exc) == "permanent"


@pytest.mark.parametrize("message", [
    "connection refused",
    "Connection reset by peer",
    "timed out",
    "Name or service not known",
    "failed to connect to 127.0.0.1:4010",
    "HTTP 500 Server Error",
    "server returned status 404",
])
def test_generic_connection_failures_stay_transient(message):
    """A URL/port/body that merely contains 401/403 must not look like an auth rejection."""
    from tools.mcp_tool_errors import _classify_mcp_failure, _is_auth_rejection

    exc = ConnectionError(message)
    assert _is_auth_rejection(exc) is False
    assert _classify_mcp_failure(exc) == "transient"


@pytest.mark.no_isolate
def test_wrapped_auth_rejection_engages_bounded_budget_then_terminal(monkeypatch, tmp_path):
    """The SDK-wrapped 401 must park on the AUTH schedule: 5 probes, growing backoff capped at
    ``_AUTH_PARK_MAX_BACKOFF_SEC``, then terminal ``unavailable``; discovery must not nudge it and
    an explicit reconnect must start a fresh budget."""
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    from tools import mcp_tool, mcp_tool_config, mcp_tool_discovery, mcp_tool_loop
    from tools.mcp_tool import MCPServerTask

    real_sleep = asyncio.sleep
    monkeypatch.setattr(mcp_tool, "_PARKED_RETRY_INTERVAL", 0.02)
    monkeypatch.setattr(mcp_tool, "_AUTH_PARK_MAX_SELF_PROBES", 5)
    monkeypatch.setattr(mcp_tool, "_AUTH_PARK_MAX_BACKOFF_SEC", 0.1)
    monkeypatch.setattr(mcp_tool_config, "_load_mcp_config",
                        lambda: {"wrapped": {"command": "x"}})
    mcp_tool._server_unavailable.clear()
    mcp_tool._servers.clear()
    mcp_tool._server_scope_keys.clear()

    class _Task(MCPServerTask):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.calls = 0
            self.wait_timeouts = []

        def _is_http(self):
            return False

        def _deregister_tools(self):
            self._registered_tool_names = []

        async def _run_stdio(self, config):
            self.calls += 1
            raise _sdk_shaped_auth_error()

        async def _wait_for_reconnect_or_shutdown(self, timeout=None):
            self.wait_timeouts.append(timeout)
            if self._auth_park_terminal:
                # Stay parked (as production does) until an explicit reconnect/shutdown.
                while not (self._reconnect_event.is_set() or self._shutdown_event.is_set()):
                    await real_sleep(0.005)
                if self._shutdown_event.is_set():
                    return "shutdown"
                self._reconnect_event.clear()
                return "reconnect"
            return "self-probe"

    task = _Task("wrapped")
    nudged = []
    monkeypatch.setattr(mcp_tool_loop, "_signal_reconnect", lambda srv: nudged.append(srv.name))

    async def _scenario():
        run_task = asyncio.ensure_future(task.run({"command": "x"}))
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not task._auth_park_terminal:
                await real_sleep(0.01)
            assert task._auth_park_terminal, "wrapped 401 never reached the terminal latch"
            # one initial connect + exactly the bounded five self-probes
            assert task.calls == 1 + 5, task.calls
            assert task._auth_self_probes == 5
            assert "authentication rejected" in mcp_tool._server_unavailable["wrapped"]
            # 0.02, then 0.02/0.04/0.08 doubling, capped at 0.1 — never the generic 0.02 forever
            assert task.wait_timeouts[:6] == [0.02, 0.02, 0.04, 0.08, 0.1, 0.1], task.wait_timeouts
            assert task.wait_timeouts[6] is None

            # No unattended probe once terminal, even as time passes.
            calls_at_terminal = task.calls
            await real_sleep(0.15)
            assert task.calls == calls_at_terminal

            # Discovery must not wake a terminal auth park (budget bypass).
            mcp_tool._servers["wrapped"] = task
            mcp_tool._server_scope_keys["wrapped"] = None
            try:
                mcp_tool_discovery._select_new_servers({"wrapped": {"command": "x"}})
                assert nudged == [], nudged
            finally:
                mcp_tool._servers.pop("wrapped", None)
                mcp_tool._server_scope_keys.pop("wrapped", None)

            # An explicit reconnect (/reload-mcp, `qubit mcp login`) still revives and resets.
            task._reconnect_event.set()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and task.calls == calls_at_terminal:
                await real_sleep(0.01)
            assert task.calls > calls_at_terminal, "explicit reconnect did not revive the server"
            # ...and the revived run starts a FRESH bounded budget (first wait 0.02 again).
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and len(task.wait_timeouts) < 8:
                await real_sleep(0.01)
            assert task.wait_timeouts[7] == 0.02, task.wait_timeouts
        finally:
            task._shutdown_event.set()
            task._reconnect_event.set()
            try:
                await asyncio.wait_for(run_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                run_task.cancel()

    try:
        asyncio.run(_scenario())
    finally:
        mcp_tool._server_unavailable.clear()
        mcp_tool._servers.clear()
        mcp_tool._server_scope_keys.clear()


@pytest.mark.no_isolate
def test_loopback_http_401_is_classified_as_auth_rejection(monkeypatch, tmp_path):
    """End-to-end against a real loopback 401: the fork's transport wraps the SDK failure in
    ``ConnectionError`` (cause = httpx2.HTTPStatusError), which must classify as auth-rejected."""
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    pytest.importorskip("mcp.client.streamable_http")

    class _Handler(BaseHTTPRequestHandler):
        def _reject(self):
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"unauthorized"}')

        do_POST = _reject
        do_GET = _reject

        def do_HEAD(self):
            self.send_response(401)
            self.end_headers()

        def log_message(self, *args):  # keep pytest output clean
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        from tools import mcp_tool
        from tools.mcp_tool import MCPServerTask
        from tools.mcp_tool_errors import _classify_mcp_failure, _is_auth_rejection

        task = MCPServerTask("loopback401")
        url = f"http://127.0.0.1:{server.server_address[1]}/mcp"

        async def _run():
            with pytest.raises(ConnectionError) as caught:
                await task._run_http({"url": url})
            return caught.value

        async def _connect():
            return await asyncio.wait_for(_run(), timeout=30)

        exc = asyncio.run(_connect())
        assert _is_auth_rejection(exc) is True, (type(exc).__name__, str(exc))
        assert _classify_mcp_failure(exc) == "permanent"
    finally:
        server.shutdown()
        server.server_close()
        from tools import mcp_tool
        mcp_tool._server_unavailable.clear()


# ── child stderr log cap ─────────────────────────────────────────────────────

def test_stderr_log_is_size_bounded(monkeypatch, tmp_path):
    token = set_qubit_home_override(tmp_path)
    try:
        from tools import mcp_tool_config

        mcp_tool_config._close_mcp_stderr_logs()
        monkeypatch.setattr(mcp_tool_config, "_MCP_STDERR_LOG_MAX_BYTES", 2048)
        path = tmp_path / "logs" / "mcp-stderr.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10_000)  # pre-existing oversized log is truncated on open

        fh = mcp_tool_config._get_mcp_stderr_log()
        try:
            mcp_tool_config._write_stderr_log_header("probe")
            for _ in range(20):
                fh.write("y" * 500 + "\n")
            fh.flush()
        finally:
            mcp_tool_config._close_mcp_stderr_logs()

        assert path.exists()
        assert path.stat().st_size <= 2048, path.stat().st_size
    finally:
        reset_qubit_home_override(token)
        _cleanup_home(tmp_path)
