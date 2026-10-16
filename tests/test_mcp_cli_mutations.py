"""CLI MCP mutations use the existing guarded canonical authority, temp homes only."""
import json

import pytest

from voicebridge import cli, config, mcp_servers as MS, qubit_compat as HC


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "QUBIT_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    path = tmp_path / "voice.yaml"
    path.write_text("logging:\n  jsonl: false\n")
    cfg = config.Config.load(path)
    MS.upsert(cfg, "alpha", {"transport": "stdio", "command": "alpha-mcp",
                             "env": {"TOKEN": "env:ALPHA_TOKEN"}})
    MS.set_enabled(cfg, "alpha", True)
    return cfg


def run(cfg, capsys, *tokens):
    args = cli.build_parser().parse_args(["mcp", *tokens])
    rc = cli._cmd_mcp_mutate(cfg, args)
    return rc, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("verb", ["remove", "rm", "delete", "disable"])
def test_confirmed_mutations(cfg, capsys, verb):
    rc, out = run(cfg, capsys, verb, "alpha", "--confirm")
    assert rc == 0 and out["ok"]
    records = MS.list_servers(cfg)["servers"]
    if verb == "disable":
        assert len(records) == 1 and records[0]["enabled"] is False
        assert records[0]["env"] == {"TOKEN": "env:ALPHA_TOKEN"}
    else:
        assert records == []
    runtime = MS._load_runtime_config(cfg).get("mcp_servers", {})
    if verb == "disable":
        assert runtime["alpha"]["enabled"] is False
    else:
        assert "alpha" not in runtime


@pytest.mark.parametrize("verb", ["remove", "disable"])
def test_unconfirmed_stages_without_mutation(cfg, capsys, verb):
    before = MS._load(cfg)
    rc, out = run(cfg, capsys, verb, "alpha")
    assert rc == 1 and out["confirmation_id"]
    assert MS._load(cfg) == before
    rc, out = run(cfg, capsys, verb, "alpha", "--confirmation-id", out["confirmation_id"])
    assert rc == 0 and out["ok"]


@pytest.mark.parametrize("verb", ["remove", "disable"])
def test_dry_run_never_enters_mutation_authority(cfg, capsys, monkeypatch, verb):
    def forbidden(*a, **kw):
        pytest.fail("dry run reached mutation handler")
    monkeypatch.setattr(HC, "mcp_delete", forbidden)
    monkeypatch.setattr(HC, "mcp_set_enabled", forbidden)
    assert run(cfg, capsys, verb, "alpha", "--dry-run", "--confirm")[0] == 0
    assert MS.list_servers(cfg)["servers"][0]["enabled"] is True


@pytest.mark.parametrize("verb", ["remove", "disable"])
def test_estop_precedes_confirmation(cfg, capsys, monkeypatch, verb):
    before = MS._load(cfg)
    monkeypatch.setattr(HC, "_estop", lambda cfg: True)
    rc, out = run(cfg, capsys, verb, "alpha", "--confirm")
    assert rc == 1 and out["code"] == "EMERGENCY_STOP"
    assert MS._load(cfg) == before


def test_missing_disable_is_typed(cfg, capsys):
    rc, out = run(cfg, capsys, "disable", "absent", "--confirm")
    assert rc == 1 and out["code"] == "MCP_NOT_FOUND"
