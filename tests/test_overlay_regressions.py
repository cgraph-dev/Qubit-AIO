"""Task 10 — targeted regression tests for the B4 overlay fixes + B3-R1.

Covers, through the real production modules:

* **B4-01/B4-02** ``/api/model/info`` derives capabilities + the auto-detected
  context window from the vendored metadata resolver instead of baking in
  placeholders (the dead ``/chat`` reasoning selector).
* **B4-03** ``/api/status`` carries memory/disk pressure, the platform map, the
  auth gate, and the component rollup the reference SPA consumes.
* **B4-04** ``gateway_supervisor.status`` is scoped to the resolved
  ``QUBIT_HOME`` (an active unit from a different home is not reported as ours).
* **B4-05** MCP probe errors are redacted before they reach the served body.
* **B4-06** the pinned plugin index serves de-branded display fields while
  keeping the upstream repo/ref as internal provenance.
* **B4-07** the ``Qubit-cli`` toolset id (capital Q) resolves to the real
  bundle, not zero tools.
* **B3-R1** the help epilogue slice preserves the ``qubit`` separator.

The throwaway ``HOME``/``XDG_*``/``QUBIT_HOME`` fixture keeps the read-only
reference home and live services untouched.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
class _Cfg:
    """Minimal cfg double for the injected-runner/reader unit surfaces."""

    def __init__(self, data=None):
        self._d = data or {}

    def get(self, *path, default=None):
        node = self._d
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Throwaway HOME/XDG/QUBIT_HOME so no test touches the live install."""
    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
                 "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    yield tmp_path


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# --------------------------------------------------------------------------- #
# B3-R1 — help epilogue separator
# --------------------------------------------------------------------------- #
def test_b3_r1_rebrand_epilogue_preserves_separator():
    from voicebridge.qubit_runtime import _rebrand_epilogue

    text = ('qubit chat -q "Hello"        Single query mode\n'
            'qubit --tui                  Launch the modern TUI\n'
            'qubit setup                  Run setup wizard\n')
    out = _rebrand_epilogue(text, "qubit")
    assert "qubit chat -q" in out
    assert "qubit --tui" in out
    assert "qubit setup" in out
    for broken in ("qubitchat", "qubit--tui", "qubitsetup", "qubit--"):
        assert broken not in out


def test_b3_r1_bare_upstream_token_maps_to_prog():
    from voicebridge.qubit_runtime import _rebrand_epilogue

    # No trailing newline on the last line: the exact-token branch fires.
    assert _rebrand_epilogue("hermes", "qubit") == "qubit"


# --------------------------------------------------------------------------- #
# B4-01 / B4-02 — model capabilities + auto context
# --------------------------------------------------------------------------- #
def test_b4_01_02_model_info_derives_caps_and_auto_context(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    monkeypatch.setattr(HC, "_resolved_model", lambda cfg: {
        "model": "deepseek-v4-flash", "provider": "opencode-go", "ctx": 200000,
        "kind": "api", "locality": "api", "base_url": "https://api.example",
        "profile_id": "default"})
    # Auto window differs from the configured/declared one.
    monkeypatch.setattr(HC, "_auto_context_length", lambda *a, **k: 1_000_000)
    monkeypatch.setattr(HC, "_vendored_runtime", lambda cfg: None)

    caps = types.SimpleNamespace(
        supports_tools=True, supports_vision=True, supports_reasoning=True,
        context_window=1_000_000, max_output_tokens=384000,
        model_family="deepseek")
    fake_md = types.ModuleType("agent.models_dev")
    fake_md.get_model_capabilities = lambda provider, model: caps
    fake_agent = types.ModuleType("agent")
    fake_agent.models_dev = fake_md
    monkeypatch.setitem(sys.modules, "agent", fake_agent)
    monkeypatch.setitem(sys.modules, "agent.models_dev", fake_md)

    code, out = HC.h_model_info(_cfg(tmp_path, monkeypatch), {}, {}, {})
    assert code == 200
    assert out["auto_context_length"] == 1_000_000
    assert out["config_context_length"] == 200000
    assert out["effective_context_length"] == 200000
    assert out["capabilities"]["supports_reasoning"] is True
    assert out["capabilities"]["supports_vision"] is True
    assert out["capabilities"]["max_output_tokens"] == 384000


def test_b4_01_model_info_falls_back_without_vendored_metadata(tmp_path,
                                                               monkeypatch):
    from voicebridge import qubit_compat as HC

    monkeypatch.setattr(HC, "_resolved_model", lambda cfg: {
        "model": "local-model", "provider": "ollama", "ctx": 4096,
        "kind": "local", "locality": "local", "base_url": ""})

    def boom(cfg):
        raise RuntimeError("no fork")

    monkeypatch.setattr(HC, "_vendored_runtime", boom)
    code, out = HC.h_model_info(_cfg(tmp_path, monkeypatch), {}, {}, {})
    assert code == 200
    assert out["capabilities"]["model_family"] == "qwen3-4b"
    assert out["effective_context_length"] == 4096


# --------------------------------------------------------------------------- #
# B4-03 — /api/status advisory + platform + auth + components
# --------------------------------------------------------------------------- #
def test_b4_03_status_carries_reference_payload(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import runtime_inventory as RI
    from voicebridge import gateway_supervisor as GS

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(RI, "runtime_inventory", lambda cfg: {"state": "OK"})
    monkeypatch.setattr(GS, "status", lambda cfg, **kw: {
        "running": True, "state": "OK", "pid": 4242,
        "service_unit": "qubit-gateway.service", "detail": "active",
        "daemon": {"platforms_map": {"telegram": {"state": "connected"},
                                     "slack": {"state": "error"}},
                   "active_agents": 2},
    })
    monkeypatch.setattr(HC, "_status_pressure",
                        lambda cfg, home: ({"pressure": "ok", "system_total_mb": 1},
                                           {"pressure": "ok", "free_mb": 2}))
    monkeypatch.setattr(HC, "_status_profile_names", lambda cfg: ["default"])
    monkeypatch.setattr(HC, "_vendored_runtime", lambda cfg: None)

    code, out = HC.h_status(cfg, {}, {}, {})
    assert code == 200
    assert out["memory"]["pressure"] == "ok"
    assert out["disk"]["pressure"] == "ok"
    assert out["gateway_platforms"] == {"telegram": {"state": "connected"},
                                        "slack": {"state": "error"}}
    assert out["active_agents"] == 2
    assert out["auth_required"] is False and out["auth_providers"] == []
    assert out["gateway_mode"] == "standalone"
    assert out["profiles"] == ["default"]
    assert set(out["components"]) >= {"gateway", "dashboard", "storage", "platforms"}
    assert out["overall"] in ("ok", "degraded")
    assert out["components"]["platforms"]["configured"] == 2
    assert out["components"]["platforms"]["connected"] == 1


# --------------------------------------------------------------------------- #
# B4-04 — gateway status is QUBIT_HOME-scoped
# --------------------------------------------------------------------------- #
def _show(active, pid=9999, env_home=""):
    env = f"Environment=QUBIT_HOME={env_home}\n" if env_home else ""
    return (0,
            f"ActiveState={'active' if active else 'inactive'}\n"
            f"SubState={'running' if active else 'dead'}\n"
            f"MainPID={pid if active else 0}\n{env}", "")


def test_b4_04_status_rejects_a_different_home(tmp_path, monkeypatch):
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS.shutil, "which", lambda _n: "/usr/bin/systemctl")
    other = tmp_path / "someone-elses-home"
    cfg = _Cfg({"webui": {"host": "127.0.0.1", "port": 8890}})
    st = GS.status(cfg, runner=lambda a, t: _show(True, env_home=str(other)),
                   probe=lambda h, p: True, daemon_reader=lambda: None,
                   control_query=lambda: None)
    assert st["state"] == "NOT_INSTALLED", st
    assert st["running"] is False and st["pid"] is None
    assert st["port_in_use"] is False and st["health"] == "DOWN"


def test_b4_04_status_accepts_matching_home(tmp_path, monkeypatch):
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS.shutil, "which", lambda _n: "/usr/bin/systemctl")
    own = tmp_path / "qhome"
    own.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(own))
    cfg = _Cfg({"webui": {"host": "127.0.0.1", "port": 8890}})
    st = GS.status(cfg, runner=lambda a, t: _show(True, env_home=str(own)),
                   probe=lambda h, p: True, daemon_reader=lambda: None,
                   control_query=lambda: None)
    assert st["running"] is True and st["pid"] == 9999
    assert st["state"] == "OK" and st["health"] == "OK"


# --------------------------------------------------------------------------- #
# B4-05 — MCP probe redaction
# --------------------------------------------------------------------------- #
def test_b4_05_probe_errors_are_redacted(monkeypatch):
    from voicebridge import mcp_client as MC
    from voicebridge import mcp_servers as MS

    secret = "abcDEF1234567890"
    assert secret not in MS._redact_probe(
        f"Authorization: Bearer {secret}")

    def boom(*_a, **_k):
        raise MC.McpClientError("PROBE_FAIL",
                                f"connect failed: Authorization: Bearer {secret}")

    monkeypatch.setattr(MS.MC, "discover_tools", boom)
    out = MS._probe(_Cfg({}), {"name": "s", "enabled": True, "transport": "http"})
    blob = json.dumps(out)
    assert secret not in blob
    assert out["connected"] is False and out["state"] == "ERROR"


# --------------------------------------------------------------------------- #
# B4-06 — pinned plugin index de-branded display / provenance kept
# --------------------------------------------------------------------------- #
_LEGACY_TOKENS = ("nousresearch", "nous portal", "openclaw", "hermes")


def test_b4_06_pinned_index_serves_debranded_display():
    from voicebridge import plugin_catalog as PC

    entries = PC.pinned_index_entries()
    assert entries
    for entry in entries:
        surface = {k: v for k, v in entry.items() if k != "provenance"}
        blob = json.dumps(surface).lower()
        for token in _LEGACY_TOKENS:
            assert token not in blob, (entry["name"], token, surface)
        assert entry["repo"] == "" and entry["maintainer"] == ""
        # The real upstream identity survives only as internal provenance.
        prov = entry["provenance"]
        assert prov["upstream_repo"].startswith("https://")
        assert len(entry["pinned_ref"]) == 40
    # A de-branded id still resolves.
    assert PC._index_entry("qubit-media-studio") is not None
    assert PC._index_entry("qubit-telegram-business") is not None


# --------------------------------------------------------------------------- #
# B4-07 — Qubit-cli alias case
# --------------------------------------------------------------------------- #
def test_b4_07_capital_qubit_cli_resolves_to_the_bundle(tmp_path, monkeypatch):
    from voicebridge import qubit_asgi as QA

    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    QA._overlay_applied = False
    QA._apply_qubit_config_overlay()

    import toolsets as ts

    lower = ts.resolve_toolset("qubit-cli")
    upper = ts.resolve_toolset("Qubit-cli")
    assert lower, "qubit-cli must resolve to a non-empty bundle"
    assert upper == lower, "Qubit-cli (capital Q) must resolve like qubit-cli"
    assert lower == ts.resolve_toolset("hermes-cli")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
