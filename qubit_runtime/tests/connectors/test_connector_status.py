"""Read-only readiness contracts for ``qubit connectors status``.

Every test runs against a temp ``QUBIT_HOME`` and proves the status path makes
no network/client call and never prints token bytes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from connectors.dropbox import DropboxConnector
from connectors.google_calendar import GoogleCalendarConnector
from connectors.google_contacts import GoogleContactsConnector
from qubit_cli.connector_status import (
    STATUS_AVAILABLE,
    STATUS_ERROR,
    STATUS_MISSING_CREDENTIAL,
    STATUS_NOT_SUPPORTED,
    STATUSES,
    collect_status,
    render_json,
    render_text,
)
from qubit_cli.connectors_cmd import cmd_connectors
from qubit_cli.subcommands.connectors import build_connectors_parser

SECRET_MARKER = "SECRET-TOKEN-MARKER-DO-NOT-PRINT"


def _row(rows, connector: str):
    matches = [row for row in rows if row.connector == connector]
    assert len(matches) == 1, f"expected one {connector} row, got {len(matches)}"
    return matches[0]


def _parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    build_connectors_parser(parser.add_subparsers(dest="command"))
    return parser.parse_args(list(argv))


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "qubit-home"
    path.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(path))
    return path


@pytest.fixture
def no_client_calls(monkeypatch):
    """Fail loudly if readiness ever reaches a connector operation or HTTP."""

    def _boom(*args, **kwargs):
        raise AssertionError("connector status must not make a client/network call")

    for cls, method in (
        (GoogleContactsConnector, "list"),
        (GoogleContactsConnector, "search"),
        (GoogleCalendarConnector, "list_events"),
        (GoogleCalendarConnector, "search"),
        (DropboxConnector, "list_folder"),
        (DropboxConnector, "get_metadata"),
        (DropboxConnector, "search"),
    ):
        monkeypatch.setattr(cls, method, _boom)
    monkeypatch.setattr("connectors.google_contacts._default_http_get", _boom)
    monkeypatch.setattr("connectors.google_calendar._default_http_get", _boom)
    monkeypatch.setattr("connectors.dropbox._default_http_post", _boom)


def test_missing_tokens_report_typed_rows_and_exact_paths(home):
    rows = collect_status(platform="linux")

    assert {row.connector for row in rows} == {
        "google_contacts",
        "google_calendar",
        "dropbox",
        "apple_contacts",
        "apple_calendar",
        "apple_health",
        "apple_music",
    }
    for connector in ("google_contacts", "google_calendar"):
        row = _row(rows, connector)
        assert row.status == STATUS_MISSING_CREDENTIAL
        assert row.token_path == str(home / "google_token.json")
        assert row.hint

    dropbox = _row(rows, "dropbox")
    assert dropbox.status == STATUS_MISSING_CREDENTIAL
    assert dropbox.token_path == str(home / "dropbox_token.json")
    assert dropbox.hint

    for connector in ("apple_contacts", "apple_calendar", "apple_health", "apple_music"):
        row = _row(rows, connector)
        assert row.status == STATUS_NOT_SUPPORTED
        assert row.platform == "linux"
        assert row.required_framework
        assert "macOS" in row.hint
        assert row.required_framework in row.hint


def test_present_token_is_available_without_any_client_call(home, no_client_calls):
    (home / "google_token.json").write_text(
        json.dumps({"access_token": SECRET_MARKER, "refresh_token": "refresh"}), encoding="utf-8")
    (home / "dropbox_token.json").write_text(
        json.dumps({"access_token": SECRET_MARKER}), encoding="utf-8")

    rows = collect_status(platform="linux")

    for connector in ("google_contacts", "google_calendar"):
        row = _row(rows, connector)
        assert row.status == STATUS_AVAILABLE
        assert row.token_path == str(home / "google_token.json")
    dropbox = _row(rows, "dropbox")
    assert dropbox.status == STATUS_AVAILABLE
    assert dropbox.token_path == str(home / "dropbox_token.json")

    text = render_text(rows)
    document = render_json(rows)
    assert SECRET_MARKER not in text
    assert SECRET_MARKER not in document


def test_unusable_token_file_is_an_error_not_a_credential_claim(home, no_client_calls):
    (home / "google_token.json").write_text(
        f"not json {SECRET_MARKER}", encoding="utf-8")

    rows = collect_status(platform="linux")

    for connector in ("google_contacts", "google_calendar"):
        row = _row(rows, connector)
        assert row.status == STATUS_ERROR
        assert row.token_path == str(home / "google_token.json")
    assert SECRET_MARKER not in render_text(rows)
    assert SECRET_MARKER not in render_json(rows)


def test_json_flag_is_valid_and_carries_no_secret(home, no_client_calls, monkeypatch, capsys):
    (home / "google_token.json").write_text(
        json.dumps({"access_token": SECRET_MARKER}), encoding="utf-8")
    # Status must work signed out: the managed portal is not consulted.
    monkeypatch.setattr("tools.connectors.connectors_available", lambda: False, raising=False)

    rc = cmd_connectors(_parse("connectors", "status", "--json"))

    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload["connectors"], list)
    assert len(payload["connectors"]) == 7
    assert all(row["status"] in STATUSES for row in payload["connectors"])
    assert SECRET_MARKER not in out


def test_doctor_alias_and_legacy_actions_still_parse(home, no_client_calls, monkeypatch, capsys):
    # The alias dispatches through the same handler and stays local.
    monkeypatch.setattr("tools.connectors.connectors_available", lambda: False, raising=False)
    doctor = _parse("connectors", "doctor")
    assert doctor.connectors_action == "doctor"
    assert doctor.func is cmd_connectors
    assert cmd_connectors(doctor) == 0
    assert "google_contacts" in capsys.readouterr().out

    for argv, action in (
        (("list",), "list"),
        (("ls",), "ls"),
        (("accounts",), "accounts"),
        (("connect", "gmail"), "connect"),
        (("rename", "gmail", "work", "--to", "home"), "rename"),
        (("disconnect", "gmail", "--alias", "work", "--yes"), "disconnect"),
    ):
        args = _parse("connectors", *argv)
        assert args.connectors_action == action
        assert args.func is cmd_connectors
