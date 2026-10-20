"""The commit-point preflight must consult the downgrade guard before any tree write."""
from __future__ import annotations

import pytest

from qubit_cli import update_cmd


def test_downgrade_refusal_stops_before_the_commit_point(monkeypatch):
    main = type("Main", (), {
        "PROJECT_ROOT": "/tmp/does-not-matter",
        "_resume_windows_gateways_after_update": staticmethod(lambda _token: None),
    })()
    monkeypatch.setattr(update_cmd, "_m", lambda: main)
    monkeypatch.setattr(update_cmd._commit, "preflight_refusal", lambda *a, **k: None)
    stopped: list[str] = []
    monkeypatch.setattr(update_cmd, "_record_stop", stopped.append)
    seen: list[tuple] = []

    def refuse(git_cmd, root, target_ref):
        seen.append((git_cmd, root, target_ref))
        return "✗ Refusing to move this install backwards"

    monkeypatch.setattr("qubit_cli.update_downgrade_guard.downgrade_refusal", refuse)

    with pytest.raises(SystemExit) as exc:
        update_cmd._refuse_before_commit_point(["git"], "abc123", None)

    assert exc.value.code == 1
    assert seen == [(["git"], "/tmp/does-not-matter", "abc123")]
    assert stopped == ["downgrade_refused"]


def test_clean_preflight_returns_without_the_guard(monkeypatch):
    main = type("Main", (), {
        "PROJECT_ROOT": "/tmp/does-not-matter",
        "_resume_windows_gateways_after_update": staticmethod(lambda _token: None),
    })()
    monkeypatch.setattr(update_cmd, "_m", lambda: main)
    monkeypatch.setattr(update_cmd._commit, "preflight_refusal", lambda *a, **k: None)
    monkeypatch.setattr("qubit_cli.update_downgrade_guard.downgrade_refusal", lambda *a, **k: None)
    update_cmd._refuse_before_commit_point(["git"], "abc123", None)  # no SystemExit
