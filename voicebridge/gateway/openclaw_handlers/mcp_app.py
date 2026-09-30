"""OpenClaw contractual-pin ``mcp.app.*`` family.

Qubit's canonical MCP authority is the in-process host in
:mod:`voicebridge.plugin_runtime` (the loopback MCP endpoint, its bound tool
handlers, and the attach-grant store). There is no separate MCP app sandbox or
resource runtime, so each upstream ``mcp.app.*`` operation resolves to the real
local capability or a typed refusal naming its counterpart:

* ``mcp.app.callTool`` — IMPLEMENTED: invoke a bound in-process tool handler
  through the canonical ``plugin_runtime.invoke`` (e-stop + shared durable
  confirmation journal + bound-handler check), or a configured stdio/http MCP
  server through the brokered ``mcp_servers.invoke`` -> ``mcp_client`` transport
  (e-stop + shared confirmation + capability/allowlist gate + bounded timeout).
  A server that is inventory-only (not enabled, runnable, and allowlisted)
  returns the typed ``MCP_CLIENT_INVOCATION_OPERATOR_REQUIRED`` refusal.
* ``mcp.app.listResources`` / ``mcp.app.readResource`` — real when a configured,
  ready server is named: the call is brokered through
  ``mcp_servers.invoke_method`` -> ``mcp_client`` (e-stop + shared confirmation
  + method gate) and returns the live ``resources/list`` / ``resources/read``
  result. With no mounted server (the default) the handler keeps its typed
  SAFE_EQUIVALENT code (``MCP_RESOURCES_LOCAL_TOOL`` /
  ``MCP_READ_RESOURCE_LOCAL_TOOL``) naming the canonical MCP tool layer.
* ``mcp.app.listResourceTemplates`` — OPERATOR_ONLY: no in-tree capability
  exposes MCP resource templates.
* ``mcp.app.updateModelContext`` — OPERATOR_ONLY: needs the MCP App sandbox
  runtime Qubit does not run.
"""
from __future__ import annotations

from typing import Any, Dict

from ..gateway_ws import GatewayError

MCP_BAD_PARAMS = "GATEWAY_BAD_PARAMS"

#: SAFE_EQUIVALENT typed code (real local counterpart: the MCP tool layer).
MCP_RESOURCES_LOCAL_TOOL = "MCP_RESOURCES_LOCAL_TOOL"
MCP_READ_RESOURCE_LOCAL_TOOL = "MCP_READ_RESOURCE_LOCAL_TOOL"
#: OPERATOR_ONLY typed codes (missing live runtime / service).
MCP_RESOURCE_TEMPLATES_OPERATOR_REQUIRED = "MCP_RESOURCE_TEMPLATES_OPERATOR_REQUIRED"
MCP_APP_SANDBOX_OPERATOR_REQUIRED = "MCP_APP_SANDBOX_OPERATOR_REQUIRED"


def _mcp_str(params: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = params.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _mcp_confirmed(params: Dict[str, Any]) -> bool:
    value = params.get("confirm", params.get("confirmed", False))
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "confirm")
    return False


def _mcp_servers(cfg: Any) -> Dict[str, Any]:
    from ... import mcp_servers as MS

    try:
        return MS.list_servers(cfg)
    except Exception as exc:  # a broken store must not break the refusal
        return {"state": "DEGRADED", "servers": [],
                "detail": str(exc)[:200]}


def _mcp_is_in_process_tool(cfg: Any, name: str) -> bool:
    """True when *name* resolves to an in-process plugin host capability."""
    from ... import plugin_runtime as PR

    try:
        PR.get_plugin(cfg, name)
        return True
    except Exception:
        return False


def _mcp_call_tool(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import mcp_servers as MS
    from ... import plugin_runtime as PR

    name = _mcp_str(params, "toolName", "tool", "name")
    server = _mcp_str(params, "server", "serverName") or name
    if not name:
        raise GatewayError(MCP_BAD_PARAMS, "toolName is required")
    raw_args = params.get("arguments")
    if not isinstance(raw_args, dict):
        raw_args = params.get("args") if isinstance(params.get("args"),
                                                      dict) else {}
    # A configured (external) MCP server runs through the canonical brokered
    # client (``mcp_servers.invoke`` -> ``mcp_client``: e-stop + shared
    # confirmation + capability/allowlist gate). A server that is inventory-only
    # still returns the typed OPERATOR_ONLY refusal -- never a fabricated call.
    # In-process host handlers (including host adapters) keep the plugin-runtime
    # path.
    configured = {str(s.get("name")) for s in
                  MS.list_servers(cfg).get("servers", [])
                  if isinstance(s, dict) and s.get("name")}
    if server in configured and not _mcp_is_in_process_tool(cfg, server):
        mcp_tool = _mcp_str(params, "method") or _mcp_str(params, "tool") or name
        try:
            out = MS.invoke(
                cfg, server, tool=mcp_tool, args=raw_args,
                confirmed=_mcp_confirmed(params),
                confirmation_id=_mcp_str(params, "confirmationId",
                                         "confirmation_id"),
                requested_by="gateway:mcp")
        except MS.McpServerError as exc:
            raise GatewayError(exc.code, exc.detail) from exc
        return {"state": str(out.get("state", "OPERATOR_ONLY")),
                "toolName": mcp_tool, "server": server,
                "code": out.get("code"), "result": out.get("result"),
                "text": out.get("text"), "invocable": out.get("invocable"),
                "operator_prerequisite": out.get("operator_prerequisite"),
                "authority": out.get("authority")}
    out = PR.invoke(
        cfg, name, args=raw_args, confirmed=_mcp_confirmed(params),
        confirmation_id=_mcp_str(params, "confirmationId", "confirmation_id"),
        requested_by="gateway:mcp")
    return {"state": str(out.get("state", "OK")), "toolName": name,
            "result": out.get("result"), "tool": out.get("plugin"),
            "authority": "voicebridge.plugin_runtime.invoke (in-process MCP "
                         "tool host; e-stop + shared confirmation)"}


def _mcp_list_resources(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import mcp_servers as MS

    server = _mcp_str(params, "server", "serverName")
    if server and not _mcp_is_in_process_tool(cfg, server):
        configured = {str(s.get("name")) for s in
                      MS.list_servers(cfg).get("servers", [])
                      if isinstance(s, dict) and s.get("name")}
        if server in configured:
            try:
                out = MS.invoke_method(
                    cfg, server, "resources/list", params={},
                    confirmed=_mcp_confirmed(params),
                    confirmation_id=_mcp_str(params, "confirmationId",
                                             "confirmation_id"),
                    requested_by="gateway:mcp")
            except MS.McpServerError as exc:
                raise GatewayError(exc.code, exc.detail) from exc
            payload = out.get("result") if isinstance(out.get("result"),
                                                      dict) else {}
            resources = payload.get("resources") if isinstance(payload,
                                                               dict) else None
            return {"state": str(out.get("state", "OPERATOR_ONLY")),
                    "code": out.get("code"),
                    "server": server,
                    "resources": resources if isinstance(resources, list)
                    else [],
                    "result": out.get("result"),
                    "text": out.get("text"),
                    "names": out.get("names"),
                    "invocable": out.get("invocable"),
                    "operator_prerequisite": out.get("operator_prerequisite"),
                    "authority": out.get("authority")}
    servers = _mcp_servers(cfg)
    return {"state": "UNAVAILABLE", "code": MCP_RESOURCES_LOCAL_TOOL,
            "detail": ("Qubit's canonical MCP host is tools-only; MCP "
                       "resources are served through the brokered agent tool "
                       "layer, not a gateway resource endpoint"),
            "qubit_counterpart": ("the MCP tool layer's `list_resources` "
                                  "(qubit_runtime/tools/mcp_tool.py)"),
            "servers": servers.get("servers", []),
            "authority": "voicebridge.mcp_servers.list_servers"}


def _mcp_read_resource(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import mcp_servers as MS

    uri = _mcp_str(params, "uri", "resource")
    if not uri:
        raise GatewayError(MCP_BAD_PARAMS, "uri is required")
    server = _mcp_str(params, "server", "serverName")
    if server and not _mcp_is_in_process_tool(cfg, server):
        configured = {str(s.get("name")) for s in
                      MS.list_servers(cfg).get("servers", [])
                      if isinstance(s, dict) and s.get("name")}
        if server in configured:
            try:
                out = MS.invoke_method(
                    cfg, server, "resources/read", params={"uri": uri},
                    confirmed=_mcp_confirmed(params),
                    confirmation_id=_mcp_str(params, "confirmationId",
                                             "confirmation_id"),
                    requested_by="gateway:mcp")
            except MS.McpServerError as exc:
                raise GatewayError(exc.code, exc.detail) from exc
            payload = out.get("result") if isinstance(out.get("result"),
                                                      dict) else {}
            contents = payload.get("contents") if isinstance(payload,
                                                             dict) else None
            return {"state": str(out.get("state", "OPERATOR_ONLY")),
                    "code": out.get("code"),
                    "server": server,
                    "uri": uri,
                    "contents": contents if isinstance(contents, list) else [],
                    "result": out.get("result"),
                    "text": out.get("text"),
                    "invocable": out.get("invocable"),
                    "operator_prerequisite": out.get("operator_prerequisite"),
                    "authority": out.get("authority")}
    return {"state": "UNAVAILABLE", "code": MCP_READ_RESOURCE_LOCAL_TOOL,
            "detail": ("Qubit's canonical MCP host is tools-only; a resource "
                       "is read through the brokered agent tool layer"),
            "qubit_counterpart": ("the MCP tool layer's `read_resource` "
                                  "(qubit_runtime/tools/mcp_tool.py)"),
            "uri": uri,
            "authority": "voicebridge.mcp_servers.list_servers"}


def _mcp_list_resource_templates(cfg: Any, params: Dict[str, Any]
                                ) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": MCP_RESOURCE_TEMPLATES_OPERATOR_REQUIRED,
            "detail": ("no in-tree Qubit capability exposes MCP resource "
                       "templates; the canonical MCP host is tools-only"),
            "operator_prerequisite": ("an MCP server/runtime that advertises "
                                      "resource templates"),
            "qubit_counterpart": ("the MCP tool layer's `list_resources` "
                                  "(qubit_runtime/tools/mcp_tool.py)")}


def _mcp_update_model_context(cfg: Any, params: Dict[str, Any]
                              ) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": MCP_APP_SANDBOX_OPERATOR_REQUIRED,
            "detail": ("updating an MCP App's model context needs the MCP App "
                       "sandbox runtime and an active app view; Qubit runs no "
                       "MCP App sandbox"),
            "operator_prerequisite": ("an MCP App sandbox runtime/active app "
                                      "view"),
            "qubit_counterpart": ("voicebridge.plugin_runtime.mcp_doctor "
                                  "(in-process MCP host diagnostics)")}


def register(registry: Any) -> None:
    """Register the ``mcp.app.*`` handlers on *registry*."""
    registry.register("mcp.app.callTool", _mcp_call_tool)
    registry.register("mcp.app.listResources", _mcp_list_resources)
    registry.register("mcp.app.readResource", _mcp_read_resource)
    registry.register("mcp.app.listResourceTemplates",
                      _mcp_list_resource_templates)
    registry.register("mcp.app.updateModelContext",
                      _mcp_update_model_context)
