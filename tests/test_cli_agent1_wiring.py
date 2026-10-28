"""Agent 1 integration wave — `qubit <family> <sub>` real backends.

Every case invokes the canonical CLI entry (`voicebridge.cli.main`) and asserts
the JSON envelope produced by the Agent 5-10 backend plus its typed negative
path. Backends are hermetic and offline; no network or hardware is touched.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import cli as CLI  # noqa: E402


def _cfg(tmp_path, monkeypatch) -> Path:
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        d = tmp_path / var.lower()
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(d))
    monkeypatch.setenv("HOME", str(tmp_path))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return p


def _run(cfg_path, argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI.main([*argv, "--config", str(cfg_path)])
    body = buf.getvalue()
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = None
    return rc, parsed, body


def _subcommands(family: str):
    from voicebridge.cli import build_parser

    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            sp = action.choices.get(family)
            if sp is None:
                return set()
            for a in sp._actions:
                if isinstance(a, argparse._SubParsersAction):
                    return set(a.choices)
    return set()


def test_integration_table_covers_wired_backends():
    table = CLI._INTEGRATION
    required = {
        ("browser", "profiles"), ("browser", "create-profile"),
        ("browser", "open-profile"), ("browser", "close-managed-profile"),
        ("browser", "remove-profile"),
        ("computer-use", "screen"),
        ("local-models", "fallback"),
        ("gateway", "supervision-request"),
        ("gateway", "supervision-revert"), ("gateway", "discovery"),
        ("pairing", "setup-code"), ("pairing", "accept"),
        ("runtime", "recover"), ("runtime", "repair"), ("runtime", "history"),
        ("runtime", "update-report"), ("runtime", "discovery"),
        ("runtime", "marketplace"),
        ("egress", "status"), ("egress", "check"),
        ("policy", "conformance"), ("policy", "attest"),
        ("hooks", "emit"), ("skills", "workshop"),
        ("usage", None), ("usage", "telemetry"),
        ("receipts", None),
        ("logbook", None), ("logbook", "note"),
    }
    assert required <= set(table)
    assert len(table) >= 20
    # Every wired pair must be a real parser subcommand (or a leaf family for
    # the ``None`` default-action entries).
    for family, sub in table:
        if sub is None:
            assert family in _top_level_families(), family
            continue
        assert sub in _subcommands(family), (family, sub)
    # Native commands must NOT be shadowed by the integration table.
    assert ("browser", "close-profile") not in table
    assert ("experiments", "status") not in table


def test_integration_families_are_top_level_cli_names():
    """The integration-only families exist under their voice-parity names."""
    families = _top_level_families()
    for name in ("egress", "policy", "usage", "receipts", "logbook"):
        assert name in families, name
    # Existing families gain the real backend subcommands.
    assert {"profiles", "create-profile", "close-managed-profile"} <= \
        _subcommands("browser")
    assert {"supervision-request", "discovery"} <= _subcommands("gateway")
    assert {"recover", "repair", "history", "marketplace"} <= \
        _subcommands("runtime")


def _top_level_families():
    from voicebridge.cli import build_parser

    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


def test_usage_family_is_not_shadowed_by_the_fork_forward():
    """Regression: `usage` is a Qubit-native top-level family.

    Routing it through the fork-forward layer shadowed the native parser +
    ``_INTEGRATION`` handlers and turned ``qubit usage telemetry`` into a
    ``FORK_SUB_UNCLASSIFIED`` refusal. The fork layer must return ``None`` for
    the family (native parser owns it); the ported vendored account-usage
    report remains reachable as ``qubit agent usage``.
    """
    from voicebridge import cli_qubit_forward as HF

    assert HF.route(["usage"]) is None
    assert HF.route(["usage", "telemetry"]) is None
    assert HF.route(["usage", "analytics"]) is None
    assert "usage" not in HF.FAMILY_FORWARD_WAVE6


def test_browser_managed_profile_lifecycle(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    rc, out, _ = _run(cfg, ["browser", "profiles"])
    assert rc == 0 and out["schema"] == "qubit-browser-profiles/1"

    rc, out, _ = _run(cfg, ["browser", "create-profile", "research"])
    assert rc == 3 and out["code"] == "BROWSER_CONFIRM_REQUIRED"

    rc, out, _ = _run(cfg, ["browser", "create-profile", "research",
                            "--confirm", "--cdp-port", "9333"])
    assert rc == 0 and out["profile"]["name"] == "research"

    rc, out, _ = _run(cfg, ["browser", "profiles"])
    assert rc == 0 and [p["name"] for p in out["profiles"]] == ["research"]

    # No approved launcher is configured: typed handoff, never a spawn.
    rc, out, _ = _run(cfg, ["browser", "open-profile", "research", "--confirm"])
    assert rc == 2 and out["code"] == "BROWSER_PROFILE_NO_LAUNCHER"

    rc, out, _ = _run(cfg, ["browser", "close-managed-profile", "research",
                            "--confirm"])
    assert rc == 0 and out["closed"] is True

    rc, out, _ = _run(cfg, ["browser", "remove-profile", "research",
                            "--confirm"])
    assert rc == 0 and out["removed"] is True

    # Real (unmanaged) profiles are still refused by the native surface.
    rc, out, _ = _run(cfg, ["browser", "close-profile"])
    assert rc == 2 and out["code"] == "BROWSER_CLOSE_REFUSED"


def test_computer_use_screen_arrangement(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    rc, out, _ = _run(cfg, ["computer-use", "screen"])
    assert rc == 3 and out["code"] == "SCREEN_CONFIRM_REQUIRED"

    rc, out, _ = _run(cfg, ["computer-use", "screen", "--panes", "chat,boards",
                            "--layout", "columns", "--confirm"])
    assert rc == 0 and out["schema"] == "qubit-screen/1"
    assert out["panes"] == ["chat", "boards"]

    rc, out, _ = _run(cfg, ["computer-use", "screen", "--panes", "not-a-pane",
                            "--confirm"])
    assert rc == 2 and out["code"] == "SCREEN_BAD_INPUT"


def test_local_models_fallback_chain(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out, _ = _run(cfg, ["local-models", "fallback"])
    assert rc == 0 and out["state"] == "OK"
    assert isinstance(out["chain"], list) and "runtime_observed" in out

    rc, out, _ = _run(cfg, ["local-models", "fallback", "qubit-brain"])
    assert rc in (0, 2)
    assert out["state"] in ("OK", "UNAVAILABLE") and "chain" in out


def test_gateway_supervision_and_discovery(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    rc, out, _ = _run(cfg, ["gateway", "discovery"])
    assert rc == 0 and out["state"] == "OK" and "gateways" in out

    rc, out, _ = _run(cfg, ["gateway", "supervision-request", "gw1", "start"])
    assert rc == 3 and out["code"] == "GW_CONFIRM_REQUIRED"

    rc, out, _ = _run(cfg, ["gateway", "supervision-revert", "gw1"])
    assert rc == 3 and out["code"] == "GW_CONFIRM_REQUIRED"


def test_pairing_setup_code_and_accept(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    rc, out, _ = _run(cfg, ["pairing", "setup-code", "laptop", "chat"])
    assert rc == 3 and out["code"] == "PAIRING_CONFIRM_REQUIRED"

    rc, out, _ = _run(cfg, ["pairing", "setup-code", "laptop", "chat",
                            "--confirm"])
    assert rc == 0 and out["code"] and out["join_url"]
    code = out["code"]

    rc, out, _ = _run(cfg, ["pairing", "accept", code, "--confirm"])
    assert rc == 0 and out.get("setup_accepted") is True

    rc, out, _ = _run(cfg, ["pairing", "accept", "bogus-code", "--confirm"])
    assert rc == 2 and out["code"] in ("PAIRING_BAD_SETUP", "PAIRING_BAD_CODE")


def test_runtime_history_and_confirmed_recovery(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    rc, out, _ = _run(cfg, ["runtime", "history"])
    assert rc == 0 and out["state"] == "OK" and isinstance(out["entries"], list)

    rc, out, _ = _run(cfg, ["runtime", "recover", "opencode-binary"])
    assert rc == 3 and out["code"] == "RUNTIME_CONFIRM_REQUIRED"

    rc, out, _ = _run(cfg, ["runtime", "repair"])
    assert rc == 3 and out["code"] == "RUNTIME_CONFIRM_REQUIRED"

    rc, out, _ = _run(cfg, ["runtime", "discovery"])
    assert rc == 0 and out["state"] in ("OK", "DEGRADED")

    rc, out, _ = _run(cfg, ["runtime", "marketplace"])
    assert rc == 0 and out["state"] == "OK"


def test_egress_status_and_check(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    rc, out, _ = _run(cfg, ["egress", "status"])
    assert rc == 0 and out["schema"] == "qubit-egress-status/1"

    rc, out, _ = _run(cfg, ["egress", "check", "example.com"])
    assert rc == 2 and out["code"] == "EGRESS_DENIED"


def test_hooks_emit_and_typed_bad_payload(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    rc, out, _ = _run(cfg, ["hooks", "emit", "session.start",
                            "--payload", '{"k": "v"}'])
    assert rc == 0 and out["state"] == "OK" and out["shell_executed"] is False
    assert out["payload_keys"] == ["k"]

    rc, out, _ = _run(cfg, ["hooks", "emit", "session.start",
                            "--payload", "not-json"])
    assert rc == 2 and out["code"] == "HOOK_BAD_PAYLOAD"


def test_skills_workshop_collection_layer(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    rc, out, _ = _run(cfg, ["skills", "workshop", "curator-status"])
    assert rc == 0 and out["schema"] == "qubit-skill-workshop/1"

    rc, out, _ = _run(cfg, ["skills", "workshop", "contracts"])
    assert rc == 0 and out["contracts"]

    rc, out, _ = _run(cfg, ["skills", "workshop", "collection"])
    assert rc == 0 and "skills" in out


def test_evaluation_usage_logbook_receipts_telemetry(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    rc, out, _ = _run(cfg, ["usage"])
    assert rc == 0 and out["state"] == "OK" and out["calls"] == 0

    rc, out, _ = _run(cfg, ["logbook", "note", "did work",
                            "--kind", "note"])
    assert rc == 0 and out["entry"]["kind"] == "note"

    rc, out, _ = _run(cfg, ["logbook"])
    assert rc == 0 and out["summary"]["entries"] == 1
    assert out["entries"][0]["summary"] == "did work"

    rc, out, _ = _run(cfg, ["receipts"])
    assert rc == 2 and out["code"] == "RECEIPTS_NOT_FOUND"

    rc, out, _ = _run(cfg, ["usage", "telemetry"])
    assert rc == 0 and out["enabled"] is False


def test_policy_workspace_conformance_and_attestation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    rc, out, _ = _run(cfg, ["policy", "conformance"])
    assert rc in (0, 2) and out["state"] in ("PASS", "FAIL")
    assert "findings" in out

    rc, out, _ = _run(cfg, ["policy", "attest"])
    assert rc in (0, 2) and out["state"] in ("PASS", "FAIL")
    assert out["attestation_id"] and out["immutable"] is True


def test_run_integration_ignores_native_commands(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    class _Args:
        browser_command = "close-profile"
        experiments_command = "status"

    assert CLI._run_integration(cfg, "browser", _Args()) is None
    assert CLI._run_integration(cfg, "experiments", _Args()) is None
    assert CLI._run_integration(cfg, "status", _Args()) is None
