"""Office exit checks are completion proofs, never an arbitrary shell runner."""
from __future__ import annotations

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _org(cfg, *, workers=1, managers=1):
    from voicebridge.office import org as O

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    managers = [O.hire(cfg, f"mgr-{i}", seat_type="manager",
                       supervisor_id=ceo, confirmed=True)["id"]
                for i in range(managers)]
    workers = [O.hire(cfg, f"wk-{i}", seat_type="worker",
                      supervisor_id=managers[i % len(managers)], confirmed=True)["id"]
               for i in range(workers)]
    return {"ceo": ceo, "managers": managers, "workers": workers}


@pytest.mark.parametrize("command", [
    "true",
    "false",
    "test -f /nonexistent/path",
    "test -d '/tmp/some path'",
    "grep -q needle /dev/null",
    "sqlite3 -readonly -safe /tmp/x.db 'SELECT 1' | grep -q 1",
])
def test_safe_verification_commands_are_accepted(command):
    from voicebridge.office.exit_check import validate_exit_check

    assert validate_exit_check(command) is None


@pytest.mark.parametrize("command", [
    "touch /tmp/qubit-office-pwned",
    "rm -rf /tmp/x",
    "echo hi",
    "python -c 'print(1)'",
    "test -f x; touch /tmp/pwned",
    "test -f x && touch /tmp/pwned",
    "test -f x > /tmp/out",
    "sh -c id",
    "/bin/sh -c id",
    "sqlite3 /tmp/x.db 'SELECT 1'",
    "sqlite3 -readonly /tmp/x.db '.shell touch /tmp/pwned'",
    "sqlite3 -readonly /tmp/x.db 'SELECT load_extension(\"/tmp/x.so\")'",
    "grep -q x a | grep -q y",
])
def test_unsafe_commands_are_refused(command):
    from voicebridge.office.exit_check import validate_exit_check

    assert validate_exit_check(command) is not None


def test_shell_checker_never_executes_a_command_injection(tmp_path):
    from voicebridge.office.dispatch import shell_checker

    marker = tmp_path / "pwned"
    checker = shell_checker(f"test -f /nope; touch {marker}")
    assert checker({}) is False
    assert not marker.exists()


def test_a_task_cannot_be_created_with_an_unsafe_exit_check(tmp_path, monkeypatch):
    from voicebridge.office import work as W
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(OfficeError) as exc:
        W.create(cfg, "run a command", exit_check="touch /tmp/pwned", confirmed=True)
    assert exc.value.code == "OFFICE_EXIT_CHECK_UNSAFE"


def test_a_seat_cannot_propose_an_unsafe_exit_check(tmp_path, monkeypatch):
    from voicebridge.office import task_proposal as P
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    with pytest.raises(OfficeError) as exc:
        P.propose(cfg, ids["workers"][0], "idea", exit_check="touch /tmp/pwned",
                  rationale="because")
    assert exc.value.code == "OFFICE_EXIT_CHECK_UNSAFE"


def test_the_autowork_check_is_in_the_safe_grammar(tmp_path, monkeypatch):
    from voicebridge.office import autowork as AW, store as S
    from voicebridge.office.exit_check import validate_exit_check

    cfg = _cfg(tmp_path, monkeypatch)
    command = AW._tick_exit_check(cfg, {}, "tick:123")
    assert "-safe" in command
    assert "office_run_receipts" in command
    assert validate_exit_check(command) is None
