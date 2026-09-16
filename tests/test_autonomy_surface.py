"""Autonomy tri-surface proof: CLI + dashboard + voice on one backend.

Proves the owner-scoped autonomy admin (Workstream F) is reachable from the
canonical surfaces and calls the real ``voicebridge.autonomy_ops`` backend,
not a mock. Hermetic: temp XDG dirs, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    from voicebridge import config as C

    return C.Config.load(p), p


def _run(capsys, cfg_path: Path, *argv) -> dict:
    from voicebridge import cli as CLI

    rc = CLI.main(["--config", str(cfg_path), *argv])
    out = capsys.readouterr().out
    start = out.find("{")
    payload = json.loads(out[start:]) if start >= 0 else {}
    payload["_rc"] = rc
    return payload


def test_voice_registry_exposes_autonomy_entries():
    from voicebridge import voice_commands as VC

    # Every autonomy admin op is a real registry entry with a real backend.
    for eid in ("autonomy_status", "autonomy_list", "autonomy_show",
                "autonomy_pause", "autonomy_resume", "autonomy_revoke",
                "autonomy_expire"):
        entry = VC.REGISTRY[eid]
        assert entry.backend in VC._BACKENDS, (eid, entry.backend)
    for alias, eid in (("autonomy status", "autonomy_status"),
                       ("list autonomy profiles", "autonomy_list"),
                       ("pause autonomy", "autonomy_pause"),
                       ("resume autonomy", "autonomy_resume"),
                       ("revoke autonomy", "autonomy_revoke")):
        r = VC.resolve(alias)
        assert r is not None and r.entry is not None, (alias, r)
        assert r.entry.id == eid, (alias, r.entry.id)


def test_cli_autonomy_status_and_list(tmp_path, monkeypatch, capsys):
    _cfg_obj, cfg_path = _cfg(tmp_path, monkeypatch)
    st = _run(capsys, cfg_path, "autonomy", "status")
    assert st["_rc"] == 0
    assert st["autonomy"]["profile_count"] == 0
    lst = _run(capsys, cfg_path, "autonomy", "list")
    assert lst["_rc"] == 0
    assert lst["autonomy"]["count"] == 0


def test_cli_autonomy_pause_resume_revoke_round_trip(tmp_path, monkeypatch,
                                                     capsys):
    cfg, cfg_path = _cfg(tmp_path, monkeypatch)
    from voicebridge import autonomy_ops as AO
    from voicebridge.actions import HUMAN_PRINCIPAL

    prof = AO.create(cfg, owner=HUMAN_PRINCIPAL,
                     capabilities=("t.write",), name="nightly")
    pid = prof.profile_id

    # Unconfirmed mutation stages a typed needs-confirm (no state change).
    staged = _run(capsys, cfg_path, "autonomy", "pause", pid)
    assert staged["_rc"] == 3
    assert staged["code"] == "CONFIRM_REQUIRED"

    paused = _run(capsys, cfg_path, "autonomy", "pause", pid, "--confirm")
    assert paused["_rc"] == 0
    assert paused["autonomy"]["profile"]["state"] == "paused"

    resumed = _run(capsys, cfg_path, "autonomy", "resume", pid, "--confirm")
    assert resumed["_rc"] == 0
    assert resumed["autonomy"]["profile"]["state"] == "active"

    revoked = _run(capsys, cfg_path, "autonomy", "revoke", pid, "--confirm")
    assert revoked["_rc"] == 0
    assert revoked["autonomy"]["profile"]["state"] == "revoked"


def test_dashboard_route_and_confirmation_flow(tmp_path, monkeypatch):
    """The dashboard route runs the same backend and shares the journal."""
    cfg, _ = _cfg(tmp_path, monkeypatch)
    from voicebridge import dashboard_ops as OPS
    from voicebridge import autonomy_ops as AO
    from voicebridge.actions import HUMAN_PRINCIPAL

    assert OPS.run_op(cfg, "autonomy_status", {},
                      actor="dashboard:user")["state"] == "executed"
    assert OPS.run_op(cfg, "autonomy_list", {},
                      actor="dashboard:user")["state"] == "executed"
    prof = AO.create(cfg, owner=HUMAN_PRINCIPAL, capabilities=("t.write",),
                     name="nightly")
    assert OPS.run_op(cfg, "autonomy_show", {"profile_id": prof.profile_id},
                      actor="dashboard:user")["state"] == "executed"
    staged = OPS.run_op(cfg, "autonomy_pause", {"profile_id": prof.profile_id},
                        actor="dashboard:user")
    assert staged["state"] == "needs-confirm" and staged["confirmation_id"]
    done = OPS.confirm_op(cfg, staged["confirmation_id"], actor="dashboard:user")
    assert done["state"] == "executed"
    assert AO.show(cfg, prof.profile_id).state == "paused"

    st = OPS.run_op(cfg, "autonomy_resume", {"profile_id": prof.profile_id},
                    actor="dashboard:user")
    assert st["state"] == "needs-confirm" and st["confirmation_id"]
    assert OPS.confirm_op(cfg, st["confirmation_id"],
                          actor="dashboard:user")["state"] == "executed"
    assert AO.show(cfg, prof.profile_id).state == "active"

    st = OPS.run_op(cfg, "autonomy_revoke", {"profile_id": prof.profile_id},
                    actor="dashboard:user")
    assert st["state"] == "needs-confirm" and st["confirmation_id"]
    assert OPS.confirm_op(cfg, st["confirmation_id"],
                          actor="dashboard:user")["state"] == "executed"
    assert AO.show(cfg, prof.profile_id).state == "revoked"

    assert OPS.run_op(cfg, "autonomy_expire", {},
                      actor="dashboard:user")["state"] == "executed"


def test_dashboard_catalog_includes_autonomy_ops(tmp_path, monkeypatch):
    _cfg_obj, _ = _cfg(tmp_path, monkeypatch)
    from voicebridge import dashboard_ops as OPS

    ops = OPS.catalog()
    ids = {str(i.get("id")) for i in ops}
    for op in ("autonomy_status", "autonomy_list", "autonomy_show",
               "autonomy_pause", "autonomy_resume", "autonomy_revoke",
               "autonomy_expire"):
        assert op in ids, op
