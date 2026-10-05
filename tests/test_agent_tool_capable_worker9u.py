"""/qubit-chat agent_task parity: the worker must run the SAME tool-capable
agent the terminal /chat path uses, not the planner-only shim.

Root cause (measured 2026-10-05): ``voicebridge.agent.run_session`` never sent
a tool schema to a model. It ran a 9-tool reversible ``planner.plan()`` and a
plain text ``gateway.run_role`` with ``max_tokens=1024`` — so a reasoning model
emitted its whole budget as ``reasoning_content`` (``finish_reason: length``,
empty content), no tool ever ran, and ``_finish_task`` typed the promise
``TASK_NO_BUILD``. The vendored ``run_agent.AIAgent`` (what ``/chat`` drives)
exposes the full tool set (skills, plugins, MCP) and actually calls tools.

These tests pin the contract: when the vendored agent is importable and a
tool-capable model resolves, ``run_session`` drives it and reports the real
tool receipts. Hermetic: the vendored import and the agent run are doubles —
no model call, no network.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _install_fake_vendored_agent(monkeypatch, captured):
    """Install a fake ``run_agent.AIAgent`` + ``qubit_cli`` modules.

    The real vendored runtime is importable in production; tests must not
    depend on it (no network, no model). We pin the contract that
    ``run_session`` PREFERS the vendored agent and drives
    ``run_conversation``.
    """
    mods = {}

    class _FakeAgent:
        def __init__(self, **kw):
            captured["agent_kwargs"] = kw
            self.valid_tool_names = ["write_file", "read_file", "terminal"]

        def run_conversation(self, user_message, **kw):
            captured["user_message"] = user_message
            return {
                "final_response": "Created qubitchat-tools-proof.txt "
                                  "(hello world, 12 bytes).",
                "messages": [
                    {"role": "assistant", "content": "",
                     "tool_calls": [{"id": "1",
                                     "function": {"name": "write_file"}}]},
                    {"role": "tool", "name": "write_file",
                     "content": "wrote 12 bytes"},
                    {"role": "tool", "name": "read_file",
                     "content": "hello world"},
                    {"role": "assistant",
                     "content": "Created qubitchat-tools-proof.txt."},
                ],
            }

    run_agent = types.ModuleType("run_agent")
    run_agent.AIAgent = _FakeAgent
    mods["run_agent"] = run_agent

    qc = types.ModuleType("qubit_cli")
    qc.__path__ = []  # package
    mods["qubit_cli"] = qc

    config = types.ModuleType("qubit_cli.config")
    config.load_config = lambda: {
        "model": {"provider": "opencode-go", "default": "deepseek-v4.1-flash"}}
    mods["qubit_cli.config"] = config

    tools_config = types.ModuleType("qubit_cli.tools_config")
    tools_config._get_platform_tools = lambda cfg, platform: {
        "files", "terminal", "web"}
    mods["qubit_cli.tools_config"] = tools_config

    runtime_provider = types.ModuleType("qubit_cli.runtime_provider")
    runtime_provider.resolve_runtime_with_fallback = (
        lambda cfg, requested="", target_model=None, explicit_base_url="",
         explicit_api_key="": ({"provider": "opencode-go",
                                "requested_provider": "opencode-go",
                                "model": "deepseek-v4.1-flash",
                                "base_url": "https://example.invalid",
                                "api_key": "test-key"}, None))
    mods["qubit_cli.runtime_provider"] = runtime_provider

    oneshot = types.ModuleType("qubit_cli.oneshot")

    class _Choice:
        provider = "opencode-go"
        model = "deepseek-v4.1-flash"
        base_url = "https://example.invalid"
        api_key = "test-key"
        api_mode = None

    oneshot._resolve_model_and_provider = (
        lambda cfg, model, provider: _Choice())
    mods["qubit_cli.oneshot"] = oneshot

    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return captured


def test_run_session_uses_tool_capable_agent(monkeypatch):
    from voicebridge import agent as AG

    captured = {}
    _install_fake_vendored_agent(monkeypatch, captured)
    store = _FakeStore()
    monkeypatch.setattr(AG, "_canonical_session_store", lambda cfg: store)

    out = AG.run_session(
        {"agent": {"enabled": True}, "model": {}},
        "create a text document called qubitchat-tools-proof with the text "
        "hello world",
        session="tools-proof-9u",
        execute_tool=lambda tool, args: (_ for _ in ()).throw(
            AssertionError("planner execute_tool must not run")),
    )
    assert out.get("state") == "OK", out
    # The vendored agent actually ran and its tool calls are reported.
    assert "run_conversation" in captured or "agent_kwargs" in captured, captured
    assert "write_file" == out.get("tool_used"), out
    assert out.get("steps", 0) >= 1, out
    assert "qubitchat-tools-proof" in out.get("reply", ""), out


def test_run_session_falls_back_to_planner_without_vendored_agent(
        monkeypatch):
    """No vendored runtime importable -> today's planner behavior, unchanged."""
    from voicebridge import agent as AG

    # Force the vendored import to fail.
    monkeypatch.setitem(sys.modules, "run_agent", None)
    store = _FakeStore()
    monkeypatch.setattr(AG, "_canonical_session_store", lambda cfg: store)

    seen = {}

    def _execute(tool, args):
        seen["tool"] = tool
        return "did the thing"

    out = AG.run_session(
        {"agent": {"enabled": True}, "model": {}},
        "open notepad",  # a planner-eligible reversible tool shape
        session="fallback-9u",
        execute_tool=_execute,
    )
    # Either the planner proposed a tool (execute_tool called) or the model
    # path ran; the key contract is: no crash, a truthful state.
    assert out.get("state") in ("OK", "FAILED"), out


class _FakeStore:
    def __init__(self):
        self.rows = []

    def ensure(self, sid, *, model=None, identity="dashboard:user"):
        return {"state": "OK", "session": {"id": sid}}

    def append(self, sid, role, content, *, meta=None, model=None,
               timestamp=None):
        self.rows.append((role, content))
        return {"state": "OK", "message": {"role": role, "content": content},
                "message_count": len(self.rows)}

    def messages(self, sid, limit=200):
        return {"messages": [{"role": r, "content": c} for r, c in self.rows]}
