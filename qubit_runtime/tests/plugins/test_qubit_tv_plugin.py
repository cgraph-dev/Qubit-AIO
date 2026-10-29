"""QubitTV plugin: fixed tools, closed schemas, no model-controlled session scope."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "qubit_tv_under_test", Path(__file__).parents[2] / "plugins" / "qubit-tv" / "__init__.py"
)
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

TOKEN = "x" * 24


class Capability:
    def __init__(self):
        self.calls = []

    def available(self):
        return True

    def media_request(self, *, session_id, operation):
        self.calls.append((session_id, operation))
        return {"operation": operation["type"]}


class Context:
    def __init__(self, capability):
        self.desktop = capability
        self.tools = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)


def registered(capability=None):
    ctx = Context(capability)
    plugin.register(ctx)
    return ctx


def test_registers_seven_fixed_tools_with_model_free_session_scope():
    cap = Capability()
    ctx = registered(cap)
    assert [tool["name"] for tool in ctx.tools] == [
        "qubit_tv_context",
        "qubit_tv_control",
        "qubit_tv_transcript",
        "qubit_tv_download",
        "qubit_tv_jobs",
        "qubit_tv_job_status",
        "qubit_tv_job_cancel",
    ]
    assert all("session_id" not in str(tool["schema"]) for tool in ctx.tools)
    control = next(tool for tool in ctx.tools if tool["name"] == "qubit_tv_control")
    result = control["handler"](
        {"context_token": TOKEN, "navigation_generation": 3, "action": "play"},
        session_id="durable",
    )
    assert json.loads(result) == {"operation": "control"}
    assert cap.calls == [
        ("durable", {"type": "control", "context_token": TOKEN, "navigation_generation": 3, "action": "play"})
    ]


def test_rejects_model_route_fields_and_malformed_values_before_dispatch():
    cap = Capability()
    ctx = registered(cap)
    control = next(tool for tool in ctx.tools if tool["name"] == "qubit_tv_control")
    assert json.loads(control["handler"](
        {"context_token": TOKEN, "navigation_generation": 0, "action": "play", "session_id": "other"},
        session_id="trusted",
    )) == {"error": "invalid-operation"}
    assert json.loads(control["handler"](
        {"context_token": TOKEN, "navigation_generation": 0, "action": {}},
        session_id="trusted",
    )) == {"error": "invalid-operation"}
    assert json.loads(control["handler"](
        {"context_token": "short", "navigation_generation": 0, "action": "play"},
        session_id="trusted",
    )) == {"error": "invalid-operation"}
    assert json.loads(control["handler"](
        {"context_token": TOKEN, "navigation_generation": -1, "action": "play"},
        session_id="trusted",
    )) == {"error": "invalid-operation"}
    assert json.loads(control["handler"](
        {"context_token": TOKEN, "navigation_generation": 0, "action": "rewind"},
        session_id="trusted",
    )) == {"error": "invalid-operation"}
    assert json.loads(control["handler"](
        {"context_token": TOKEN, "navigation_generation": 0, "action": "seek"},
        session_id="trusted",
    )) == {"error": "invalid-operation"}
    assert cap.calls == []


def test_old_host_without_capability_is_unavailable_and_withdrawn():
    ctx = registered(None)
    assert len(ctx.tools) == 7
    assert not any(tool["check_fn"]() for tool in ctx.tools)
    handler = next(tool["handler"] for tool in ctx.tools if tool["name"] == "qubit_tv_context")
    assert json.loads(handler({}, session_id="durable")) == {"error": "host-capability-unavailable"}


def test_missing_dispatcher_session_is_refused_before_dispatch():
    cap = Capability()
    ctx = registered(cap)
    handler = next(tool["handler"] for tool in ctx.tools if tool["name"] == "qubit_tv_context")
    assert json.loads(handler({})) == {"error": "session-unavailable"}
    assert cap.calls == []


def test_all_handlers_dispatch_their_closed_operation_type():
    cap = Capability()
    ctx = registered(cap)
    args_by_name = {
        "qubit_tv_context": {},
        "qubit_tv_control": {"context_token": TOKEN, "navigation_generation": 0, "action": "play"},
        "qubit_tv_transcript": {"context_token": TOKEN, "language": "en-US"},
        "qubit_tv_download": {"context_token": TOKEN, "quality": "best"},
        "qubit_tv_jobs": {},
        "qubit_tv_job_status": {"job_id": "job_1"},
        "qubit_tv_job_cancel": {"job_id": "job_1"},
    }
    results = [
        json.loads(tool["handler"](args_by_name[tool["name"]], session_id="durable"))
        for tool in ctx.tools
    ]
    assert [item["operation"] for item in results] == [
        "context",
        "control",
        "transcript.start",
        "download.start",
        "jobs.list",
        "job.status",
        "job.cancel",
    ]


def test_real_plugin_context_desktop_capability_is_traversed(monkeypatch, tmp_path):
    if not hasattr(__import__("qubit_cli.plugins", fromlist=["PluginContext"]).PluginContext, "desktop"):
        pytest.skip("host PluginContext.desktop capability is added by the parallel runtime lane")
    desktop = pytest.importorskip("qubit_cli.plugin_desktop")
    from qubit_cli.plugins import PluginContext, PluginManager
    from qubit_cli.plugins_manifest import PluginManifest

    calls = []

    monkeypatch.setattr(desktop.DesktopPluginCapability, "available", lambda self: True)
    monkeypatch.setattr(
        desktop.DesktopPluginCapability,
        "media_request",
        lambda self, *, session_id, operation: calls.append((session_id, operation)) or {"operation": operation["type"]},
    )
    ctx = PluginContext(PluginManifest(name="qubit-tv"), PluginManager(scope_key=str(tmp_path)))
    captured = []
    ctx.register_tool = lambda **kwargs: captured.append(kwargs)  # type: ignore[method-assign]
    plugin.register(ctx)

    assert [tool["name"] for tool in captured] == [
        "qubit_tv_context",
        "qubit_tv_control",
        "qubit_tv_transcript",
        "qubit_tv_download",
        "qubit_tv_jobs",
        "qubit_tv_job_status",
        "qubit_tv_job_cancel",
    ]
    handler = next(tool["handler"] for tool in captured if tool["name"] == "qubit_tv_context")
    assert json.loads(handler({}, session_id="durable")) == {"operation": "context"}
    assert calls == [("durable", {"type": "context"})]
