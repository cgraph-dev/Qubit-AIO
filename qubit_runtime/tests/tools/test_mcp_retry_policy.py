"""Bounded MCP failure handling: unresolved env refs and auth rejection are terminal for the
process, and the child-stderr log cannot grow without bound.

Measured live: two broken servers were retried every ~5 minutes forever (an HTTP 401 endpoint
falling back to SSE, and a stdio server whose ``${env:PIXELLAB_API_TOKEN}`` was unset), while
``logs/mcp-stderr.log`` reached 206 MB.
"""

import asyncio
import time

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
