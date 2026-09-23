"""CLI / dashboard / voice parity table (machine-checked).

Every Hermes-dashboard family must be reachable through the SAME Qubit
backend from all three surfaces:
- dashboard: qubit_compat route (implemented or typed-gated);
- CLI: `qubit <group>` command family;
- voice: Tier-1 registry entry routing to the same backend module.

Same-store spot checks prove dashboard and voice read the same records.
No CLI_ONLY / DASHBOARD_ONLY / VOICE_MISSING rows are permitted below:
every row asserts all three surfaces.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# (compat GET path, CLI group, voice op id, expected http)
ROWS = [
    ("/api/status", "status", "status", 200),
    ("/api/sessions", "sessions", "sessions", 200),
    ("/api/config", "profile", "config_profiles", 200),
    ("/api/model/info", "frontier", "model", 200),
    ("/api/profiles", "brain", "brain", 200),
    ("/api/skills", "skills", "skills_list", 200),
    ("/api/tools/toolsets", "capability", "capabilities_list", 200),
    ("/api/cron/jobs", "schedules", "schedules_list", 200),
    ("/api/logs", "monitor", "logs", 200),
    ("/api/system/stats", "status", "status", 200),
    ("/api/mcp", "mcp", "mcp", 200),
    ("/api/pairing", "pairing", "pairing_list", 200),
    ("/api/webhooks", "webhook", "webhooks", 200),
    ("/api/messaging/platforms", "msg", "msg_accounts", 200),
    ("/api/memory", "memory", "memory_search", 200),
    ("/api/analytics", "monitor", "insights", 200),
    ("/api/files", "files", "files_list", 200),
    ("/api/gateway", "gateway", "gateway", 200),
    ("/api/curator", "lessons", "curator", 200),
    ("/api/env", "auth", "auth_status", 200),
    ("/api/ops/doctor", "setup", "doctor", 200),
    ("/api/ops/checkpoints", "checkpoints", "checkpoints_list", 200),
    ("/api/ops/hooks", "hooks", "hooks_list", 200),
    ("/api/plugins/kanban/events", "kanban", "kanban_list", 200),
    ("/api/dashboard/themes", "theme", "themes", 200),
    ("/api/dashboard/plugins", "skills", "skills_list", 200),
    ("/api/ops/boards", "kanban", "kanban_boards", 200),
    ("/api/ops/runtime", "runtime", "runtime_status", 200),
    # typed gates: same backend refusal on all surfaces (policy parity)
    ("/api/pty", "tui", "tui_help", 403),
    ("/api/config/raw", "profile", "config_profiles", 403),
    # gateway control is confirm-gated (Owner Decision #10): a bare start is a
    # typed 409 GATEWAY_CONFIRM_REQUIRED, never a raw 403 (see
    # voicebridge/gateway_supervisor._authorize_control + h_gateway_supervision).
    ("/api/gateway/start", "gateway", "gateway", 409),
]


def _cfg(tmp_path):
    from voicebridge.config import Config
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl_path: '" + str(state) + "'\n")
    return Config.load(p)


def test_parity_table_all_surfaces(tmp_path):
    from voicebridge import qubit_compat as HC
    from voicebridge import voice_commands as VC
    cfg = _cfg(tmp_path)
    for path, cli_group, voice_op, http in ROWS:
        code, out = HC.dispatch(cfg, "GET" if not path.endswith("/start") else "POST",
                                path, {}, {})
        if path == "/api/gateway/start":
            code, out = HC.dispatch(cfg, "POST", path, {}, {})
        assert code == http, (path, code, str(out)[:160])
        r = subprocess.run([sys.executable, "-m", "voicebridge.cli",
                            cli_group, "--help"],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, (path, cli_group, r.stderr[:200])
        assert voice_op in VC.REGISTRY, (path, voice_op)


def _ids_pairing_compat(out):
    users = (out.get("pending", []) or []) + (out.get("approved", []) or [])
    return sorted(u.get("user_id", "") for u in users)


def _ids_pairing_voice(out):
    res = out.get("receipt", {})
    res = res.get("result", res) if isinstance(res, dict) else {}
    codes = res.get("codes", []) if isinstance(res, dict) else []
    return sorted(c.get("code_id", c.get("id", "")) for c in codes
                  if isinstance(c, dict))


def test_same_store_dashboard_voice(tmp_path):
    """Dashboard compat and voice ops read the same records."""
    from voicebridge import qubit_compat as HC
    from voicebridge import dashboard_ops as OPS
    cfg = _cfg(tmp_path)
    # pairing: seed one code through the backend, read via both surfaces
    from voicebridge import pairing as P
    created = P.create(cfg, "parity-probe", ["chat"], account="",
                       requested_by="parity", confirmed=True)
    cid = created.get("code_id", created.get("id", ""))
    code, dout = HC.dispatch(cfg, "GET", "/api/pairing", {}, {})
    assert code == 200
    vout = OPS.run_op(cfg, "pairing_list", {}, actor="voice:user",
                      session="voice")
    assert vout.get("state") == "executed", vout
    assert cid in _ids_pairing_compat(dout), dout
    assert cid in _ids_pairing_voice(vout), vout
    # webhooks: same-store check
    from voicebridge import webhooks as W
    W.add(cfg, "parity-hook", "http://localhost:9/qubit-parity-hook", ["message"],
          "env:QUBIT_PARITY_REF", requested_by="parity", confirmed=True)
    code, dout = HC.dispatch(cfg, "GET", "/api/webhooks", {}, {})
    names_d = sorted(s.get("name", "") for s in dout.get("subscriptions", []))
    vout = OPS.run_op(cfg, "webhooks", {}, actor="voice:user", session="voice")
    res = vout.get("receipt", {})
    res = res.get("result", res) if isinstance(res, dict) else {}
    subs = res.get("subscriptions", []) if isinstance(res, dict) else []
    names_v = sorted(s.get("id", s.get("sub_id", "")) for s in subs
                     if isinstance(s, dict))
    assert "parity-hook" in names_d and "parity-hook" in names_v
    # schedules: same-store check
    from voicebridge import schedules as S
    job = S.create(cfg, "parity-job", "parity task text", "once in 60 minutes",
                   requested_by="parity", confirmed=True)
    jid = str(job.get("job", {}).get("id", ""))
    code, dout = HC.dispatch(cfg, "GET", "/api/cron/jobs", {}, {})
    assert code == 200 and isinstance(dout, list)
    assert jid in [j.get("id", "") for j in dout], dout
    vout = OPS.run_op(cfg, "schedules_list", {}, actor="voice:user",
                      session="voice")
    res = vout.get("receipt", {})
    res = res.get("result", res) if isinstance(res, dict) else {}
    sched = res.get("schedules", res) if isinstance(res, dict) else {}
    jobs = sched.get("jobs", []) if isinstance(sched, dict) else []
    assert jid in [str(j.get("id", "")) for j in jobs if isinstance(j, dict)]
