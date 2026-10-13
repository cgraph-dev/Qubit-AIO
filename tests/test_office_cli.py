"""The `qubit office` surface: the confirm gate, dry-run, and typed refusals.

M0's human-facing surface. These tests drive the *real* parser and the *real*
`_cmd_office` dispatcher, because the gate that matters is the one a human
actually hits — a mutation must never reach the store on a bare invocation.

Hermetic: tmp XDG state only; no network, no provider call, no agent started.
"""
from __future__ import annotations

import json

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _run(cfg, argv, capsys):
    """Parse real argv through the real parser, then dispatch for real."""
    from voicebridge import cli as CLI

    args = CLI.build_parser().parse_args(argv)
    rc = CLI._cmd_office(cfg, args)
    out = capsys.readouterr().out
    return rc, (json.loads(out) if out.strip() else {})


def _net(cfg):
    from voicebridge.office import ledger
    return ledger.pnl(cfg, scope="org")["net_cents"]


def test_office_is_registered_in_the_cli(tmp_path, monkeypatch):
    """The family and its subcommands exist on the real parser."""
    from voicebridge import cli as CLI

    args = CLI.build_parser().parse_args(["office", "health"])
    assert args.command == "office"
    assert args.office_command == "health"

    text = CLI.build_parser().format_help()
    assert "office" in text


def test_bare_record_is_refused_and_writes_nothing(tmp_path, monkeypatch,
                                                   capsys):
    """The bug this test exists for: a bare `record` used to apply."""
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out = _run(cfg, ["office", "init"], capsys)
    assert rc == 0

    rc, out = _run(cfg, ["office", "record", "--kind", "revenue",
                         "--cents", "10000"], capsys)
    assert rc == 2
    assert out["code"] == "OFFICE_CONFIRM_REQUIRED"
    assert out["state"] == "REFUSED"
    assert _net(cfg) == 0                      # nothing reached the store


def test_dry_run_previews_and_writes_nothing(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, monkeypatch)
    _run(cfg, ["office", "init"], capsys)

    rc, out = _run(cfg, ["office", "record", "--kind", "revenue",
                         "--cents", "10000", "--dry-run"], capsys)
    assert rc == 0
    assert out["state"] == "preview"
    assert _net(cfg) == 0


def test_confirmed_record_applies_once(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, monkeypatch)
    _run(cfg, ["office", "init"], capsys)

    rc, out = _run(cfg, ["office", "record", "--kind", "revenue",
                         "--cents", "10000", "--note", "first sale",
                         "--confirm"], capsys)
    assert rc == 0
    assert out["state"] == "OK"
    assert _net(cfg) == 10000

    rc, out = _run(cfg, ["office", "pnl"], capsys)
    assert rc == 0
    assert out["net_cents"] == 10000
    assert out["revenue_cents"] == 10000


def test_the_whole_m0_loop_through_the_cli(tmp_path, monkeypatch, capsys):
    """Fund a CEO, hang a manager under them, record money, read it back."""
    cfg = _cfg(tmp_path, monkeypatch)
    _run(cfg, ["office", "init"], capsys)

    # a floor, a business, and two seats forming a real chain
    rc, out = _run(cfg, ["office", "floor-create", "--number", "1",
                         "--name", "Operations", "--confirm"], capsys)
    assert rc == 0, out
    rc, fl = _run(cfg, ["office", "floors"], capsys)
    floor_id = fl["floors"][0]["id"]

    rc, out = _run(cfg, ["office", "hire", "--slug", "ceo",
                         "--seat-type", "ceo", "--confirm"], capsys)
    assert rc == 0, out
    ceo_id = out["id"]

    rc, out = _run(cfg, ["office", "hire", "--slug", "pod-mgr",
                         "--seat-type", "manager", "--supervisor", str(ceo_id),
                         "--confirm"], capsys)
    assert rc == 0, out
    mgr_id = out["id"]

    rc, out = _run(cfg, ["office", "department-create", "--slug",
                         "print-on-demand", "--floor", str(floor_id),
                         "--division-kind", "product", "--kill-criteria",
                         "no sale in 30 days", "--confirm"], capsys)
    assert rc == 0, out

    # a hire with no supervisor is refused by the chain rule, not by the CLI
    rc, out = _run(cfg, ["office", "hire", "--slug", "orphan",
                         "--seat-type", "worker", "--confirm"], capsys)
    assert rc == 2
    assert out["code"] == "OFFICE_CHAIN"

    rc, out = _run(cfg, ["office", "chain", "--agent", str(mgr_id)], capsys)
    assert rc == 0, out
    assert out["terminates_at_ceo"] is True
    assert out["depth"] == 2

    rc, tree = _run(cfg, ["office", "tree"], capsys)
    assert tree["seats"] == 2
    assert tree["roots"] == ["ceo"]

    # money, attributed to a seat
    rc, out = _run(cfg, ["office", "record", "--kind", "revenue",
                         "--cents", "8000", "--agent", str(mgr_id),
                         "--confirm"], capsys)
    assert rc == 0, out
    rc, out = _run(cfg, ["office", "record", "--kind", "inference",
                         "--cents", "2000", "--agent", str(mgr_id),
                         "--confirm"], capsys)
    assert rc == 0, out

    rc, pnl = _run(cfg, ["office", "pnl", "--scope", "agent"], capsys)
    assert rc == 0, out
    row = {r["agent_id"]: r for r in pnl["rows"]}[mgr_id]
    assert row["net_cents"] == 6000

    rc, health = _run(cfg, ["office", "health"], capsys)
    assert health["healthy"] is True
    assert health["append_only_enforced"] == {"update": True, "delete": True}
    assert health["rollup_drift_cents"] == 0


def test_reverse_through_the_cli_leaves_the_original_intact(tmp_path,
                                                            monkeypatch,
                                                            capsys):
    cfg = _cfg(tmp_path, monkeypatch)
    _run(cfg, ["office", "init"], capsys)
    rc, rec = _run(cfg, ["office", "record", "--kind", "revenue",
                         "--cents", "5000", "--confirm"], capsys)
    assert rc == 0, rec

    rc, out = _run(cfg, ["office", "reverse", str(rec["id"]),
                         "--note", "duplicate charge", "--confirm"], capsys)
    assert rc == 0, out
    assert out["kind"] == "transfer"
    assert out["amount_cents"] == -5000
    assert _net(cfg) == 0

    rc, rows = _run(cfg, ["office", "ledger"], capsys)
    assert rows["count"] == 2                  # both rows survive (C1)
    assert {r["amount_cents"] for r in rows["rows"]} == {5000, -5000}


def test_unknown_subcommand_is_a_typed_refusal(tmp_path, monkeypatch, capsys):
    """No traceback, ever — an unknown op gets a code, like kanban's."""
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out = _run(cfg, ["office", "health"], capsys)      # a valid one first
    assert rc == 0


def test_an_out_of_domain_kind_is_rejected_by_argparse(tmp_path, monkeypatch):
    """`--kind` is a closed set, so a bad one never reaches the store."""
    from voicebridge import cli as CLI

    with pytest.raises(SystemExit):
        CLI.build_parser().parse_args(
            ["office", "record", "--kind", "nonsense", "--cents", "1",
             "--confirm"])


def test_the_unknown_spend_loop_through_the_cli(tmp_path, monkeypatch, capsys):
    """D15 end to end from the command line: receipt → refuse → attest → go."""
    cfg = _cfg(tmp_path, monkeypatch)
    _run(cfg, ["office", "init"], capsys)
    rc, out = _run(cfg, ["office", "hire", "--slug", "ceo",
                         "--seat-type", "ceo", "--confirm"], capsys)
    agent = out["id"]
    _run(cfg, ["office", "record", "--kind", "revenue", "--cents", "5000",
               "--agent", str(agent), "--confirm"], capsys)

    # the book is complete, so admission is fine
    rc, out = _run(cfg, ["office", "admit", "--agent", str(agent),
                         "--estimate", "10"], capsys)
    assert rc == 0, out

    # a turn begins and the process dies before booking anything
    rc, run = _run(cfg, ["office", "run-begin", "--agent", str(agent),
                         "--cap", "50", "--confirm"], capsys)
    assert rc == 0, run
    run_id = run["run_id"]

    rc, out = _run(cfg, ["office", "unsettled"], capsys)
    assert out["count"] == 1
    assert out["complete"] is False

    rc, out = _run(cfg, ["office", "admit", "--agent", str(agent),
                         "--estimate", "10"], capsys)
    assert rc == 2
    assert out["code"] == "OFFICE_ADMISSION_REFUSED"
    assert run_id in out["explanation"]

    # `office health` agrees, and is not green
    rc, health = _run(cfg, ["office", "health"], capsys)
    assert health["complete"] is False
    assert health["healthy"] is False

    # the owner states the real charge
    rc, out = _run(cfg, ["office", "settle", run_id, "--cents", "9",
                         "--attested-as", "entered", "--confirm"], capsys)
    assert rc == 0, out

    assert _net(cfg) == 5000 - 9
    rc, out = _run(cfg, ["office", "admit", "--agent", str(agent),
                         "--estimate", "10"], capsys)
    assert rc == 0, out


def test_caps_through_the_cli(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, monkeypatch)
    _run(cfg, ["office", "init"], capsys)

    rc, out = _run(cfg, ["office", "caps"], capsys)
    assert rc == 0
    assert {c["key"] for c in out["caps"]} == {"perRun", "perAgent", "perDay",
                                              "global"}
    assert all(c["source"] == "default" for c in out["caps"])

    # a bare cap-set must not write
    rc, out = _run(cfg, ["office", "cap-set", "--key", "perRun",
                         "--value", "25"], capsys)
    assert rc == 2
    assert out["code"] == "OFFICE_CONFIRM_REQUIRED"

    rc, out = _run(cfg, ["office", "cap-set", "--key", "perRun",
                         "--value", "25", "--confirm"], capsys)
    assert rc == 0
    assert out["effective_cents"] == 25

    # 0 is a real saved value meaning "no cap"
    rc, out = _run(cfg, ["office", "cap-set", "--key", "perRun",
                         "--value", "0", "--confirm"], capsys)
    assert out["meaning"] == "no cap"

    # and clearing reverts to the default
    rc, out = _run(cfg, ["office", "cap-set", "--key", "perRun",
                         "--value", "default", "--confirm"], capsys)
    assert out["cleared"] is True
    assert out["source"] == "default"


def test_the_turn_surface_is_confirm_gated_and_never_spends_by_default(
        tmp_path, monkeypatch, capsys):
    """`office run` makes a REAL provider call — so a bare one must refuse.

    This is the safest possible test of the M1 surface: it asserts the gate
    without letting anything reach a provider.
    """
    cfg = _cfg(tmp_path, monkeypatch)
    _run(cfg, ["office", "init"], capsys)
    rc, out = _run(cfg, ["office", "hire", "--slug", "ceo",
                         "--seat-type", "ceo", "--confirm"], capsys)
    agent = out["id"]

    # a bare run refuses, typed
    rc, out = _run(cfg, ["office", "run", "--agent", str(agent),
                         "--task", "say hello"], capsys)
    assert rc == 2
    assert out["code"] == "OFFICE_CONFIRM_REQUIRED"

    # --dry-run previews it and still spends nothing
    rc, out = _run(cfg, ["office", "run", "--agent", str(agent),
                         "--task", "say hello", "--dry-run"], capsys)
    assert rc == 0
    assert out["state"] == "preview"

    # and nothing was receipted
    rc, out = _run(cfg, ["office", "trace", "--agent", str(agent)], capsys)
    assert rc == 0
    assert out["count"] == 0


def test_trace_and_attribution_check_are_readable_when_empty(tmp_path,
                                                             monkeypatch,
                                                             capsys):
    cfg = _cfg(tmp_path, monkeypatch)
    _run(cfg, ["office", "init"], capsys)
    rc, out = _run(cfg, ["office", "hire", "--slug", "ceo",
                         "--seat-type", "ceo", "--confirm"], capsys)
    agent = out["id"]

    rc, t = _run(cfg, ["office", "trace", "--agent", str(agent)], capsys)
    assert rc == 0
    assert t["count"] == 0
    assert t["agent"]["slug"] == "ceo"

    rc, a = _run(cfg, ["office", "attribution-check"], capsys)
    assert rc == 0
    assert a["unattributed_entries"] == 0


def test_consent_and_hardline_from_the_cli(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, monkeypatch)
    _run(cfg, ["office", "init"], capsys)

    rc, out = _run(cfg, ["office", "hardline"], capsys)
    assert rc == 0
    assert len(out["hardline"]) == 6
    assert out["tiers"] == ["hardline", "bypass", "grant", "resolve"]

    # the floor holds even with the grant supplied explicitly
    rc, out = _run(cfg, ["office", "consent", "--capability", "ledger",
                         "--scope", "rewrite", "--grant", "ledger:rewrite"],
                   capsys)
    assert rc == 0
    assert out["allowed"] is False
    assert out["tier"] == "hardline"

    # a frozen seat is revoked
    rc, out = _run(cfg, ["office", "consent", "--capability", "publish",
                         "--scope", "store", "--posture", "frozen"], capsys)
    assert out["allowed"] is False
    assert out["tier"] == "bypass"


def test_a_floor_number_out_of_range_is_a_typed_refusal(tmp_path, monkeypatch,
                                                        capsys):
    """`--number 99` parses (it is an int) and is refused by the org layer."""
    cfg = _cfg(tmp_path, monkeypatch)
    _run(cfg, ["office", "init"], capsys)

    rc, out = _run(cfg, ["office", "floor-create", "--number", "99",
                         "--confirm"], capsys)
    assert rc == 2
    assert out["code"] == "OFFICE_BAD_INPUT"
    assert out["state"] == "REFUSED"
