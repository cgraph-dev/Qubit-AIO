"""QBPV2 Agent 4 — CLI / dashboard / voice tri-parity regression suite.

This wave's verification found one real cross-surface defect and confirmed the
adjacent invariants the task asked to prove:

1. **git log default/argument parity.** ``_b_git`` defaulted the commit-log
   limit to 5 while the live CLI parser (``qubit git log --limit``) and
   ``git_scope.log`` both default to 10, and the registry entry exposed no
   ``limit`` argument at all. The dashboard and voice therefore returned a
   *different, shorter* result set than the CLI, and neither could select a
   limit. This is the same class of drift an earlier wave fixed for
   ``sessions`` / ``kanban_list`` / ``kanban_log``.

2. **No dead voice slots / orphan CLI coverage / fabricated catalog surface.**

3. **One shared confirmation journal** (never a private policy authority) and a
   **transport-neutral gateway RPC registry** that opens no listener.

4. **OpenClaw ledger honesty**: terminal SAFE_EQUIVALENT / OPERATOR_ONLY rows
   keep a real proof and a live need, and the OC-68 voice-surface row reflects
   the live registry instead of a frozen count.

Only the real production callers are exercised (``cli.main``,
``dashboard_ops.run_op``, ``voice_commands.voice_say``, ``gateway.rpc``), not
mocks.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import inspect
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER = REPO_ROOT / "docs" / "QUBIT_GAP_LEDGER.openclaw.json"
GEN = REPO_ROOT / "scripts" / "rebuild_ledger_openclaw.py"


# ----------------------------------------------------------------- helpers
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    from voicebridge.config import Config

    return p, Config.load(p)


def _cli(path, *argv):
    from voicebridge import cli

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = cli.main([*argv, "--config", str(path)])
        except SystemExit as exc:
            rc = getattr(exc, "code", 0) or 0
    text = buf.getvalue().strip()
    try:
        return rc, json.loads(text) if text else None
    except ValueError:
        return rc, None


def _dash(cfg, op_id, args=None, session="agent4"):
    from voicebridge import dashboard_ops as OPS

    return OPS.run_op(cfg, op_id, dict(args or {}), actor="dashboard:user",
                      session=session)


def _say(cfg, text, session="agent4"):
    from voicebridge import voice_commands as VC

    return VC.voice_say(cfg, text, actor="voice:user", session=session,
                        source="user")


def _parser_default(family, sub, flag):
    from voicebridge.cli import build_parser

    def descend(node, name):
        for action in node._actions:
            if isinstance(action, argparse._SubParsersAction) \
                    and name in action.choices:
                return action.choices[name]
        raise AssertionError(f"no subparser {name!r}")

    node = descend(build_parser(), family)
    if sub is not None:
        node = descend(node, sub)
    for action in node._actions:
        if flag in action.option_strings:
            return action.default
    raise AssertionError((family, sub, flag))


def _make_repo(tmp_path, commits=8):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo,
                   check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    for i in range(commits):
        (repo / f"f{i}.txt").write_text(str(i))
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", f"commit {i}"], cwd=repo,
                       check=True)
    return repo


def _activate(cfg, repo):
    from voicebridge import projects as PJ

    assert PJ.create(cfg, "agent4-demo", folders=[str(repo)],
                     requested_by="cli:user", confirmed=True)["state"] == "OK"
    assert PJ.use(cfg, "agent4-demo", requested_by="cli:user",
                  confirmed=True)["state"] == "OK"


# ------------------------------------------------ git log default parity
def test_git_log_default_is_shared_by_all_three_surfaces():
    """The CLI parser, ``git_scope.log``, and the voice constant must agree."""
    from voicebridge import git_scope as GI
    from voicebridge import voice_commands as VC

    assert VC.GIT_LOG_DEFAULT == 10
    assert _parser_default("git", "log", "--limit") == VC.GIT_LOG_DEFAULT
    assert inspect.signature(GI.log).parameters["limit"].default \
        == VC.GIT_LOG_DEFAULT


def test_git_log_registry_declares_the_limit_argument():
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    entry = VC.REGISTRY["git_log"]
    names = {a.name for a in entry.args}
    assert "limit" in names, names
    row = next(o for o in OPS.catalog() if o["id"] == "git_log")
    assert {a["name"] for a in row["args"]} == names
    assert "cli" in row["interfaces"]


def test_git_log_voice_and_dashboard_default_matches_cli(tmp_path, monkeypatch):
    """A real repo proves the three surfaces return the same commit set."""
    path, cfg = _env(tmp_path, monkeypatch)
    repo = _make_repo(tmp_path, commits=8)
    _activate(cfg, repo)

    rc, cout = _cli(path, "git", "log")
    assert rc == 0, (rc, cout)
    cli_entries = cout["entries"]
    dash = _dash(cfg, "git_log")["receipt"]["result"]["git"]["entries"]
    said = _say(cfg, "qubit, recent commits")["receipt"]["result"]["git"]["entries"]

    # 8 commits < default 10, so all three must return the SAME 8 commits.
    assert len(cli_entries) == 8, cli_entries
    assert dash == cli_entries
    assert said == cli_entries


def test_git_log_limit_is_expressible_on_every_surface(tmp_path, monkeypatch):
    path, cfg = _env(tmp_path, monkeypatch)
    repo = _make_repo(tmp_path, commits=8)
    _activate(cfg, repo)

    rc, cout = _cli(path, "git", "log", "--limit", "3")
    assert rc == 0, (rc, cout)
    assert len(cout["entries"]) == 3

    dash = _dash(cfg, "git_log", {"limit": 3})
    assert len(dash["receipt"]["result"]["git"]["entries"]) == 3

    said = _say(cfg, "qubit, recent commits 3")
    assert len(said["receipt"]["result"]["git"]["entries"]) == 3
    assert said["receipt"]["result"]["git"]["entries"] == cout["entries"]


def test_git_log_backend_no_longer_hardcodes_five(tmp_path, monkeypatch):
    """Regression guard: the backends must pass the shared default, not 5."""
    _, cfg = _env(tmp_path, monkeypatch)
    from voicebridge import git_scope as GI
    from voicebridge import voice_commands as VC

    seen = {}
    real = GI.log

    def spy(_cfg, root="", limit=10, requested_by="cli:user"):
        seen["limit"] = limit
        return real(_cfg, root, limit, requested_by=requested_by)

    monkeypatch.setattr(GI, "log", spy)
    ctx = VC.make_ctx(cfg, actor="voice:user", session="s", source="user")
    VC._b_git(ctx, {"action": "log"})
    assert seen["limit"] == VC.GIT_LOG_DEFAULT != 5


# --------------------------------------- status parity excludes live gauges
# Same contract as tests/test_qbpv_oc4_tri_parity.py::test_status_read_tri_parity:
# the `status` payload embeds the live free-VRAM gauge, which is sampled from
# the host and may move between two reads of the SAME backend operation. The
# comparison must ignore only that named live metric and assert exact parity on
# everything else (this is NOT a weakened assertion).
_LIVE_METRIC_KEYS = ("free_vram_mib",)


def _strip_live(value):
    if isinstance(value, dict):
        return {k: _strip_live(v) for k, v in value.items()
                if k not in _LIVE_METRIC_KEYS}
    if isinstance(value, list):
        return [_strip_live(v) for v in value]
    return value


def _key_shape(value):
    if isinstance(value, dict):
        return {k: _key_shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_key_shape(v) for v in value]
    return None


def test_status_parity_ignores_only_live_vram(tmp_path, monkeypatch):
    _, cfg = _env(tmp_path, monkeypatch)

    dash = _dash(cfg, "status")
    said = _say(cfg, "qubit, system status")
    assert dash["state"] == "executed" and said["state"] == "executed"

    dr = dash["receipt"]["result"]
    sr = said["receipt"]["result"]
    # The live gauge is present (so the scrub is meaningful, not vacuous).
    flat = json.dumps(dr)
    assert "free_vram_mib" in flat, "status no longer exposes the live gauge"
    # Identical schema (no dropped/renamed subtree) and identical substantive
    # values; only the live gauge may differ.
    assert _key_shape(sr) == _key_shape(dr)
    assert _strip_live(sr) == _strip_live(dr)


def test_status_parity_is_stable_over_repeated_reads(tmp_path, monkeypatch):
    """Deterministic non-flake guard: 6 alternating surface reads all match."""
    _, cfg = _env(tmp_path, monkeypatch)
    baseline = _strip_live(_dash(cfg, "status")["receipt"]["result"])
    for i in range(6):
        got = _strip_live(
            _say(cfg, "qubit, system status", session=f"repeat-{i}")
            ["receipt"]["result"])
        assert got == baseline, i


# ------------------------------------------------ structural tri-parity
def test_no_dead_voice_backend_slots():
    from voicebridge import voice_commands as VC

    dead = [(e.id, e.backend)
            for e in list(VC.REGISTRY.values())
            + list(VC.PLUGIN_FLEET_REGISTRY.values())
            if e.backend and e.backend not in VC._BACKENDS]
    assert dead == [], dead


def test_cli_coverage_targets_all_exist():
    from voicebridge import voice_commands as VC

    known = set(VC.REGISTRY) | set(VC.PLUGIN_FLEET_REGISTRY) \
        | set(VC.VOICE_GUARDS)
    orphans = [(family, target)
               for family, targets in VC.CLI_COVERAGE.items()
               for target in targets if target not in known]
    assert orphans == [], orphans


def test_catalog_voice_surface_is_always_a_real_entry():
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    registry = set(VC.REGISTRY)
    gaps = []
    for item in OPS.catalog():
        if "voice" not in item["interfaces"]:
            continue
        op_id = item["id"]
        linked = (op_id in registry
                  or (item.get("voice_guard") or "") in registry
                  or f"{op_id}_refusal" in registry
                  or f"{op_id}_voice" in registry)
        if not linked:
            gaps.append(op_id)
    assert gaps == [], gaps


# ---------------------------------- one shared journal, no second authority
def test_voice_and_dashboard_share_one_confirmation_journal(tmp_path,
                                                            monkeypatch):
    _, cfg = _env(tmp_path, monkeypatch)
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    vpath = Path(VC._journal(cfg).path)
    dpath = Path(OPS._journal(cfg).path)
    assert vpath == dpath, (vpath, dpath)
    assert vpath.name == "approvals.jsonl"


def test_gateway_rpc_opens_no_listener_and_delegates():
    """The RPC registry is transport-neutral: no socket/thread/dup backend."""
    from voicebridge.gateway import rpc as RPC

    src = Path(RPC.__file__).read_text(encoding="utf-8")
    for forbidden in ("socket(", "socket.socket", ".bind(", ".listen(",
                      "Thread(", "multiprocessing"):
        assert forbidden not in src, forbidden

    names = RPC.default_registry().names()
    assert "plugin.mutations" in names
    assert "sessions.sharing" in names
    # A fabricated method is a typed method-not-found, never a fake success.
    with pytest.raises(Exception) as exc:
        RPC.default_registry().handle(None, "does.not.exist", {})
    assert getattr(exc.value, "code", "") == RPC.METHOD_NOT_FOUND


# ------------------------------------------------ terminal classification
def test_terminal_openclaw_rows_are_honestly_classified():
    rows = {r["id"]: r for r in json.loads(LEDGER.read_text())["rows"]}
    allowed_needs = {"creds", "hardware", "owner"}
    for row in rows.values():
        if row["status"] == "OPERATOR_ONLY":
            assert row["needs"] in allowed_needs and row["needs"] != "none", row
            assert row["proving_test"], row["id"]
            assert (REPO_ROOT / row["proving_test"].split("::")[0]).is_file()
        if row["status"] == "SAFE_EQUIVALENT":
            assert row["needs"] in allowed_needs and row["needs"] != "none", row
            # The note must document *why* the refusal is the safe equivalent.
            assert row["note"].strip(), row["id"]


def test_oc34_note_no_longer_claims_a_pending_cli_handoff():
    rows = {r["id"]: r for r in json.loads(LEDGER.read_text())["rows"]}
    note = rows["OC-34"]["note"]
    assert "tracked cross-boundary handoff" not in note
    assert "kanban restore" in note.lower()
    assert rows["OC-34"]["status"] == "IMPLEMENTED"


def test_oc68_voice_counts_match_the_live_registry():
    from voicebridge import voice_commands as VC

    rows = {r["id"]: r for r in json.loads(LEDGER.read_text())["rows"]}
    expected = (f"{len(VC.REGISTRY)} entries/{len(VC.VOICE_GUARDS)} guards")
    assert rows["OC-68"]["voice"] == expected, rows["OC-68"]["voice"]


def test_openclaw_ledger_regenerates_clean():
    out = subprocess.run([sys.executable, str(GEN), "--check"],
                         cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert out.returncode == 0, (out.returncode, out.stdout, out.stderr)
    assert "up to date" in out.stdout


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
