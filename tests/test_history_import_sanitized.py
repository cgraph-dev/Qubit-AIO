"""Sanitized CLI-history import — reviewable, reversible, secret-free.

Hermetic: every test builds a synthetic source tree under ``tmp_path`` and
passes explicit ``home=``/``dest=`` paths, so the operator's real
``~/.hermes/.hermes_history`` is never opened and no Qubit state is touched.

Contracts proven here:

* secret-looking values (``sk-``, ``sk_live_``, ``ghp_``, ``xoxb-``, ``AKIA``,
  bearer tokens, ``api_key=``/``password=`` pairs, long high-entropy tokens) are
  replaced by a typed placeholder, and the secret text never reaches the
  destination;
* ordinary command lines are preserved byte-for-byte;
* the default is review-only and writes nothing;
* ``--apply`` backs up an existing destination first and is reversible;
* rollback refuses when there is no backup.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

SK_KEY = "sk-proj-AbCdEf0123456789ghijkl"
GHP = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _source(tmp_path: Path, text: str) -> Path:
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / ".hermes_history").write_text(text, encoding="utf-8")
    return src


def test_sanitizer_redacts_secret_shapes_and_preserves_safe_lines():
    from voicebridge import qubit_migration as HM

    safe = [
        "qubit agent -z 'summarize the changelog'",
        "git log abcdef1234567890abcdef1234567890abcdef12",
        "cd /home/user/projects/qubit-voicebridge",
    ]
    unsafe = [
        f"export OPENAI_API_KEY={SK_KEY}",
        "stripe key sk_live_51H8xYZ1234567890abcdef",
        f"git clone https://{GHP}@github.com/o/r.git",
        "aws AKIAIOSFODNN7EXAMPLE",
        "curl -H 'Authorization: Bearer abcd1234.token.xyz'",
        "curl https://api.example.com?api_key=supersecretvalue123",
        "echo password=hunter2",
        "token = 'aB3dE6gH9jK2mN5pQ8rS1tU4vW7xY0zA'",
    ]
    text = "\n".join(safe + unsafe) + "\n"
    report = HM.sanitize_cli_history(text)

    assert report["lines"] == len(safe) + len(unsafe)
    assert report["redactions"] >= len(unsafe), report
    # Safe lines survive unchanged.
    for line in safe:
        assert line in report["text"], line
    # Indices reported are 1-based and correspond to the unsafe lines.
    assert report["redacted_lines"] == list(
        range(len(safe) + 1, len(safe) + len(unsafe) + 1)), report
    # No secret text survives anywhere in the sanitized output.
    for line in unsafe:
        assert line not in report["text"], line
    assert SK_KEY not in report["text"]
    assert GHP not in report["text"]
    assert HM.HISTORY_PLACEHOLDER in report["text"]
    # Report carries counts/line numbers only.
    assert SK_KEY not in json.dumps(report)


def test_sanitizer_is_idempotent_and_placeholder_safe():
    from voicebridge import qubit_migration as HM

    once = HM.sanitize_cli_history(f"export KEY={SK_KEY}\n")
    twice = HM.sanitize_cli_history(once["text"])
    assert twice["redactions"] == 0
    assert twice["text"] == once["text"]
    assert HM.HISTORY_PLACEHOLDER in once["text"]


def test_dry_run_writes_nothing(tmp_path):
    from voicebridge import qubit_migration as HM

    src = _source(tmp_path, f"export KEY={SK_KEY}\nsafe command\n")
    dest = tmp_path / "dest" / ".hermes_history"
    bdir = tmp_path / "backup"

    out = HM.import_cli_history_sanitized(
        home=src, dest=dest, confirm=False, backup_dir=bdir)

    assert out["state"] == "DRY_RUN", out
    assert out["dry_run"] is True
    assert out["redactions"] >= 1
    assert out["source_redacted_lines"] == [1]
    assert not dest.exists(), "dry-run must not create the destination"
    assert not bdir.exists(), "dry-run must not create a backup"
    assert SK_KEY not in json.dumps(out), "review must never carry the secret"


def test_apply_redacts_secret_backs_up_and_rolls_back(tmp_path):
    from voicebridge import qubit_migration as HM

    src = _source(tmp_path, f"export KEY={SK_KEY}\nqubit agent -z hi\n")
    dest = tmp_path / "dest" / ".hermes_history"
    dest.parent.mkdir(parents=True)
    dest.write_text("qubit doctor\n", encoding="utf-8")
    original = dest.read_bytes()
    bdir = tmp_path / "backup"

    out = HM.import_cli_history_sanitized(
        home=src, dest=dest, confirm=True, backup_dir=bdir)
    assert out["state"] == "OK", out
    assert out["dry_run"] is False
    assert Path(out["backup_dir"]) == bdir
    assert out["destination_existed"] is True
    assert out["added_lines"] == 2

    written = dest.read_text(encoding="utf-8")
    # The secret never reaches the destination; the placeholder + safe lines do.
    assert SK_KEY not in written
    assert HM.HISTORY_PLACEHOLDER in written
    assert "qubit agent -z hi" in written
    assert "qubit doctor" in written
    # Backup captured the pre-import destination exactly, and a journal exists.
    backup_file = bdir / "destination" / dest.name
    assert backup_file.read_bytes() == original
    journal = json.loads((bdir / "journal.json").read_text())
    assert journal["state"] == "OK"
    assert journal["destination_existed"] is True
    assert SK_KEY not in json.dumps(journal)

    # Re-run adds nothing (idempotent).
    again = HM.import_cli_history_sanitized(
        home=src, dest=dest, confirm=True, backup_dir=bdir)
    assert again["state"] == "NOOP", again

    # Rollback is dry-run by default, then restores the exact pre-import bytes.
    dry = HM.rollback_cli_history(backup_dir=bdir, confirm=False)
    assert dry["state"] == "DRY_RUN", dry
    assert dest.read_text(encoding="utf-8") != original.decode()
    rolled = HM.rollback_cli_history(backup_dir=bdir, confirm=True)
    assert rolled["state"] == "OK", rolled
    assert rolled["action"] == "restored"
    assert dest.read_bytes() == original


def test_import_does_not_duplicate_a_secret_shared_by_source_and_dest(tmp_path):
    """A secret already in the destination must not be re-added by the source.

    Regression: the merge de-duplicated incoming lines against the *raw*
    destination, but kept the *sanitized* destination lines, so a line whose
    secret was redacted in both source and destination was written twice.
    """
    from voicebridge import qubit_migration as HM

    src = _source(tmp_path, f"export KEY={SK_KEY}\nsafe command\n")
    dest = tmp_path / "dest" / ".hermes_history"
    dest.parent.mkdir(parents=True)
    dest.write_text(f"export KEY={SK_KEY}\n", encoding="utf-8")
    bdir = tmp_path / "backup"

    out = HM.import_cli_history_sanitized(
        home=src, dest=dest, confirm=True, backup_dir=bdir)
    assert out["state"] == "OK", out
    # Only the one genuinely new line is added; the shared secret line is not.
    assert out["added_lines"] == 1, out

    written = dest.read_text(encoding="utf-8").splitlines()
    placeholder_lines = [ln for ln in written if HM.HISTORY_PLACEHOLDER in ln]
    assert len(placeholder_lines) == 1, written
    assert SK_KEY not in "\n".join(written)
    assert "safe command" in written

    # The sanitized output is idempotent: a second apply adds nothing.
    again = HM.import_cli_history_sanitized(
        home=src, dest=dest, confirm=True, backup_dir=bdir)
    assert again["state"] == "NOOP", again


def test_apply_writes_review_out_file_without_secrets(tmp_path):
    """``--review-out`` must write the report on apply, not only dry-run."""
    from voicebridge import qubit_migration as HM

    src = _source(tmp_path, f"export KEY={SK_KEY}\nqubit doctor\n")
    dest = tmp_path / "dest" / ".hermes_history"
    bdir = tmp_path / "backup"
    review_file = tmp_path / "review.json"

    out = HM.import_cli_history_sanitized(
        home=src, dest=dest, confirm=True, backup_dir=bdir,
        review_path=review_file)
    assert out["state"] == "OK", out
    assert review_file.is_file(), "review report must be written on apply"
    assert out["review_path"] == str(review_file)
    report = review_file.read_text(encoding="utf-8")
    assert SK_KEY not in report
    assert json.loads(report)["state"] == "OK"


def test_rollback_removes_destination_the_import_created(tmp_path):
    from voicebridge import qubit_migration as HM

    src = _source(tmp_path, "qubit status\n")
    dest = tmp_path / "dest" / ".hermes_history"
    bdir = tmp_path / "backup"

    out = HM.import_cli_history_sanitized(
        home=src, dest=dest, confirm=True, backup_dir=bdir)
    assert out["state"] == "OK", out
    assert out["destination_existed"] is False
    assert dest.is_file()

    rolled = HM.rollback_cli_history(backup_dir=bdir, confirm=True)
    assert rolled["state"] == "OK", rolled
    assert rolled["action"] == "removed"
    assert not dest.exists()


def test_rollback_refuses_without_backup(tmp_path):
    from voicebridge import qubit_migration as HM

    out = HM.rollback_cli_history(backup_dir=tmp_path / "nope", confirm=True)
    assert out["state"] == "MISSING", out
    assert out["code"] == "HISTORY_NO_BACKUP", out


def test_missing_source_is_refused(tmp_path):
    from voicebridge import qubit_migration as HM

    out = HM.import_cli_history_sanitized(
        home=tmp_path / "empty", dest=tmp_path / "dest/.hermes_history")
    assert out["state"] == "MISSING", out
    assert out["source_present"] is False


def test_cli_verbs_are_registered():
    from voicebridge.cli import build_parser

    parser = build_parser()
    imp = parser.parse_args(["product", "history-import"])
    assert imp.product_command == "history-import"
    assert imp.apply is False
    roll = parser.parse_args(
        ["product", "history-rollback", "--backup-dir", "/tmp/x"])
    assert roll.product_command == "history-rollback"
    assert roll.apply is False
