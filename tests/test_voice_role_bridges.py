"""Hermetic verification of the eight agent-role voice bridges.

The audit and every execution here are offline: no provider/model call, no
network, no service. A fake backend proves the bridge calls the real runner,
and a typed refusal is reported as UNAVAILABLE rather than spoken success.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "verify_role_voice.py"

REQUIRED = (
    "orchestrator",
    "react",
    "operative",
    "openhands",
    "simple",
    "researcher",
    "digest",
    "monitor",
)
MUTATING = {"orchestrator", "react", "operative", "openhands"}
NON_MUTATING = set(REQUIRED) - MUTATING


def _load_verify():
    spec = importlib.util.spec_from_file_location(
        "verify_role_voice", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


verify = _load_verify()


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    path = tmp_path / "voice.yaml"
    path.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(path)


@pytest.fixture
def vc():
    from voicebridge import role_bridges
    from voicebridge import voice_commands as VC

    entries = dict(VC.CLI_BRIDGE_REGISTRY)
    backends = dict(VC._BACKENDS)
    summaries = dict(VC._SUMMARIES)
    yield VC, role_bridges
    VC.CLI_BRIDGE_REGISTRY.clear()
    VC.CLI_BRIDGE_REGISTRY.update(entries)
    VC._BACKENDS.clear()
    VC._BACKENDS.update(backends)
    VC._SUMMARIES.clear()
    VC._SUMMARIES.update(summaries)


def _role(role_id: str, *, aliases=(), confirmation="auto", recipe_id=""):
    from roles.base import AgentRole

    return AgentRole(
        id=role_id,
        title=role_id.title(),
        description=f"Qubit-native {role_id} verification posture.",
        system_prompt="" if recipe_id else f"Act as {role_id}.",
        confirmation=confirmation,
        voice_aliases=tuple(aliases) or (f"run the {role_id} role",),
        recipe_id=recipe_id,
        source=f"test:{role_id}",
    )


def test_all_eight_roles_are_reachable(vc):
    VC, _ = vc
    report = verify.audit(VC)

    assert report["exit_code"] == 0, report["gaps"]
    assert report["covered"] == "8/8"
    assert report["missing"] == []
    assert report["duplicate_aliases"] == {}
    by_id = {row["id"]: row for row in report["roles"]}
    assert set(by_id) == set(REQUIRED)
    for role_id in REQUIRED:
        assert by_id[role_id]["reachable"], by_id[role_id]
        assert by_id[role_id]["confirmation_ok"], by_id[role_id]
        assert by_id[role_id]["aliases"], role_id


def test_confirmation_posture_matches_mutation_risk(vc):
    VC, _ = vc
    report = verify.audit(VC)
    by_id = {row["id"]: row for row in report["roles"]}

    for role_id in MUTATING:
        row = by_id[role_id]
        assert row["mutating"] is True
        assert row["expects_confirmation"] is True
        if row["role_entry"]:
            assert VC.CLI_BRIDGE_REGISTRY[row["role_entry"]].confirm is True

    for role_id in NON_MUTATING:
        row = by_id[role_id]
        assert row["mutating"] is False
        assert row["expects_confirmation"] is False
        if row["role_entry"]:
            assert VC.CLI_BRIDGE_REGISTRY[row["role_entry"]].confirm is False
        if row["recipe_entry"]:
            assert VC.CLI_BRIDGE_REGISTRY[row["recipe_entry"]].confirm is False

    for role_id in MUTATING:
        owned = [a for a in by_id[role_id]["aliases"]
                 if a["owner_kind"] == "role"]
        assert owned, role_id
        assert all(a["confirm"] is True for a in owned), role_id


def test_digest_and_monitor_aliases_stay_with_their_recipe_bridges(vc):
    VC, _ = vc
    report = verify.audit(VC)
    by_id = {row["id"]: row for row in report["roles"]}

    for role_id in ("digest", "monitor"):
        row = by_id[role_id]
        assert row["recipe_id"]
        assert row["recipe_entry"]
        assert any(a["owner_kind"] == "recipe" for a in row["aliases"])
        for alias in row["aliases"]:
            assert alias["owner_kind"] in ("role", "recipe"), alias
            if alias["owner_kind"] == "recipe":
                assert alias["recipe"] == row["recipe_id"], alias
                assert alias["owner"] == row["recipe_entry"], alias
                assert alias["backend"] == "recipe", alias
                assert alias["confirm"] is False, alias

    monitor = by_id["monitor"]
    assert monitor["aliases"]
    assert all(alias["owner_kind"] == "recipe"
               for alias in monitor["aliases"])
    assert all(alias["owner"] == "recipe_monitor_operative"
               for alias in monitor["aliases"])


def test_fake_alias_executes_the_runner_and_refusal_is_unavailable(
        vc, tmp_path, monkeypatch):
    VC, role_bridges = vc
    role = _role("voiceprobe", aliases=("run the voiceprobe role",))
    role_bridges.install(VC, roles=[role])
    monkeypatch.setattr("roles.registry.get_role", lambda _id: role)

    called = {}

    def fake_run(role_id, prompt, *, engine):
        called.update(role=role_id, prompt=prompt, engine=engine)
        return {
            "ok": True,
            "role": role_id,
            "engine": engine,
            "text": "Voice probe answer.",
            "model": "m",
            "provider": "p",
            "elapsed_ms": 1.0,
        }

    monkeypatch.setattr("roles.runner.run_role", fake_run)
    ctx = VC.make_ctx(_cfg(tmp_path, monkeypatch), session="roles",
                      source="cli")

    resolution = VC.resolve("qubit, run the voiceprobe role")
    assert resolution.kind == VC.MATCH
    assert resolution.entry.id == "role_voiceprobe"
    outcome = VC.execute(ctx, resolution)

    assert outcome.kind == VC.EXECUTED, (outcome.kind, outcome.speech)
    assert "Voice probe answer." in outcome.speech
    assert outcome.receipt["result"]["agent_role"]["state"] == "OK"
    assert called == {
        "role": "voiceprobe",
        "prompt": "",
        "engine": "native",
    }

    def refused_run(role_id, prompt, *, engine):
        return {
            "ok": False,
            "role": role_id,
            "engine": engine,
            "text": "",
            "refusal": "role_engine_unsupported",
            "hint": "Use the native engine.",
            "detail": "engine cannot honour a role",
            "elapsed_ms": 0.5,
        }

    monkeypatch.setattr("roles.runner.run_role", refused_run)
    refused = VC.execute(ctx, VC.resolve("qubit, run the voiceprobe role"))

    assert refused.kind == VC.EXECUTED, (refused.kind, refused.speech)
    body = refused.receipt["result"]["agent_role"]
    assert body["state"] == "UNAVAILABLE"
    assert body["code"] == "role_engine_unsupported"
    assert "unavailable" in refused.speech.lower()
    assert "returned no text" not in refused.speech.lower()


def test_conflicting_alias_fixture_fails_the_audit(vc):
    VC, role_bridges = vc
    shared = "shared phantom phrase"
    alpha = _role("alpha", aliases=(shared,))
    beta = _role("beta", aliases=(shared,))
    role_bridges.install(VC, roles=[alpha, beta])

    report = verify.audit(VC, roles=[alpha, beta])

    assert report["exit_code"] == 1, report
    by_id = {row["id"]: row for row in report["roles"]}
    assert by_id["alpha"]["reachable"] is True
    assert by_id["beta"]["reachable"] is False
    assert any("another role bridge" in gap for gap in report["gaps"]), (
        report["gaps"])


def test_duplicate_alias_fixture_fails_the_audit(vc):
    VC, _ = vc
    from voicebridge.voice_commands import VoiceCommand

    VC.CLI_BRIDGE_REGISTRY["fixture_duplicate"] = VoiceCommand(
        id="fixture_duplicate",
        title="Duplicate fixture",
        aliases=("morning digest",),
        backend="recipe",
        fixed=(("recipe", "deep_research"),),
    )

    report = verify.audit(VC)

    assert report["exit_code"] == 1
    assert "morning digest" in report["duplicate_aliases"]
    assert any("duplicate alias" in gap for gap in report["gaps"])


def test_default_audit_never_calls_the_runner(vc, monkeypatch):
    VC, _ = vc

    def explode(*_args, **_kwargs):
        raise AssertionError("the offline audit called the real runner")

    monkeypatch.setattr("roles.runner.run_role", explode)
    report = verify.audit(VC)

    assert report["exit_code"] == 0, report["gaps"]


def test_execute_is_explicit_and_json_reports_success(
        vc, monkeypatch, capsys):
    VC, _ = vc
    calls = []

    def fake_run(role_id, prompt, *, engine):
        calls.append((role_id, prompt, engine))
        return {
            "ok": True,
            "role": role_id,
            "engine": engine,
            "text": "ROLE_OK",
            "model": "m",
            "provider": "p",
            "elapsed_ms": 1.0,
        }

    monkeypatch.setattr("roles.runner.run_role", fake_run)

    assert verify.main(["--json"]) == 0
    assert calls == []
    capsys.readouterr()

    code = verify.main(
        ["--execute", "simple", "--prompt", "say OK", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert calls == [("simple", "say OK", "native")]
    assert payload["role"] == "simple"
    assert payload["ok"] is True
    assert payload["result"]["text"] == "ROLE_OK"
