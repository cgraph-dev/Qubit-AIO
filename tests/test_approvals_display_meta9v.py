"""`GET /api/ops/approvals` display metadata.

The Office Approvals panel must show WHAT a pending record is asking for
(action/risk/requester) without ever receiving the staged secret-bearing
payload the journal keeps to settle it. This pins the projection at the route
boundary: safe display keys are copied (redacted/bounded), and raw
args/payload/digests never leave the journal.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path):
    from voicebridge.config import Config

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(
        f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n")
    return Config.load(p)


def test_pending_approval_carries_safe_display_metadata(tmp_path):
    from voicebridge import qubit_compat as HC
    from voicebridge.approvals import ApprovalJournal

    cfg = _cfg(tmp_path)
    journal = ApprovalJournal(
        Path(cfg.state_dir()) / "approvals.jsonl", default_ttl_s=120.0)
    approval = journal.create(
        "dashboard.backup", "Backup: confirm to proceed.", ttl_s=120.0,
        session="dashboard", action="backup", risk="high",
        actor="dashboard:user", op="backup",
        args={"output": "/tmp/backup.zip"}, sensitive=["passphrase"])

    status, out = HC.h_approvals_list(cfg, {}, {}, {})
    assert status == 200, out
    assert out["count"] == 1
    row = out["approvals"][0]
    assert row["id"] == approval.id
    assert row["capability"] == "dashboard.backup"
    assert row["action"] == "backup"
    assert row["risk"] == "high"
    assert row["requested_by"] == "dashboard:user"
    assert row["session"] == "dashboard"
    # Staged settlement material is NEVER projected.
    for forbidden in ("args", "payload", "sensitive", "args_digest",
                      "task_key", "content_base64"):
        assert forbidden not in row, (forbidden, row)


def test_display_metadata_falls_back_to_the_channel_actor(tmp_path):
    from voicebridge import qubit_compat as HC
    from voicebridge.approvals import ApprovalJournal

    cfg = _cfg(tmp_path)
    journal = ApprovalJournal(
        Path(cfg.state_dir()) / "approvals.jsonl", default_ttl_s=120.0)
    journal.create(
        "voice.kanban_boards_create", "Create board: say yes to confirm.",
        ttl_s=120.0, session="dashboard", args={"board": "main"},
        actor="owner")

    status, out = HC.h_approvals_list(cfg, {}, {}, {})
    assert status == 200, out
    row = out["approvals"][0]
    # `action` is absent on this channel; `op`/`issued_by` fallbacks are not
    # invented, so only the actor alias is projected.
    assert row["requested_by"] == "owner"
    assert "args" not in row


def test_no_pending_records_is_an_empty_list_not_an_error(tmp_path):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path)
    status, out = HC.h_approvals_list(cfg, {}, {}, {})
    assert status == 200, out
    assert out["ok"] is True
    assert out["count"] == 0
    assert out["approvals"] == []
