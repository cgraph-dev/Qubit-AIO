"""Registry-level tests for the ``google_contacts`` tool surface."""

from __future__ import annotations

import json

from connectors.google_contacts import (
    Contact,
    GoogleContactsConnector,
    GoogleContactsError,
    REASON_MISSING_CREDENTIAL,
)
from tools.google_contacts_tool import _google_contacts_configured
from tools.registry import registry


class _FakeConnector:
    def __init__(self, *, contacts=None, error=None) -> None:
        self.contacts = contacts or []
        self.error = error
        self.calls: list[tuple[str, object, object]] = []

    def configured(self) -> bool:
        return True

    def list(self, limit=20):
        self.calls.append(("list", None, limit))
        if self.error:
            raise self.error
        return list(self.contacts)

    def search(self, query, limit=20):
        self.calls.append(("search", query, limit))
        if self.error:
            raise self.error
        return list(self.contacts)


def _contact(name: str = "Alice Smith") -> Contact:
    return Contact(
        resource_name="people/c1",
        name=name,
        emails=("alice@co.com",),
        phones=("+1-555-0100",),
        organization="Acme Corp",
        title="VP Engineering",
    )


def test_tool_is_registered_in_its_own_toolset():
    entry = registry.get_entry("google_contacts")
    assert entry is not None
    assert entry.toolset == "google_contacts"
    assert entry.schema["name"] == "google_contacts"
    assert entry.schema["parameters"]["properties"]["action"]["enum"] == ["search", "list"]
    assert entry.schema["parameters"]["required"] == ["action"]


def test_tool_is_read_only():
    entry = registry.get_entry("google_contacts")
    params = entry.schema["parameters"]["properties"]
    assert set(params) == {"action", "query", "limit"}
    description = entry.schema["description"].lower()
    assert "read-only" in description
    assert "cannot create" in description
    assert "delete" in description
    assert "message contacts" in description


def test_dispatch_list_returns_bounded_json():
    fake = _FakeConnector(contacts=[_contact()])
    raw = registry.dispatch(
        "google_contacts", {"action": "list", "limit": 3},
        connector_factory=lambda: fake)
    payload = json.loads(raw)

    assert payload["source"] == "google_contacts"
    assert payload["action"] == "list"
    assert payload["count"] == 1
    assert payload["contacts"][0]["name"] == "Alice Smith"
    assert payload["contacts"][0]["emails"] == ["alice@co.com"]
    assert "query" not in payload
    assert fake.calls == [("list", None, 3)]


def test_dispatch_search_passes_query_and_limit():
    fake = _FakeConnector(contacts=[_contact("Bob")])
    raw = registry.dispatch(
        "google_contacts", {"action": "search", "query": "bob", "limit": 5},
        connector_factory=lambda: fake)
    payload = json.loads(raw)

    assert payload["action"] == "search"
    assert payload["query"] == "bob"
    assert fake.calls == [("search", "bob", 5)]


def test_dispatch_search_without_query_is_a_typed_error():
    raw = registry.dispatch(
        "google_contacts", {"action": "search"},
        connector_factory=lambda: _FakeConnector())
    payload = json.loads(raw)
    assert payload["reason"] == "invalid_request"
    assert "query" in payload["error"]


def test_dispatch_rejects_unknown_write_action():
    raw = registry.dispatch(
        "google_contacts", {"action": "delete"},
        connector_factory=lambda: _FakeConnector())
    payload = json.loads(raw)
    assert payload["reason"] == "invalid_request"
    assert "read-only" in payload["error"]


def test_dispatch_missing_credential_keeps_the_typed_reason():
    error = GoogleContactsError(
        REASON_MISSING_CREDENTIAL,
        "google_token.json not found; run the google-workspace setup")
    raw = registry.dispatch(
        "google_contacts", {"action": "list"},
        connector_factory=lambda: _FakeConnector(error=error))
    payload = json.loads(raw)
    assert payload["reason"] == "missing_credential"
    assert "google-workspace setup" in payload["error"]


def test_dispatch_missing_scope_names_the_scope():
    error = GoogleContactsError(
        "missing_scope",
        "the stored Google token is missing the Google Contacts scope",
        scope="https://www.googleapis.com/auth/contacts.readonly")
    raw = registry.dispatch(
        "google_contacts", {"action": "search", "query": "alice"},
        connector_factory=lambda: _FakeConnector(error=error))
    payload = json.loads(raw)
    assert payload["reason"] == "missing_scope"
    assert payload["scope"] == "https://www.googleapis.com/auth/contacts.readonly"


def test_dispatch_unexpected_failure_does_not_leak_the_exception():
    class _Broken(_FakeConnector):
        def list(self, limit=20):
            raise RuntimeError("secret ya29.SUPER_SECRET_TOKEN")

    raw = registry.dispatch(
        "google_contacts", {"action": "list"},
        connector_factory=lambda: _Broken())
    payload = json.loads(raw)
    assert payload["reason"] == "tool_error"
    assert "ya29.SUPER_SECRET_TOKEN" not in raw


def test_check_fn_false_without_a_token(monkeypatch, tmp_path):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    assert _google_contacts_configured() is False


def test_check_fn_true_with_the_existing_token(monkeypatch, tmp_path):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    (tmp_path / "google_token.json").write_text(
        json.dumps({"type": "authorized_user", "token": "t", "refresh_token": "r"}),
        encoding="utf-8")
    assert _google_contacts_configured() is True


def test_default_factory_is_the_real_connector(monkeypatch):
    seen: list[object] = []

    def fake_list(self, limit=20):
        seen.append(self)
        return []

    monkeypatch.setattr(GoogleContactsConnector, "list", fake_list)
    raw = registry.dispatch("google_contacts", {"action": "list"})
    payload = json.loads(raw)
    assert payload["count"] == 0
    assert seen and isinstance(seen[0], GoogleContactsConnector)
