"""A misconfigured OAuth MCP server fails typed and never self-probes.

The shipped ``airtable`` entry points at ``https://mcp.airtable.com/mcp``, which
answers ``401`` with ``WWW-Authenticate: Bearer ... resource_metadata=...``. With
no ``auth: oauth`` the transport never builds an OAuth provider, so no retry can
ever succeed: this used to consume the bounded auth self-probe budget (and was
still nudged by discovery), which is a retry storm against a permanent
misconfiguration. It must now fail typed and terminal.
"""

import asyncio

import httpx
import pytest

from tools.mcp_tool import MCPServerTask
from tools import mcp_tool_errors as E

_AIRTABLE = "https://mcp.airtable.com/mcp"
_CHALLENGE = (
    'Bearer error="invalid_token", error_description="Missing Authorization header", '
    'resource_metadata="https://mcp.airtable.com/.well-known/oauth-protected-resource"'
)
_CONFIG = {"url": _AIRTABLE, "connect_timeout": 1, "skip_preflight": True}


class _OpaqueSdkError(Exception):
    """The SDK's opaque shape for a non-2xx it could not parse."""

    def __init__(self):
        super().__init__("Server returned an error response")
        self.error = type("E", (), {"code": -32603})()


def _rejection(**over):
    return {"status": 401, "method": "POST", "url": _AIRTABLE, "body": '{"error":"UNAUTHORIZED"}',
            "www_authenticate": _CHALLENGE, **over}


def _task(monkeypatch, rejection, error, *, auth_type=""):
    task = MCPServerTask("airtable")
    task._config = {}
    task._auth_type = auth_type
    calls = []

    async def fake_serve(self, cm, label, timeout):
        calls.append(label)
        raise error

    monkeypatch.setattr(MCPServerTask, "_serve_transport", fake_serve)
    monkeypatch.setattr(MCPServerTask, "_streamable_http_transport",
                        lambda self, *a, **k: self._http_rejection.update(rejection) or object())
    monkeypatch.setattr(MCPServerTask, "_build_oauth_auth", lambda self, *a: None)
    return task, calls


def test_oauth_challenge_detection_is_specific():
    assert E.is_oauth_challenge(401, _CHALLENGE)
    assert E.is_oauth_challenge(401, 'Bearer resource_metadata="https://x/.well-known"')
    # A non-OAuth rejection (or the wrong status) is not a provider misconfiguration.
    assert not E.is_oauth_challenge(401, 'Basic realm="private"')
    assert not E.is_oauth_challenge(403, _CHALLENGE)
    assert not E.is_oauth_challenge(None, _CHALLENGE)


def test_recorder_captures_the_oauth_challenge_header():
    sink = {}
    recorder = E._make_http_rejection_recorder(sink)
    request = httpx.Request("POST", _AIRTABLE)
    response = httpx.Response(
        401, request=request,
        headers={"content-type": "application/json", "www-authenticate": _CHALLENGE},
        content=b'{"error":"UNAUTHORIZED"}')

    asyncio.run(recorder(response))

    assert sink["status"] == 401
    assert sink["www_authenticate"] == _CHALLENGE
    assert E.is_oauth_challenge(sink["status"], sink["www_authenticate"])


def test_unconfigured_oauth_fails_typed_and_does_not_try_sse(monkeypatch):
    task, calls = _task(monkeypatch, _rejection(), _OpaqueSdkError())
    with pytest.raises(E.OAuthRequiredError) as info:
        asyncio.run(task._run_http(dict(_CONFIG)))
    message = str(info.value)
    assert "requires OAuth" in message
    assert "auth: oauth" in message
    assert "qubit mcp login airtable" in message
    # A 401 is not a Streamable-HTTP transport mismatch: no SSE fallback attempt.
    assert calls == ["HTTP"]


def test_configured_oauth_server_keeps_the_generic_path(monkeypatch):
    """``auth: oauth`` means the SDK owns the flow; never replace its error with the typed
    misconfiguration (the provider may be mid-login or refreshing)."""
    task, _calls = _task(monkeypatch, _rejection(), _OpaqueSdkError(), auth_type="oauth")
    with pytest.raises(ConnectionError) as info:
        asyncio.run(task._run_http(dict(_CONFIG)))
    assert not isinstance(info.value, E.OAuthRequiredError)


@pytest.mark.no_isolate
def test_misconfigured_oauth_parks_terminal_without_self_probing(monkeypatch, tmp_path):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    from tools import mcp_tool
    from tools import mcp_tool_config as _config
    from tools import mcp_tool_discovery as _discovery

    monkeypatch.setattr(mcp_tool, "_PARKED_RETRY_INTERVAL", 0.05)
    monkeypatch.setattr(_config, "_load_mcp_config",
                        lambda: {"airtable": {"url": _AIRTABLE, "skip_preflight": True}})

    attempts = []

    class _Task(MCPServerTask):
        def _is_http(self):
            return True

        def _deregister_tools(self):
            self._registered_tool_names = []

        async def _run_http(self, config):
            attempts.append(config["url"])
            raise E.OAuthRequiredError(
                "MCP server 'airtable' requires OAuth (HTTP 401: Bearer ...). "
                "Set mcp_servers.airtable.auth: oauth in config.yaml, then run "
                "`qubit mcp login airtable`; retrying without a provider cannot succeed.")

    async def scenario():
        task = _Task("airtable")
        run = asyncio.ensure_future(task.run(dict(_CONFIG)))
        for _ in range(200):
            await asyncio.sleep(0)
            if task._was_parked:
                break

        assert task._was_parked, "misconfigured OAuth server never parked"
        assert task._auth_park_terminal is True
        assert task._auth_park_rejection is True
        assert not run.done(), "run task exited instead of staying revivable"
        assert "requires OAuth" in str(
            mcp_tool._server_unavailable.get(task._unavailable_key) or "")

        # Several self-probe intervals pass: NOTHING retries (the storm is gone).
        await asyncio.sleep(mcp_tool._PARKED_RETRY_INTERVAL * 4)
        assert attempts == [1 * _AIRTABLE], attempts

        # Discovery also leaves a terminal park alone.
        _discovery._select_new_servers({"airtable": {"url": _AIRTABLE}})
        await asyncio.sleep(0)
        assert len(attempts) == 1

        task._shutdown_event.set()
        task._reconnect_event.set()
        await asyncio.wait_for(run, 5)

    asyncio.run(scenario())


_MEMELORD = "https://www.memelord.com/api/mcp"
_MEMELORD_CONFIG = {"url": _MEMELORD, "auth": "oauth", "connect_timeout": 1, "skip_preflight": True}


@pytest.mark.no_isolate
def test_login_required_oauth_parks_terminal_without_self_probing(monkeypatch, tmp_path):
    """A configured OAuth server with no cached token cannot log in non-interactively.

    The real live case: ``memelord`` is configured ``auth: oauth`` but the
    gateway process has no cached token and cannot open the browser flow. It
    used to re-run the OAuth setup on every bounded self-probe (a repeating
    warning in the gateway log). It must park terminal with the exact
    ``qubit mcp login <name>`` guidance instead, and only an explicit
    reconnect/login or a token-file change may revive it.
    """
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    from tools import mcp_tool
    from tools import mcp_tool_config as _config
    from tools import mcp_tool_discovery as _discovery
    from tools.mcp_oauth import OAuthNonInteractiveError

    monkeypatch.setattr(mcp_tool, "_PARKED_RETRY_INTERVAL", 0.05)
    monkeypatch.setattr(_config, "_load_mcp_config",
                        lambda: {"memelord": dict(_MEMELORD_CONFIG)})

    attempts = []

    class _Task(MCPServerTask):
        def _is_http(self):
            return True

        def _deregister_tools(self):
            self._registered_tool_names = []

        async def _run_http(self, config):
            attempts.append(config["url"])
            raise OAuthNonInteractiveError(
                "MCP OAuth for 'memelord': non-interactive environment and no cached "
                "tokens found. Run `qubit mcp login memelord` interactively first to "
                "complete initial authorization.")

    async def scenario():
        task = _Task("memelord")
        run = asyncio.ensure_future(task.run(dict(_MEMELORD_CONFIG)))
        for _ in range(200):
            await asyncio.sleep(0)
            if task._was_parked:
                break

        assert task._was_parked, "login-required OAuth server never parked"
        assert task._auth_park_terminal is True
        assert task._auth_park_rejection is True
        assert task._auth_self_probes == 0, "a terminal park must not self-probe"
        assert not run.done(), "run task exited instead of staying revivable"
        reason = str(mcp_tool._server_unavailable.get(task._unavailable_key) or "")
        assert "qubit mcp login memelord" in reason, reason

        # Several self-probe intervals pass: NOTHING retries (the storm is gone).
        await asyncio.sleep(mcp_tool._PARKED_RETRY_INTERVAL * 4)
        assert attempts == [_MEMELORD], attempts

        # Discovery leaves the terminal park alone while the token cache is unchanged.
        _discovery._select_new_servers({"memelord": dict(_MEMELORD_CONFIG)})
        await asyncio.sleep(0)
        assert attempts == [_MEMELORD], attempts

        task._shutdown_event.set()
        task._reconnect_event.set()
        await asyncio.wait_for(run, 5)

    asyncio.run(scenario())
