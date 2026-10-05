"""Brokered Qubit MCP client transport (stdio + streamable HTTP).

This is the ONE real transport behind :func:`voicebridge.mcp_servers.invoke`.
It is intentionally small and fail-closed:

* the **capability/allowlist gate** lives here (:func:`check_invocable`): a
  server must be enabled, have a runnable transport, and declare the requested
  tool in its explicit ``capabilities`` allowlist; otherwise nothing is spawned
  or connected;
* the sanctioned **server-level methods** (:func:`call_method` for
  ``tools/list``, ``resources/list``, ``resources/read``, ``prompts/list``,
  ``prompts/get``) carry the lighter :func:`check_method` gate (supported
  transport + enabled + runnable): discovery and read methods do not need the
  tool allowlist, which is exactly what ``tools/list`` discovers;
* a **bounded timeout** (:data:`DEFAULT_TIMEOUT_S`, hard-capped at
  :data:`MAX_TIMEOUT_S`) is enforced for both transports;
* a stdio child runs in its own process group (``start_new_session=True``) and
  the whole group is ``SIGKILL``-ed on timeout/error, so a hung server can never
  outlive its budget (:func:`_kill_group`);
* credentials are **reference-only** in the registry (``env:`` / ``file:`` /
  ``vault:``). References are resolved process-locally and passed to the child
  environment or HTTP header only; the resolved values are **never** logged,
  returned, or audited, and are scrubbed from any tool output that echoes them
  (:func:`_scrub_obj`);
* every failure is a typed :class:`McpClientError` code.

Nothing here performs authorization or confirmation: the canonical entry
point :func:`voicebridge.mcp_servers.invoke` runs the e-stop + shared durable
confirmation gate *before* calling :func:`call_server`. This module is the
sanctioned transport, not a second policy surface.
"""
from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "qubit-mcp-client/1"

# Ported from the vendored tool-call budget (``tools/mcp_tool_common.py:41
# _DEFAULT_TOOL_TIMEOUT = 300``): the reference allows a single MCP tool call up
# to 300s. Qubit previously defaulted to 30s and hard-capped at 120s, so a
# legitimately heavy tool (e.g. graft's full-repo ``graft_check_freshness``,
# ~30s on a 4k-file graph) was killed by the client before its real result
# arrived. The timeout stays bounded (300s ceiling, still killable) and config
# ``mcp.timeout_s`` / an explicit per-call ``timeout_s`` can lower it.
DEFAULT_TIMEOUT_S = 300.0
MAX_TIMEOUT_S = 300.0
MAX_OUTPUT_CHARS = 200_000
MAX_CAPABILITIES = 64
MAX_CAP_CHARS = 128
MAX_ARGS_BYTES = 64 * 1024

#: Typed failure codes (never a bare exception string on a public surface).
CONNECT_CODE = "MCP_CONNECT_FAILED"
TIMEOUT_CODE = "MCP_TIMEOUT"
PROTOCOL_CODE = "MCP_PROTOCOL_ERROR"
TOOL_ERROR_CODE = "MCP_TOOL_ERROR"
TRANSPORT_CODE = "MCP_TRANSPORT_UNSUPPORTED"
INCOMPLETE_CODE = "MCP_SERVER_INCOMPLETE"
DISABLED_CODE = "MCP_SERVER_DISABLED"
NOT_ALLOWED_CODE = "MCP_TOOL_NOT_ALLOWED"
SECRET_CODE = "MCP_SECRET_UNRESOLVED"
METHOD_CODE = "MCP_METHOD_UNSUPPORTED"

#: The sanctioned non-tool JSON-RPC methods. ``tools/call`` is handled
#: separately by :func:`call_server` because it is gated by the explicit tool
#: allowlist; these are server-level discovery/read methods.
DISCOVERY_METHOD = "tools/list"
RESOURCE_LIST_METHOD = "resources/list"
RESOURCE_READ_METHOD = "resources/read"
RESOURCE_TEMPLATES_METHOD = "resources/templates/list"
PROMPT_LIST_METHOD = "prompts/list"
PROMPT_GET_METHOD = "prompts/get"
SUPPORTED_METHODS = (DISCOVERY_METHOD, RESOURCE_LIST_METHOD,
                     RESOURCE_READ_METHOD, RESOURCE_TEMPLATES_METHOD,
                     PROMPT_LIST_METHOD, PROMPT_GET_METHOD)
#: Methods whose result carries a named list under this key.
_LIST_KEYS = {DISCOVERY_METHOD: "tools",
              RESOURCE_LIST_METHOD: "resources",
              RESOURCE_TEMPLATES_METHOD: "resourceTemplates",
              PROMPT_LIST_METHOD: "prompts"}
MAX_ITEMS = 256

_SUPPORTED_TRANSPORTS = ("stdio", "http")
_TOOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")


class McpClientError(Exception):
    """Typed MCP client transport failure (``code`` / ``detail``)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Redaction / audit
# ---------------------------------------------------------------------------
def _redact(text: Any) -> str:
    try:
        from .redaction import redact_text

        return redact_text(str(text))
    except Exception:  # noqa: BLE001 - redaction must never raise
        return str(text)


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    """Redacted audit event. Secret values are never passed in."""
    from .logging_setup import log_event

    safe = {k: _redact(v)[:160] for k, v in detail.items()}
    try:
        log_event(f"mcp.client.{op}", actor=actor, **safe)
    except Exception:  # noqa: BLE001 - audit is observability, never a gate
        pass


# ---------------------------------------------------------------------------
# Capability / allowlist gate
# ---------------------------------------------------------------------------
def allowed_tools(record: Dict[str, Any]) -> List[str]:
    """The server's explicit tool allowlist (``capabilities``; empty = deny)."""
    raw = record.get("capabilities")
    if raw is None:
        raw = record.get("allowed_tools")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[str] = []
    for item in raw:
        clean = str(item or "").strip()
        if clean:
            out.append(clean[:MAX_CAP_CHARS])
    return out[:MAX_CAPABILITIES]


def server_ready(record: Dict[str, Any]) -> bool:
    """True when the recorded transport has everything needed to run."""
    transport = str(record.get("transport") or "").strip().lower()
    if transport == "stdio":
        return bool(str(record.get("command") or "").strip())
    if transport == "http":
        return bool(str(record.get("url") or "").strip())
    return False


def check_ready(record: Dict[str, Any]) -> None:
    """The transport/enablement gate shared by every client call (fail closed).

    Order: supported transport -> enabled -> runnable config. This is a pure
    policy check; nothing is spawned or connected.
    """
    transport = str(record.get("transport") or "").strip().lower()
    if transport not in _SUPPORTED_TRANSPORTS:
        raise McpClientError(
            TRANSPORT_CODE,
            f"transport {transport!r} has no sanctioned client invocation path")
    if not record.get("enabled"):
        raise McpClientError(
            DISABLED_CODE, f"MCP server {record.get('name')!r} is disabled")
    if not server_ready(record):
        needed = "command" if transport == "stdio" else "url"
        raise McpClientError(
            INCOMPLETE_CODE,
            f"MCP server {record.get('name')!r} is missing its {needed}")


def check_method(record: Dict[str, Any], method: str) -> None:
    """Gate a sanctioned non-tool method: known method + ready server.

    ``tools/list`` discovery and the resources/prompts reads are server-level
    operations, so they carry no tool allowlist. They still require an enabled,
    runnable, supported-transport server; unknown methods are refused.
    """
    name = str(method or "").strip()
    if name not in SUPPORTED_METHODS:
        raise McpClientError(
            METHOD_CODE,
            f"MCP method {name!r} is not a sanctioned client method")
    check_ready(record)


def check_invocable(record: Dict[str, Any], tool: str) -> None:
    """The shared capability/allowlist gate; raises typed errors, fail closed.

    Order: supported transport -> enabled -> runnable config -> a non-empty
    declared allowlist -> the requested tool is on it. This is a pure policy
    check; nothing is spawned or connected.
    """
    check_ready(record)
    allow = allowed_tools(record)
    if not allow:
        raise McpClientError(
            NOT_ALLOWED_CODE,
            "no tool allowlist is declared for this server; add an explicit "
            "`capabilities` list before it can be invoked")
    clean_tool = str(tool or "").strip()
    if not clean_tool or not _TOOL_RE.match(clean_tool):
        raise McpClientError(NOT_ALLOWED_CODE, "a valid tool name is required")
    if clean_tool not in allow:
        raise McpClientError(
            NOT_ALLOWED_CODE,
            f"tool {clean_tool!r} is not in the server tool allowlist")


def is_invocable(record: Dict[str, Any]) -> bool:
    """True when the record passes the gate for *some* allowlisted tool."""
    transport = str(record.get("transport") or "").strip().lower()
    if transport not in _SUPPORTED_TRANSPORTS:
        return False
    if not record.get("enabled") or not server_ready(record):
        return False
    return bool(allowed_tools(record))



# ---------------------------------------------------------------------------
# Reference resolution (values never leave this process)
# ---------------------------------------------------------------------------
def _resolve_ref(ref: str) -> Optional[str]:
    """Resolve one ``env:``/``file:``/``vault:``/``oauth:`` reference.

    ``vault:`` has no Qubit-side resolver, so it fails closed rather than
    spawning a server without its credential. ``oauth:<server>`` reads the
    dashboard-obtained access token from the vendored per-home
    ``mcp-tokens/`` store (read-only, unexpired only — refresh happens on
    the next dashboard probe/login, never here).
    """
    ref = str(ref or "").strip()
    if ref.startswith("oauth:"):
        return _resolve_oauth_token(ref[len("oauth:"):].strip())
    if ref.startswith("env:"):
        return os.environ.get(ref[len("env:"):], "")
    if ref.startswith("file:"):
        try:
            return Path(os.path.expanduser(ref[len("file:"):])).read_text(
                encoding="utf-8").strip()
        except OSError:
            return ""
    return None


def _resolve_oauth_token(server_name: str) -> Optional[str]:
    """Read a dashboard-OAuth access token for *server_name* (``None`` = none).

    Reads the vendored ``QUBIT_HOME/mcp-tokens/<server>.json`` file written by
    the dashboard OAuth login — the same store the vendored probe reads.
    Returns the token only when present and unexpired (30 s leeway); expired
    or absent fails closed so the caller reports an honest auth error instead
    of connecting with a dead bearer token. No refresh is attempted here:
    refresh happens on the next dashboard probe/login.
    """
    import json as _json
    import time as _time
    name = str(server_name or "").strip()
    if not name:
        return None
    try:
        from .qubit_runtime import default_home, ensure_path
        ensure_path()
        token_dir = Path(str(default_home())) / "mcp-tokens"
        safe = "".join(c if (c.isalnum() or c in "-_.") else "_"
                       for c in name)[:64]
        data = _json.loads((token_dir / f"{safe}.json").read_text(
            encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    token = str(data.get("access_token") or "")
    if not token:
        return None
    try:
        expires_at = float(data.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0.0
    if expires_at and expires_at <= _time.time() + 30:
        return None
    return token


def resolve_env(env_refs: Any) -> Tuple[Dict[str, str], List[str]]:
    """Resolve env references to concrete values + the secret list to scrub.

    Raises :class:`McpClientError` with :data:`SECRET_CODE` for an unresolvable
    reference so the client never spawns a server with a missing credential.
    """
    overrides: Dict[str, str] = {}
    secrets: List[str] = []
    if not isinstance(env_refs, dict):
        return overrides, secrets
    for key, ref in env_refs.items():
        name = str(key or "").strip()
        if not name:
            continue
        if not isinstance(ref, str):
            raise McpClientError(
                SECRET_CODE, f"env {name!r} must be a reference, not a value")
        value = _resolve_ref(ref)
        if value is None:
            raise McpClientError(
                SECRET_CODE,
                f"env {name!r} cannot be resolved (only env:/file:/oauth: "
                "references have a resolver); refusing to start the server")
        overrides[name] = value
        if value:
            secrets.append(value)
    return overrides, secrets


def _scrub(text: Any, secrets: List[str]) -> str:
    out = str(text)
    for secret in secrets:
        if secret:
            out = out.replace(secret, "[REDACTED]")
    return out


def _scrub_obj(obj: Any, secrets: List[str]) -> Any:
    if isinstance(obj, str):
        return _scrub(obj, secrets)
    if isinstance(obj, list):
        return [_scrub_obj(x, secrets) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _scrub_obj(v, secrets) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
def _config_default_timeout(cfg: Any) -> float:
    """Optional ``mcp.timeout_s`` config default, clamped to the hard cap.

    Absent/invalid/non-positive config keeps :data:`DEFAULT_TIMEOUT_S`; the
    bound can never be disabled from config.
    """
    raw = None
    get = getattr(cfg, "get", None)
    if callable(get):
        try:
            raw = get("mcp", "timeout_s", default=None)
        except Exception:  # noqa: BLE001 - config is never a gate
            raw = None
    try:
        value = float(raw) if raw is not None else DEFAULT_TIMEOUT_S
    except (TypeError, ValueError):
        value = DEFAULT_TIMEOUT_S
    if value <= 0:
        value = DEFAULT_TIMEOUT_S
    return max(0.1, min(value, MAX_TIMEOUT_S))


def _bounded_timeout(timeout_s: Optional[float],
                     default: Optional[float] = None) -> float:
    base = DEFAULT_TIMEOUT_S if default is None else default
    try:
        t = float(timeout_s) if timeout_s is not None else float(base)
    except (TypeError, ValueError):
        t = float(base)
    return max(0.1, min(t, MAX_TIMEOUT_S))


def _bounded_params(params: Any) -> Dict[str, Any]:
    payload = dict(params) if isinstance(params, dict) else {}
    try:
        blob = json.dumps(payload, default=repr)
    except (TypeError, ValueError):
        raise McpClientError(PROTOCOL_CODE, "arguments are not serializable") \
            from None
    if len(blob.encode("utf-8")) > MAX_ARGS_BYTES:
        raise McpClientError(PROTOCOL_CODE,
                             f"arguments exceed {MAX_ARGS_BYTES} bytes")
    return payload


def _dispatch(cfg: Any, record: Dict[str, Any], method: str,
              params: Dict[str, Any],
              timeout_s: float) -> Tuple[Any, List[str]]:
    """Run one JSON-RPC method over the recorded transport (typed failures)."""
    transport = str(record.get("transport")).strip().lower()
    if transport == "stdio":
        return _guard(_call_stdio, cfg, record, method, params, timeout_s)
    if transport == "http":
        return _guard(_call_http, cfg, record, method, params, timeout_s)
    # pragma: no cover - the gate already refused this transport
    raise McpClientError(TRANSPORT_CODE, f"unsupported transport {transport!r}")


def call_server(cfg: Any, record: Dict[str, Any], tool: str, *,
                args: Optional[Dict[str, Any]] = None,
                timeout_s: Optional[float] = None,
                requested_by: str = "cli:user") -> Dict[str, Any]:
    """Run one brokered MCP tool call over the recorded transport.

    The caller (:func:`voicebridge.mcp_servers.invoke`) has already run the
    e-stop + confirmation gate. This function adds the capability/allowlist
    gate, enforces the timeout, kills the stdio process group on
    timeout/error, and returns a redacted result. All failures are typed
    :class:`McpClientError`.
    """
    check_invocable(record, tool)
    payload = _bounded_params(args)
    t = _bounded_timeout(timeout_s, _config_default_timeout(cfg))
    transport = str(record.get("transport")).strip().lower()
    started = time.perf_counter()
    result, secrets = _dispatch(cfg, record, "tools/call",
                                {"name": str(tool).strip(), "arguments": payload}, t)
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    out = _normalize(record, tool, transport, result, secrets, duration_ms)
    _audit("invoke", requested_by,
           {"server": out["server"], "tool": tool, "transport": transport,
            "state": out["state"]})
    return out


def call_method(cfg: Any, record: Dict[str, Any], method: str, *,
                params: Optional[Dict[str, Any]] = None,
                timeout_s: Optional[float] = None,
                requested_by: str = "cli:user") -> Dict[str, Any]:
    """Run one sanctioned non-tool MCP JSON-RPC method over the transport.

    Sanctioned methods are :data:`SUPPORTED_METHODS`: ``tools/list`` discovery
    and the ``resources``/``prompts`` reads. The caller
    (:mod:`voicebridge.mcp_servers`) has already run the e-stop + confirmation
    gate; this function adds the method gate (:func:`check_method`: supported
    transport + enabled + runnable), enforces the timeout, and returns a
    redacted result. All failures are typed :class:`McpClientError` — no
    fabricated result.
    """
    clean = str(method or "").strip()
    check_method(record, clean)
    payload = _bounded_params(params)
    t = _bounded_timeout(timeout_s, _config_default_timeout(cfg))
    transport = str(record.get("transport")).strip().lower()
    started = time.perf_counter()
    result, secrets = _dispatch(cfg, record, clean, payload, t)
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    out = _normalize_method(record, clean, transport, result, secrets,
                            duration_ms)
    _audit("method", requested_by,
           {"server": out["server"], "method": clean, "transport": transport,
            "state": out["state"]})
    return out


# ---------------------------------------------------------------------------
# Sanctioned method wrappers (discovery + resources + prompts)
# ---------------------------------------------------------------------------
def discover_tools(cfg: Any, record: Dict[str, Any], *,
                   timeout_s: Optional[float] = None,
                   requested_by: str = "cli:user") -> Dict[str, Any]:
    """Real ``tools/list`` discovery through the brokered transport."""
    return call_method(cfg, record, DISCOVERY_METHOD, params={},
                       timeout_s=timeout_s, requested_by=requested_by)


def list_resources(cfg: Any, record: Dict[str, Any], *,
                   timeout_s: Optional[float] = None,
                   requested_by: str = "cli:user") -> Dict[str, Any]:
    """Real ``resources/list`` through the brokered transport."""
    return call_method(cfg, record, RESOURCE_LIST_METHOD, params={},
                       timeout_s=timeout_s, requested_by=requested_by)


def read_resource(cfg: Any, record: Dict[str, Any], uri: str, *,
                  timeout_s: Optional[float] = None,
                  requested_by: str = "cli:user") -> Dict[str, Any]:
    """Real ``resources/read`` through the brokered transport."""
    clean = str(uri or "").strip()
    if not clean:
        raise McpClientError(PROTOCOL_CODE, "a resource uri is required")
    return call_method(cfg, record, RESOURCE_READ_METHOD, params={"uri": clean},
                       timeout_s=timeout_s, requested_by=requested_by)


def list_prompts(cfg: Any, record: Dict[str, Any], *,
                 timeout_s: Optional[float] = None,
                 requested_by: str = "cli:user") -> Dict[str, Any]:
    """Real ``prompts/list`` through the brokered transport."""
    return call_method(cfg, record, PROMPT_LIST_METHOD, params={},
                       timeout_s=timeout_s, requested_by=requested_by)


def list_resource_templates(cfg: Any, record: Dict[str, Any], *,
                            timeout_s: Optional[float] = None,
                            requested_by: str = "cli:user") -> Dict[str, Any]:
    """Real ``resources/templates/list`` through the brokered transport."""
    return call_method(cfg, record, RESOURCE_TEMPLATES_METHOD, params={},
                       timeout_s=timeout_s, requested_by=requested_by)


def get_prompt(cfg: Any, record: Dict[str, Any], name: str, *,
               arguments: Optional[Dict[str, Any]] = None,
               timeout_s: Optional[float] = None,
               requested_by: str = "cli:user") -> Dict[str, Any]:
    """Real ``prompts/get`` through the brokered transport."""
    clean = str(name or "").strip()
    if not clean:
        raise McpClientError(PROTOCOL_CODE, "a prompt name is required")
    params: Dict[str, Any] = {"name": clean}
    if isinstance(arguments, dict) and arguments:
        params["arguments"] = arguments
    return call_method(cfg, record, PROMPT_GET_METHOD, params=params,
                       timeout_s=timeout_s, requested_by=requested_by)


def _guard(fn: Any, *args: Any) -> Tuple[Any, List[str]]:
    """Run a transport with every non-typed failure converted to a typed code."""
    try:
        return fn(*args)
    except McpClientError:
        raise
    except Exception as exc:  # noqa: BLE001 - transport boundary, fail typed
        raise McpClientError(
            PROTOCOL_CODE,
            f"MCP transport failed: {_redact(exc)[:200]}") from exc


# ---------------------------------------------------------------------------
# stdio transport
# ---------------------------------------------------------------------------
def _pump(stream: Any, q: "queue.Queue[Optional[str]]") -> None:
    try:
        for line in stream:
            q.put(line)
    except Exception:  # noqa: BLE001 - a dead pipe surfaces as EOF
        pass
    finally:
        q.put(None)


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass


def _stop(proc: subprocess.Popen, *, graceful: bool = False) -> None:
    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except Exception:  # noqa: BLE001
        pass
    if proc.poll() is None and graceful:
        try:
            proc.wait(timeout=1.0)
        except Exception:  # noqa: BLE001
            pass
    if proc.poll() is None:
        _kill_group(proc)
        try:
            proc.wait(timeout=2.0)
        except Exception:  # noqa: BLE001
            pass
    for stream in (proc.stdout, proc.stderr):
        try:
            if stream is not None:
                stream.close()
        except Exception:  # noqa: BLE001
            pass


def _send(proc: subprocess.Popen, message: Dict[str, Any]) -> None:
    try:
        if proc.stdin is None:
            raise OSError("stdin is closed")
        proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        proc.stdin.flush()
    except (OSError, ValueError) as exc:
        raise McpClientError(
            CONNECT_CODE,
            f"write to MCP stdio server failed: {_redact(exc)}") from exc


def _recv(q: "queue.Queue[Optional[str]]", proc: subprocess.Popen,
          want_id: int, deadline: float, timeout_s: float) -> Any:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise McpClientError(
                TIMEOUT_CODE,
                f"MCP stdio call timed out after {timeout_s:g}s")
        try:
            line = q.get(timeout=remaining)
        except queue.Empty:
            raise McpClientError(
                TIMEOUT_CODE,
                f"MCP stdio call timed out after {timeout_s:g}s") from None
        if line is None:
            rc = proc.poll()
            raise McpClientError(
                CONNECT_CODE,
                "MCP stdio server closed the connection before responding "
                f"(exit {rc})")
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue  # non-JSON server log line
        if not isinstance(message, dict) or message.get("id") != want_id:
            continue  # notification or unrelated id
        if "error" in message:
            err = message.get("error")
            detail = err.get("message") if isinstance(err, dict) else err
            raise McpClientError(
                PROTOCOL_CODE, f"MCP server error: {_redact(detail)[:300]}")
        return message.get("result")


def _call_stdio(cfg: Any, record: Dict[str, Any], method: str,
                params: Dict[str, Any], timeout_s: float) -> Tuple[Any, List[str]]:
    argv = [str(record.get("command"))] + \
           [str(a) for a in (record.get("args") or [])]
    overrides, secrets = resolve_env(record.get("env") or {})
    child_env = dict(os.environ)
    child_env.update(overrides)
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=child_env,
            start_new_session=True)
    except OSError as exc:
        raise McpClientError(
            CONNECT_CODE,
            f"MCP stdio server failed to start: {_redact(exc)}") from exc

    deadline = time.monotonic() + timeout_s
    q: "queue.Queue[Optional[str]]" = queue.Queue()
    threading.Thread(target=_pump, args=(proc.stdout, q), daemon=True).start()
    ok = False
    try:
        _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2024-11-05",
                                "capabilities": {},
                                "clientInfo": {"name": "qubit",
                                               "version": "1.0"}}})
        _recv(q, proc, 1, deadline, timeout_s)
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized",
                     "params": {}})
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": method,
                     "params": params})
        result = _recv(q, proc, 2, deadline, timeout_s)
        ok = True
        return result, secrets
    finally:
        _stop(proc, graceful=ok)


# ---------------------------------------------------------------------------
# streamable HTTP transport
# ---------------------------------------------------------------------------
def _parse_body(body: str, content_type: str, want_id: int) -> Any:
    """Parse a JSON or SSE JSON-RPC response body, matching ``want_id``."""
    body = body or ""
    if "text/event-stream" in (content_type or "").lower() \
            or body.lstrip().startswith(("event:", "data:")):
        candidates: List[str] = []
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                candidates.append(line[len("data:"):].strip())
        for chunk in reversed(candidates):
            if not chunk or chunk == "[DONE]":
                continue
            try:
                message = json.loads(chunk)
            except ValueError:
                continue
            got = _select_message(message, want_id)
            if got is not _MISS:
                return got
        raise McpClientError(PROTOCOL_CODE,
                             "MCP http SSE response carried no matching frame")
    if not body.strip():
        return None
    try:
        message = json.loads(body)
    except ValueError as exc:
        raise McpClientError(
            PROTOCOL_CODE,
            f"MCP http response was not JSON: {_redact(exc)}") from exc
    got = _select_message(message, want_id)
    if got is _MISS:
        return None
    return got


class _Miss:
    pass


_MISS = _Miss()


def _select_message(message: Any, want_id: int) -> Any:
    if isinstance(message, list):
        for item in message:
            if isinstance(item, dict) and item.get("id") == want_id:
                return _unwrap(item)
        return _MISS
    if isinstance(message, dict):
        if message.get("id") not in (None, want_id):
            return _MISS
        return _unwrap(message)
    return _MISS


def _unwrap(message: Dict[str, Any]) -> Any:
    if "error" in message:
        err = message.get("error")
        detail = err.get("message") if isinstance(err, dict) else err
        raise McpClientError(
            PROTOCOL_CODE, f"MCP server error: {_redact(detail)[:300]}")
    return message.get("result")


def _call_http(cfg: Any, record: Dict[str, Any], method: str,
               params: Dict[str, Any], timeout_s: float) -> Tuple[Any, List[str]]:
    import urllib.error
    import urllib.request

    url = str(record.get("url"))
    overrides, secrets = resolve_env(record.get("env") or {})
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    token = overrides.get("MCP_AUTH") or overrides.get("TOKEN") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def _post(message: Dict[str, Any], session_id: str = "") -> Tuple[Any, str]:
        send_headers = dict(headers)
        if session_id:
            send_headers["Mcp-Session-Id"] = session_id
        request = urllib.request.Request(
            url, data=json.dumps(message).encode("utf-8"),
            headers=send_headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as resp:
                body = resp.read(MAX_OUTPUT_CHARS + 1).decode("utf-8", "replace")
                sid = resp.headers.get("Mcp-Session-Id", "") or ""
                ctype = resp.headers.get("Content-Type", "") or ""
        except urllib.error.HTTPError as exc:
            raise McpClientError(
                CONNECT_CODE,
                f"MCP http server returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError) or \
                    "timed out" in str(reason).lower():
                raise McpClientError(
                    TIMEOUT_CODE,
                    f"MCP http call timed out after {timeout_s:g}s") from exc
            raise McpClientError(
                CONNECT_CODE,
                f"MCP http connect failed: {_redact(reason)}") from exc
        parsed = _parse_body(body, ctype, int(message.get("id") or 0))
        return parsed, sid

    _post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
           "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                      "clientInfo": {"name": "qubit", "version": "1.0"}}})
    try:
        _post({"jsonrpc": "2.0", "method": "notifications/initialized",
               "params": {}})
    except McpClientError:
        pass
    result, _ = _post({"jsonrpc": "2.0", "id": 2, "method": method,
                       "params": params})
    return result, secrets


# ---------------------------------------------------------------------------
# Result normalization
# ---------------------------------------------------------------------------
def _extract_text(result: Any) -> str:
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif isinstance(item, str):
                    parts.append(item)
            if parts:
                return "\n".join(parts)
        for key in ("text", "output", "result"):
            if isinstance(result.get(key), str):
                return result[key]
    if isinstance(result, str):
        return result
    return json.dumps(result, default=str)


def _normalize(record: Dict[str, Any], tool: str, transport: str, result: Any,
               secrets: List[str], duration_ms: float) -> Dict[str, Any]:
    is_error = bool(result.get("isError")) if isinstance(result, dict) else False
    text = _scrub(_redact(_extract_text(result)), secrets)[:MAX_OUTPUT_CHARS]
    safe_result = _scrub_obj(result, secrets) \
        if isinstance(result, (dict, list)) else text
    return {
        "schema": SCHEMA,
        "server": record.get("name"),
        "tool": tool,
        "transport": transport,
        "state": "ERROR" if is_error else "OK",
        "connected": True,
        "invocable": True,
        "is_error": is_error,
        "result": safe_result,
        "text": text,
        "redacted": True,
        "duration_ms": duration_ms,
    }


def _item_names(result: Any, method: str) -> Optional[List[str]]:
    """The named items from a discovery/list result, or ``None`` for other methods.

    Only real names the server returned are ever emitted; a malformed payload
    yields an empty list, never a fabricated tool.
    """
    key = _LIST_KEYS.get(method)
    if key is None:
        return None
    if not isinstance(result, dict):
        return []
    items = result.get(key)
    if not isinstance(items, list):
        return []
    names: List[str] = []
    prefer_uri = method == RESOURCE_LIST_METHOD
    for item in items:
        if isinstance(item, dict):
            primary = item.get("uri") if prefer_uri else item.get("name")
            if primary is None:
                primary = item.get("uri") if not prefer_uri else item.get("name")
            name = str(primary).strip() if primary is not None else ""
        else:
            name = str(item).strip()
        if name:
            names.append(name[:MAX_CAP_CHARS])
    return names[:MAX_ITEMS]


def _normalize_method(record: Dict[str, Any], method: str, transport: str,
                      result: Any, secrets: List[str],
                      duration_ms: float) -> Dict[str, Any]:
    is_error = bool(result.get("isError")) if isinstance(result, dict) else False
    text = _scrub(_redact(_extract_text(result)), secrets)[:MAX_OUTPUT_CHARS]
    safe_result = _scrub_obj(result, secrets) \
        if isinstance(result, (dict, list)) else text
    out: Dict[str, Any] = {
        "schema": SCHEMA,
        "server": record.get("name"),
        "method": method,
        "transport": transport,
        "state": "ERROR" if is_error else "OK",
        "connected": True,
        "invocable": True,
        "is_error": is_error,
        "result": safe_result,
        "text": text,
        "redacted": True,
        "duration_ms": duration_ms,
    }
    names = _item_names(safe_result, method)
    if names is not None:
        out["names"] = names
        out["count"] = len(names)
    return out
