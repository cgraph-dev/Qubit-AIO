"""The slash worker inherits the parent agent's provider, not the config default (#57283).

A Desktop/TUI MoA session pins the live agent to the virtual ``moa`` provider, but the
persistent slash-worker subprocess was spawned with only ``--model``. QubitCLI re-resolves
the provider from config.yaml, so a ``/moa`` one-shot dispatched the MoA PRESET NAME to the
configured real provider (openrouter 402 / "No available channel for model default"). The
worker argv must carry ``--provider`` and the child must hand it to ``QubitCLI``.
"""

from types import SimpleNamespace

import pytest

import tui_gateway.server as server

# PRODUCT GAP: product _SlashWorker.__init__ (tui_gateway/server.py:286) lacks the
# provider param and --provider/--parent-pid argv that upstream 38880bd2 has
# (#57283: a MoA session dispatched its preset name to the real provider).
pytestmark = pytest.mark.xfail(strict=False, reason=(
    "PRODUCT GAP: _SlashWorker.__init__ lacks the provider param + argv upstream "
    "38880bd2 has (server.py:286 vs upstream:255)."))



class _Popen:
    """Capture the spawn argv without a real child."""

    def __init__(self, argv, **kw):
        self.argv = list(argv)
        self.stdout = self.stderr = iter(())
        self.stdin = None

    def poll(self):
        return 0


def _spawn(monkeypatch, **worker_kwargs) -> list[str]:
    captured = {}

    def _popen(argv, **kw):
        captured["argv"] = list(argv)
        return _Popen(argv, **kw)

    monkeypatch.setattr(server.subprocess, "Popen", _popen)
    server._SlashWorker("sess", "default", **worker_kwargs)
    return captured["argv"]


def test_slash_worker_argv_carries_provider(monkeypatch):
    argv = _spawn(monkeypatch, provider="moa")

    assert "--provider" in argv
    assert argv[argv.index("--provider") + 1] == "moa"
    # model still forwarded
    assert argv[argv.index("--model") + 1] == "default"


def test_slash_worker_argv_omits_empty_provider(monkeypatch):
    argv = _spawn(monkeypatch, provider=None)

    assert "--provider" not in argv


def test_slash_exec_spawns_with_the_live_agent_provider(monkeypatch):
    """First-use spawn in ``slash.exec`` passes the session agent's resolved provider — the MoA
    one-shot builds ``agent.provider == "moa"`` via ``_apply_model_switch`` before the worker runs."""
    worker_args = None

    class _RecordingWorker:
        def __init__(self, session_key, model, profile_home=None, provider=None):
            nonlocal worker_args
            worker_args = (session_key, model, profile_home, provider)
            self.stderr_tail = []

        def run(self, command):
            return "ok"

        def close(self):
            pass

    agent = SimpleNamespace(model="default", provider="moa")
    session = {"session_key": "s-key", "agent": agent, "profile_home": None, "running": False}

    monkeypatch.setattr(server, "_sessions", {"sid": session})
    monkeypatch.setattr(server, "_SlashWorker", _RecordingWorker)
    monkeypatch.setattr(server, "_sess_nowait", lambda params, rid: (session, None))
    monkeypatch.setattr(server, "_live_slash_command_output", lambda *a, **k: None)
    monkeypatch.setattr(server, "_is_snapshot_restore", lambda *a, **k: False)
    monkeypatch.setattr(server, "_mirror_slash_side_effects", lambda *a, **k: None)
    monkeypatch.setattr(server, "_resolve_model", lambda: "fallback-model")

    resp = server._methods["slash.exec"](1, {"session_id": "sid", "command": "/tools"})

    assert "error" not in resp, resp
    assert worker_args is not None
    assert worker_args[3] == "moa"


def test_slash_worker_child_forwards_provider_to_qubit_cli(monkeypatch):
    """The child's argparse accepts --provider and hands it to QubitCLI (mocked): the MoA
    virtual provider must reach ``QubitCLI(provider=...)`` instead of config re-resolution."""
    import sys as _sys
    import types

    import tui_gateway.slash_worker as sw

    built = {}

    class _FakeCLI:
        def __init__(self, **kwargs):
            built.update(kwargs)

    # main() resolves QubitCLI from ``cli`` only after arming the watchdog, so patch it there.
    monkeypatch.setitem(_sys.modules, "cli", types.SimpleNamespace(QubitCLI=_FakeCLI))
    monkeypatch.setattr(sw, "_start_parent_death_watchdog", lambda *a: None)
    monkeypatch.setattr(sw, "_prepare_slash_worker_runtime", lambda: None)
    monkeypatch.setattr(sw.sys, "stdin", type("S", (), {"readline": lambda self: ""})())
    monkeypatch.setattr(_sys, "argv", ["slash_worker", "--session-key", "s", "--model", "m",
                                       "--provider", "moa"])

    sw.main()

    assert built.get("provider") == "moa"
    assert built.get("model") == "m"
