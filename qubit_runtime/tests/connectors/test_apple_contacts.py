"""Hermetic tests for the platform-gated read-only Apple Contacts connector."""

from __future__ import annotations

import pytest

from connectors.apple_common import (
    REASON_INVALID_REQUEST,
    REASON_INVALID_RESPONSE,
    REASON_NOT_SUPPORTED,
    REASON_TOOL_ERROR,
    AppleConnectorError,
)
from connectors.apple_contacts import (
    MAX_LIMIT,
    REQUIRED_FRAMEWORK,
    AppleContactsConnector,
)


class _FakeContactsBackend:
    def __init__(self, rows=None, *, error=None):
        self.rows = rows if rows is not None else []
        self.error = error
        self.list_calls: list[dict] = []
        self.search_calls: list[dict] = []

    def list_contacts(self, *, limit):
        self.list_calls.append({"limit": limit})
        if self.error is not None:
            raise self.error
        return self.rows

    def search_contacts(self, *, query, limit):
        self.search_calls.append({"query": query, "limit": limit})
        if self.error is not None:
            raise self.error
        return self.rows

    def create_contact(self, *args, **kwargs):
        raise AssertionError("the connector must never write")


def test_non_darwin_refuses_before_touching_the_adapter():
    backend = _FakeContactsBackend([{"name": "should not be read"}])
    connector = AppleContactsConnector(platform="linux", backend=backend)

    for call in (
        lambda: connector.list_contacts(),
        lambda: connector.search_contacts("alice"),
    ):
        with pytest.raises(AppleConnectorError) as caught:
            call()
        exc = caught.value
        assert exc.reason == REASON_NOT_SUPPORTED
        assert exc.platform == "linux"
        assert exc.required_framework == REQUIRED_FRAMEWORK
        assert "linux" in exc.detail
        assert "Contacts" in exc.detail

    assert backend.list_calls == []
    assert backend.search_calls == []
    assert connector.available() is False


def test_darwin_maps_the_contact_shape_with_bounded_fields():
    backend = _FakeContactsBackend([
        {
            "identifier": "contact-1",
            "first_name": "Alice" + "x" * 300,
            "middle_name": "B",
            "last_name": "Smith",
            "organization": "Acme",
            "job_title": "Engineer",
            "department": "Platform",
            "nickname": "Al",
            "birthday": "1990-01-02T00:00:00Z",
            "emails": [
                {"value": "alice@example.com"},
                {"address": "ALICE@example.com"},
                "second@example.com",
            ],
            "phones": ["+1-555-0100", {"number": "+1-555-0100"}],
        }
    ])
    connector = AppleContactsConnector(platform="darwin", backend=backend)

    contacts = connector.list_contacts(limit=5)

    assert len(contacts) == 1
    contact = contacts[0]
    assert contact.contact_id == "contact-1"
    assert contact.name.startswith("Alice")
    assert len(contact.name) <= 200
    assert contact.first_name.startswith("Alice")
    assert len(contact.first_name) <= 200
    assert contact.last_name == "Smith"
    assert contact.organization == "Acme"
    assert contact.job_title == "Engineer"
    assert contact.department == "Platform"
    assert contact.nickname == "Al"
    assert contact.birthday == "1990-01-02"
    assert contact.emails == (
        "alice@example.com", "second@example.com")
    assert contact.phones == ("+1-555-0100",)
    assert backend.list_calls == [{"limit": 5}]
    assert connector.available() is True


def test_darwin_builds_a_display_name_when_only_components_exist():
    backend = _FakeContactsBackend([
        {"first_name": "Ada", "last_name": "Lovelace"},
        {"organization": "Analytical Engines"},
    ])
    connector = AppleContactsConnector(platform="darwin", backend=backend)

    contacts = connector.list_contacts()

    assert [contact.name for contact in contacts] == [
        "Ada Lovelace", "Analytical Engines"]


def test_darwin_search_passes_the_bounded_query_and_limit():
    backend = _FakeContactsBackend([{"name": "Alice Smith"}])
    connector = AppleContactsConnector(platform="darwin", backend=backend)

    contacts = connector.search_contacts("  Ali  ", limit=9999)

    assert [contact.name for contact in contacts] == ["Alice Smith"]
    assert backend.search_calls == [{"query": "Ali", "limit": MAX_LIMIT}]
    assert backend.list_calls == []


def test_darwin_rejects_an_empty_search_query():
    backend = _FakeContactsBackend([])
    connector = AppleContactsConnector(platform="darwin", backend=backend)

    with pytest.raises(AppleConnectorError) as caught:
        connector.search_contacts("   ")

    assert caught.value.reason == REASON_INVALID_REQUEST
    assert backend.search_calls == []


def test_darwin_skips_rows_without_a_name_or_organization():
    backend = _FakeContactsBackend([
        {"job_title": "Engineer"},
        object(),
        {"name": "Keep Me"},
    ])
    connector = AppleContactsConnector(platform="darwin", backend=backend)

    assert [contact.name for contact in connector.list_contacts()] == [
        "Keep Me"]


def test_darwin_rejects_an_unexpected_payload():
    backend = _FakeContactsBackend({"contacts": []})
    connector = AppleContactsConnector(platform="darwin", backend=backend)

    with pytest.raises(AppleConnectorError) as caught:
        connector.list_contacts()

    assert caught.value.reason == REASON_INVALID_RESPONSE


def test_darwin_redacts_a_framework_failure():
    backend = _FakeContactsBackend(
        error=RuntimeError("password=hunter2 /Users/alice/Library/AddressBook"))
    connector = AppleContactsConnector(platform="darwin", backend=backend)

    with pytest.raises(AppleConnectorError) as caught:
        connector.list_contacts()

    exc = caught.value
    assert exc.reason == REASON_TOOL_ERROR
    assert "RuntimeError" in exc.detail
    assert "hunter2" not in exc.detail
    assert "password" not in exc.detail
    assert "/Users/alice" not in exc.detail


def test_contacts_surface_has_no_write_operations():
    connector = AppleContactsConnector(platform="darwin", backend=[])
    for name in (
        "create_contact",
        "update_contact",
        "delete_contact",
        "send_message",
    ):
        assert not hasattr(connector, name)
