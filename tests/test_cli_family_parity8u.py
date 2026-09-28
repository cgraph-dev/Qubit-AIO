"""Packet 8U — real top-level CLI families over the EXISTING Qubit backends.

`model`, `config`, `doctor`, and `cron` are thin top-level parser families that
delegate to the canonical backends already used by ``profile model``, ``flags``,
``package``, and ``schedules`` — one store, never a demo route or a second
store. These tests walk the live ``build_parser()`` tree and execute the
production entrypoint (``voicebridge.cli.main``) against a hermetic tmp
config/state: no network and no live services are touched.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import cli as CLI  # noqa: E402

# Family -> the subcommands the family must expose.
FAMILIES = {
    "model": {"list", "show", "test", "create", "clone", "edit", "activate",
              "deactivate", "rollback", "remove"},
    "config": {"path", "show", "check", "env-path", "env-list", "set",
               "unset"},
    "doctor": {"run", "check", "list", "summary"},
    "cron": {"list", "add", "show", "remove", "enable", "disable", "run",
             "status", "runs"},
}


def _cfg(tmp_path, monkeypatch) -> Path:
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
                "XDG_CACHE_HOME"):
        d = tmp_path / var.lower()
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(d))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / ".hermes"))
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


def _top_level():
    parser = CLI.build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


def _subcommands(family):
    parser = CLI.build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            sp = action.choices.get(family)
            if sp is None:
                return set()
            for a in sp._actions:
                if isinstance(a, argparse._SubParsersAction):
                    return set(a.choices)
    return set()


# --------------------------------------------------------- parser structure
@pytest.mark.parametrize("family,subs", sorted(FAMILIES.items()))
def test_family_and_subcommands_present_in_parser(family, subs):
    assert family in _top_level(), family
    assert subs <= _subcommands(family), (family, sorted(subs - _subcommands(family)))


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_family_help_mentions_family_and_subcommands(family):
    buf = io.StringIO()
    with pytest.raises(SystemExit) as exc:
        with redirect_stdout(buf):
            CLI.main([family, "--help"])
    assert exc.value.code == 0
    text = buf.getvalue()
    assert family in text, text
    for sub in sorted(FAMILIES[family]):
        assert sub in text, (family, sub, text)


def test_top_level_help_lists_new_families():
    buf = io.StringIO()
    with pytest.raises(SystemExit) as exc:
        with redirect_stdout(buf):
            CLI.main(["--help"])
    assert exc.value.code == 0
    text = buf.getvalue()
    for family in FAMILIES:
        assert family in text, family


def test_new_families_are_native_not_fork_forwarded():
    from voicebridge import cli_qubit_forward as HF

    for family in FAMILIES:
        assert HF.route([family, "__nope__"]) is None, family


def test_new_families_declared_in_cli_coverage():
    from voicebridge import voice_commands as VC

    families = _top_level()
    assert set(VC.CLI_COVERAGE) == families, \
        sorted(set(families) ^ set(VC.CLI_COVERAGE))
    for family in FAMILIES:
        assert family in VC.CLI_COVERAGE
        for target in VC.CLI_COVERAGE[family]:
            assert target in VC.REGISTRY, (family, target)


# ----------------------------------------------------------- model backend
def test_model_list_reaches_resolver(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out, body = _run(cfg, ["model", "list", "--json"])
    assert rc == 0, body
    assert out["count"] >= 1 and out["profiles"], body
    assert any(p["default"] for p in out["profiles"]), body


def test_model_matches_profile_model_backend(tmp_path, monkeypatch):
    """`model list` and `profile model list` name the same resolver store."""
    cfg = _cfg(tmp_path, monkeypatch)
    rc1, a, _ = _run(cfg, ["model", "list", "--json"])
    rc2, b, _ = _run(cfg, ["profile", "model", "list", "--json"])
    assert rc1 == 0 and rc2 == 0
    assert {p["id"] for p in a["profiles"]} == {p["id"] for p in b["profiles"]}


# ---------------------------------------------------------- config backend
def test_config_reads_reach_backend(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out, body = _run(cfg, ["config", "path"])
    assert rc == 0 and out["state"] == "OK"
    assert out["path"].endswith("voice.yaml")

    rc, out, body = _run(cfg, ["config", "check"])
    assert rc == 0 and out["state"] == "OK"
    assert "logging" in out["sections"]

    rc, out, body = _run(cfg, ["config", "env-list", "--json"])
    assert rc == 0 and out["count"] == 0 and out["names"] == []


def test_config_show_masks_and_reads_effective_config(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, _out, body = _run(cfg, ["config", "show"])
    assert rc == 0, body
    assert "logging:" in body


def test_config_set_persists_reference_and_refuses_secret(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out, body = _run(
        cfg, ["config", "set", "OPENAI_API_KEY", "ref:openai_key", "--confirm"])
    assert rc == 0 and out["state"] == "OK", body

    from voicebridge import env_store as ES
    from voicebridge.config import Config

    assert ES.load(Config.load(cfg))["OPENAI_API_KEY"] == "ref:openai_key"

    rc, out, body = _run(cfg, ["config", "env-list", "--json"])
    assert "OPENAI_API_KEY" in out["names"]

    # A raw secret value is never accepted by the store.
    rc, out, body = _run(
        cfg, ["config", "set", "BAD", "sk-live-secret", "--confirm"])
    assert rc == 2 and out["code"] == "CONFIG_REF_INVALID", body
    assert "sk-live-secret" not in ES.load(Config.load(cfg)).values()

    # Writes are confirm-gated.
    rc, out, body = _run(cfg, ["config", "set", "BAZ", "ref:z"])
    assert rc == 3 and out["code"] == "CONFIRM_REQUIRED", body


# ---------------------------------------------------------- doctor backend
def test_doctor_reaches_catalog_and_host_checks(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out, body = _run(cfg, ["doctor", "summary"])
    assert rc == 0 and out["total"] > 0, body

    rc, out, body = _run(cfg, ["doctor", "check", "status"])
    assert rc == 0 and out["state"] in ("IMPLEMENTED", "NOT_UPSTREAM"), body

    rc, out, body = _run(cfg, ["doctor", "check", "definitely-not-a-capability"])
    assert rc == 2 and out["code"] == "UNKNOWN_CAPABILITY", body

    rc, out, body = _run(cfg, ["doctor", "list", "--state", "IMPLEMENTED",
                               "--json"])
    assert rc == 0 and isinstance(out, list) and out, body


def test_doctor_run_reports_real_host_checks(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out, _body = _run(cfg, ["doctor", "run", "--json"])
    assert rc in (0, 1), rc
    assert isinstance(out, list) and out
    for check in out:
        assert {"name", "state", "detail"} <= set(check)


# ------------------------------------------------------------ cron backend
def test_cron_reaches_the_schedules_store(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out, body = _run(cfg, ["cron", "list"])
    assert rc == 0 and out["jobs"] == [], body

    # A mutation without --confirm is a typed refusal, not a write.
    rc, out, body = _run(
        cfg, ["cron", "add", "nightly", "summarize notes",
              "--when", "every 1 hours"])
    assert rc == 3 and out["code"] == "SCHEDULES_CONFIRM_REQUIRED", body

    rc, out, body = _run(
        cfg, ["cron", "add", "nightly", "summarize notes",
              "--when", "every 1 hours", "--confirm"])
    assert rc == 0 and out["job"]["name"] == "nightly", body
    jid = int(out["job"]["id"])

    rc, out, body = _run(cfg, ["cron", "status"])
    assert rc == 0 and any(j["id"] == jid for j in out["jobs"]), body

    rc, out, body = _run(cfg, ["cron", "runs"])
    assert rc == 0 and "runs" in out, body

    rc, out, body = _run(cfg, ["cron", "disable", str(jid), "--confirm"])
    assert rc == 0, body
    rc, out, body = _run(cfg, ["cron", "enable", str(jid), "--confirm"])
    assert rc == 0, body

    rc, out, body = _run(cfg, ["cron", "remove", str(jid), "--confirm"])
    assert rc == 0 and out["deleted"] == jid, body


def test_cron_shares_the_schedules_store(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out, _ = _run(cfg, ["cron", "add", "shared", "do it",
                            "--when", "every 2 hours", "--confirm"])
    assert rc == 0
    rc, out, _ = _run(cfg, ["schedules", "list"])
    assert rc == 0
    assert any(j["name"] == "shared" for j in out["jobs"]), out


# ------------------------------------------------------ unknown subcommand
@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_unknown_subcommand_fails_typed(tmp_path, monkeypatch, family):
    cfg = _cfg(tmp_path, monkeypatch)
    err = io.StringIO()
    with pytest.raises(SystemExit) as exc:
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            CLI.main([family, "__definitely_not_a_subcommand__",
                      "--config", str(cfg)])
    assert exc.value.code == 2, (family, exc.value.code)
    assert "invalid choice" in err.getvalue(), (family, err.getvalue())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
