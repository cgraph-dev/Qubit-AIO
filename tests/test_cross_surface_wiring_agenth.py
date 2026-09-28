"""Agent H — cross-surface wiring fixes (cloud policy + MCP read authority).

Two split-brain bugs, both proven here at the seam the live surfaces use:

* **Cloud policy.** ``voicebridge.providers.gateway.run`` built its policy from
  ``intent.frontier.enabled`` only, so a chat profile the resolver had already
  enabled through ``intent.frontier.profile_overrides`` was reported executable
  and then denied ``CLOUD_DISABLED``. ``run`` now honors the resolved profile's
  policy/allowlist override, and OpenCode relays get the required
  ``x-opencode-session`` header the vendored runtime already sends.
* **MCP reads.** ``qubit mcp list/config/ls/picker`` forwarded to the vendored
  fork's second ``config.yaml mcp_servers`` store while native
  ``status``/``invoke`` read the canonical ``<state_dir>/mcp_servers.json``.
  The reads are now native on the one canonical registry.

Hermetic: a fake transport for the cloud path; temp XDG for the MCP path.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402


# ---------------------------------------------------------------------------
# BUG 1 — cloud policy threading + OpenCode session affinity
# ---------------------------------------------------------------------------
def _isolate_xdg(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))


def _cloud_cfg(tmp_path: Path):
    """Config whose GLOBAL frontier switch is off but one profile is overridden.

    Exactly the shipped shape: ``intent.frontier.enabled`` unset (False) while
    ``profile_overrides.qubit-go.policy_enabled/allowlisted`` opt that profile
    in. The override is what the resolver reports executable.
    """
    from voicebridge.config import Config

    (tmp_path / "c").mkdir(exist_ok=True)
    p = tmp_path / "c" / "voice.yaml"
    p.write_text(yaml.safe_dump({
        "logging": {"jsonl": False},
        "intent": {"frontier": {"profile_overrides": {
            "test-cloud": {"policy_enabled": True, "allowlisted": True}}}},
    }))
    return Config.load(p)


def _ok_transport():
    """A 200 OpenAI-shaped response; records the headers it was handed."""
    seen = {}

    def _t(url, headers, payload, timeout_s):
        seen["url"] = url
        seen["headers"] = dict(headers or {})
        return 200, {"choices": [{"message": {"content": "PONG"}}],
                     "usage": {"prompt_tokens": 3, "completion_tokens": 1}}

    return _t, seen


def _request(provider="acme-cloud", model="m1"):
    from voicebridge.providers.contracts import ProviderRequest

    return ProviderRequest(prompt="hello", max_tokens=16, timeout_s=5.0,
                           route="frontier", provider=provider, model=model)


def test_gateway_denies_cloud_when_no_profile_is_threaded(tmp_path,
                                                          monkeypatch):
    """Baseline: without the resolved profile the global switch fails closed."""
    from voicebridge.providers import gateway as G

    _isolate_xdg(tmp_path, monkeypatch)
    cfg = _cloud_cfg(tmp_path)
    transport, _seen = _ok_transport()
    res = G.run(_request(), cfg, transport=transport, api_key="test-key")
    assert res.ok is False, res
    assert res.code == "CLOUD_DISABLED", res.code


def test_gateway_honors_profile_override_threaded_from_run_role(tmp_path,
                                                                 monkeypatch):
    """The override the resolver reported executable must reach ``run``."""
    from voicebridge.providers import gateway as G

    _isolate_xdg(tmp_path, monkeypatch)
    cfg = _cloud_cfg(tmp_path)
    transport, _seen = _ok_transport()
    res = G.run(_request(), cfg, transport=transport, api_key="test-key",
                base_url="https://acme.test/v1", profile_id="test-cloud")
    assert res.ok is True, (res.code, res.message)
    assert res.text == "PONG", res.text


def test_resolver_effective_profile_is_what_gateway_run_honors(tmp_path,
                                                               monkeypatch):
    """End-to-end authority: ``resolve`` says executable ⇒ ``run`` executes.

    Builds a profile record in the registry with egress consent, backed by the
    same ``profile_overrides``, then confirms ``resolve_model`` reports it
    executable and ``run`` does not deny it on policy.
    """
    from voicebridge.config import Config
    from voicebridge.providers import gateway as G
    from voicebridge.providers import resolver as R

    _isolate_xdg(tmp_path, monkeypatch)
    (tmp_path / "c").mkdir(exist_ok=True)
    p = tmp_path / "c" / "voice.yaml"
    p.write_text(yaml.safe_dump({
        "logging": {"jsonl": False},
        "intent": {"frontier": {"profile_overrides": {
            "cloud-x": {"policy_enabled": True, "allowlisted": True}}}},
    }))
    cfg = Config.load(p)
    from voicebridge import profiles as P

    reg = P._registry_path(cfg)
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps({"version": 1, "model_profiles": [{
        "id": "cloud-x", "kind": "api", "provider": "acme",
        "model": "m1", "base_url": "https://acme.test/v1",
        "locality": "external", "egress_consent": "allow",
        "roles": ["chat"], "enabled": True,
    }]}))

    routed = R.resolve_model(cfg, "cloud-x")
    assert routed["ok"] is True, routed
    assert routed["executable"] is True, routed

    transport, _seen = _ok_transport()
    res = G.run(_request(provider="acme", model="m1"), cfg,
                transport=transport, api_key="k",
                profile_id=str(routed.get("profile_id") or "cloud-x"))
    assert res.ok is True, (res.code, res.message)


def test_opencode_target_transport_carries_session_header():
    from voicebridge.providers.gateway import _opencode_session_transport

    calls = []

    def _base(url, headers, payload, timeout_s):
        calls.append(dict(headers or {}))
        return 200, {}

    wrapped = _opencode_session_transport(
        _base, "opencode-go", "https://opencode.ai/zen/go/v1",
        "sess-abc", "")
    wrapped("https://opencode.ai/zen/go/v1/chat/completions", {}, {}, 5.0)
    assert calls and calls[0].get("x-opencode-session") == "sess-abc", calls


def test_non_opencode_target_transport_is_untouched():
    from voicebridge.providers.gateway import _opencode_session_transport

    def _base(url, headers, payload, timeout_s):
        return 200, {}

    wrapped = _opencode_session_transport(
        _base, "acme", "https://acme.test/v1", "sess-abc", "")
    assert wrapped is _base


def test_run_accepts_session_id_and_profile_id():
    import inspect

    from voicebridge.providers import gateway as G

    params = inspect.signature(G.run).parameters
    assert "profile_id" in params and "session_id" in params
    assert params["session_id"].default == ""


# ---------------------------------------------------------------------------
# BUG 2 — MCP read commands use the ONE canonical registry
# ---------------------------------------------------------------------------
def _mcp_env(tmp_path, monkeypatch):
    from voicebridge.config import Config

    _isolate_xdg(tmp_path, monkeypatch)
    cfg_dir = tmp_path / "c" / "qubit-voicebridge"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    p = cfg_dir / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return Config.load(p)


def _seed(cfg, name):
    from voicebridge import mcp_servers as MS

    MS.upsert(cfg, name, {"transport": "stdio", "command": "true",
                          "args": [], "capabilities": ["echo"]})
    MS.set_enabled(cfg, name, True)


def _capture_main(argv):
    from voicebridge import cli as CLI

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI.main(list(argv))
    return rc, buf.getvalue()


def test_mcp_route_declines_fork_forward():
    from voicebridge import cli_qubit_forward as HF

    for sub in ("list", "config", "ls", "picker"):
        assert HF.route(["mcp", sub]) is None, sub
        assert ("mcp", sub) in HF.NATIVE_MCP_READS, sub


def test_mcp_list_and_status_agree_on_a_dashboard_created_server(
        tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge import qubit_runtime

    cfg = _mcp_env(tmp_path, monkeypatch)
    _seed(cfg, "dashboard-srv")

    # The fork must never be invoked for an MCP read.
    calls = []
    monkeypatch.setattr(qubit_runtime, "run",
                        lambda argv=None: calls.append(list(argv or [])) or 0)

    rc_list, out_list = _capture_main(["mcp", "list"])
    rc_status, out_status = _capture_main(["mcp", "status"])
    assert rc_list == 0 and rc_status == 0
    assert calls == [], calls

    listed = json.loads(out_list)
    status = json.loads(out_status)
    assert "dashboard-srv" in listed["servers"], listed
    assert listed == status, (listed, status)
    assert listed["schema"] == "qubit-mcp-status/1"
    # Provenance is the canonical registry, not the fork's config store.
    rec = [r for r in listed["records"] if r["name"] == "dashboard-srv"][0]
    assert rec["source"] == "qubit-registry", rec


def test_mcp_read_aliases_all_hit_the_same_registry(tmp_path, monkeypatch):
    from voicebridge import qubit_runtime

    cfg = _mcp_env(tmp_path, monkeypatch)
    _seed(cfg, "only-here")
    monkeypatch.setattr(qubit_runtime, "run",
                        lambda argv=None: pytest.fail("fork invoked"))

    outputs = []
    for sub in ("list", "ls", "config", "picker", "status"):
        rc, out = _capture_main(["mcp", sub])
        assert rc == 0, (sub, out)
        outputs.append(json.loads(out)["servers"])
    assert all(row == ["only-here"] for row in outputs), outputs
