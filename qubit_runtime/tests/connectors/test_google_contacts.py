"""Hermetic tests for the read-only Google Contacts connector.

No live network: every People API call goes through an injected fake ``http_get``.
The token is a throwaway authorized-user payload under ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from connectors.google_contacts import (
    CONTACTS_FULL_SCOPE,
    CONTACTS_SCOPE,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    GoogleContactsConnector,
    GoogleContactsError,
)

FUTURE = "2099-01-01T00:00:00Z"
ACCESS_TOKEN = "ya29.SUPER_SECRET_ACCESS_TOKEN"


def _write_token(
    tmp_path: Path,
    *,
    scopes: object = (CONTACTS_SCOPE,),
    token: str = ACCESS_TOKEN,
    expiry: str = FUTURE,
    refresh_token: str = "rt-secret",
    **extra: object,
) -> Path:
    payload = {
        "type": "authorized_user",
        "token": token,
        "refresh_token": refresh_token,
        "client_id": "client-id.apps.googleusercontent.com",
        "client_secret": "client-secret",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": scopes,
        "expiry": expiry,
    }
    payload.update(extra)
    path = tmp_path / "google_token.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _Response:
    def __init__(self, status: int, payload: object = None, *, text: str = "") -> None:
        self.status_code = status
        self._payload = payload
        self.text = text
        self.headers: dict[str, str] = {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeGet:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def __call__(self, url, *, params, headers, timeout):
        self.calls.append({
            "url": url,
            "params": dict(params),
            "headers": dict(headers),
            "timeout": timeout,
        })
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _person(
    resource: str,
    name: str = "",
    emails: tuple[str, ...] = (),
    phones: tuple[str, ...] = (),
    org: str = "",
    title: str = "",
) -> dict:
    person: dict = {"resourceName": resource}
    if name:
        person["names"] = [{"displayName": name}]
    if emails:
        person["emailAddresses"] = [{"value": email} for email in emails]
    if phones:
        person["phoneNumbers"] = [{"value": phone} for phone in phones]
    if org or title:
        person["organizations"] = [{"name": org, "title": title}]
    return person


def _connections(*people: dict, next_page: str | None = None) -> dict:
    data: dict = {"connections": list(people)}
    if next_page:
        data["nextPageToken"] = next_page
    return data


def _search_results(*people: dict, next_page: str | None = None) -> dict:
    data: dict = {"results": [{"person": person} for person in people]}
    if next_page:
        data["nextPageToken"] = next_page
    return data


def _connector(tmp_path: Path, fake_get: _FakeGet, **kwargs) -> GoogleContactsConnector:
    token = kwargs.pop("token_path", None)
    return GoogleContactsConnector(
        token_path=token or _write_token(tmp_path), http_get=fake_get, **kwargs)


# -- configured / missing credential ---------------------------------------


def test_configured_false_without_token(tmp_path):
    connector = GoogleContactsConnector(token_path=tmp_path / "google_token.json")
    assert connector.configured() is False


def test_configured_true_with_token(tmp_path):
    connector = GoogleContactsConnector(token_path=_write_token(tmp_path))
    assert connector.configured() is True


def test_missing_token_is_a_typed_missing_credential(tmp_path):
    connector = GoogleContactsConnector(token_path=tmp_path / "google_token.json")
    with pytest.raises(GoogleContactsError) as raised:
        connector.list()
    assert raised.value.reason == "missing_credential"
    assert "google-workspace setup" in raised.value.detail
    assert ACCESS_TOKEN not in str(raised.value)


def test_corrupt_token_is_a_typed_missing_credential(tmp_path):
    path = tmp_path / "google_token.json"
    path.write_text("{not json", encoding="utf-8")
    connector = GoogleContactsConnector(token_path=path)
    with pytest.raises(GoogleContactsError) as raised:
        connector.list()
    assert raised.value.reason == "missing_credential"

def test_unreadable_token_bytes_are_a_typed_missing_credential(tmp_path):
    path = tmp_path / "google_token.json"
    path.write_bytes(b"\xff\xfe\x00not-utf8")
    connector = GoogleContactsConnector(token_path=path)
    with pytest.raises(GoogleContactsError) as raised:
        connector.list()
    assert raised.value.reason == "missing_credential"


# -- list ------------------------------------------------------------------


def test_list_maps_fields_and_never_returns_raw_payload(tmp_path):
    fake = _FakeGet(_Response(200, _connections(
        _person("people/c1", "Alice Smith", ("alice@co.com",),
                ("+1-555-0100",), "Acme Corp", "VP Engineering"),
        _person("people/c2", "Bob Jones", ("bob@co.com",), (), "Acme Corp", "Designer"),
    )))
    contacts = _connector(tmp_path, fake).list()

    assert [contact.name for contact in contacts] == ["Alice Smith", "Bob Jones"]
    first = contacts[0]
    assert first.resource_name == "people/c1"
    assert first.emails == ("alice@co.com",)
    assert first.phones == ("+1-555-0100",)
    assert first.organization == "Acme Corp"
    assert first.title == "VP Engineering"
    assert "connections" not in first.as_dict()
    assert fake.calls[0]["url"].endswith("/people/me/connections")
    assert fake.calls[0]["params"]["personFields"] == (
        "names,emailAddresses,phoneNumbers,organizations")
    assert fake.calls[0]["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"


def test_list_bounds_limit_and_field_lengths(tmp_path):
    long_name = "N" * 500
    long_email = "e" * 1000
    fake = _FakeGet(_Response(200, _connections(
        _person(
            "people/c1",
            long_name,
            tuple(f"user{i}@example.com" for i in range(10)),
            tuple(f"+1-555-{i:04d}" for i in range(10)),
            "O" * 500,
            "T" * 500,
        ),
    )))
    contacts = _connector(tmp_path, fake).list(limit=999)

    assert len(contacts) == 1
    contact = contacts[0]
    assert len(contact.name) <= 200
    assert len(contact.organization) <= 200
    assert len(contact.title) <= 200
    assert len(contact.emails) == 5
    assert all(len(email) <= 320 for email in contact.emails)
    assert len(contact.phones) == 5
    assert fake.calls[0]["params"]["pageSize"] == MAX_LIMIT


def test_list_paginates_until_limit(tmp_path):
    fake = _FakeGet(
        _Response(200, _connections(
            _person("people/c1", "Alice"), _person("people/c2", "Bob"),
            next_page="page-2")),
        _Response(200, _connections(_person("people/c3", "Carol"))),
    )
    contacts = _connector(tmp_path, fake).list(limit=3)

    assert [contact.name for contact in contacts] == ["Alice", "Bob", "Carol"]
    assert len(fake.calls) == 2
    assert fake.calls[0]["params"]["pageSize"] == 3
    assert "pageToken" not in fake.calls[0]["params"]
    assert fake.calls[1]["params"]["pageToken"] == "page-2"
    assert fake.calls[1]["params"]["pageSize"] == 1


def test_list_stops_when_api_repeats_a_page_token(tmp_path):
    fake = _FakeGet(
        _Response(200, _connections(next_page="same")),
        _Response(200, _connections(next_page="same")),
    )
    assert _connector(tmp_path, fake).list(limit=10) == []
    assert len(fake.calls) == 2


def test_list_empty_result_is_empty_not_an_error(tmp_path):
    fake = _FakeGet(_Response(200, {"connections": []}))
    assert _connector(tmp_path, fake).list() == []


def test_zero_limit_makes_no_request(tmp_path):
    fake = _FakeGet()
    assert _connector(tmp_path, fake).list(limit=0) == []
    assert fake.calls == []


def test_people_without_resource_name_are_skipped(tmp_path):
    fake = _FakeGet(_Response(200, {"connections": [
        {"names": [{"displayName": "No Id"}]},
        _person("people/c2", "Kept"),
    ]}))
    contacts = _connector(tmp_path, fake).list()
    assert [contact.name for contact in contacts] == ["Kept"]


# -- search ----------------------------------------------------------------


def test_search_warms_then_maps_results(tmp_path):
    fake = _FakeGet(
        _Response(200, {"results": []}),
        _Response(200, _search_results(
            _person("people/c1", "Alice Smith", ("alice@co.com",)))),
    )
    contacts = _connector(tmp_path, fake).search("alice", limit=5)

    assert [contact.name for contact in contacts] == ["Alice Smith"]
    warmup, call = fake.calls
    assert warmup["url"].endswith("/people:searchContacts")
    assert warmup["params"]["query"] == ""
    assert warmup["params"]["pageSize"] == 1
    assert warmup["params"]["readMask"] == "names,emailAddresses,phoneNumbers,organizations"
    assert call["url"].endswith("/people:searchContacts")
    assert call["params"]["query"] == "alice"
    assert call["params"]["pageSize"] == 5
    assert call["params"]["readMask"] == "names,emailAddresses,phoneNumbers,organizations"


def test_search_warmup_runs_once_per_connector(tmp_path):
    fake = _FakeGet(
        _Response(200, {"results": []}),
        _Response(200, _search_results(_person("people/c1", "Alice"))),
        _Response(200, _search_results(_person("people/c2", "Bob"))),
    )
    connector = _connector(tmp_path, fake)

    assert [c.name for c in connector.search("a")] == ["Alice"]
    assert [c.name for c in connector.search("b")] == ["Bob"]
    assert [call["params"]["query"] for call in fake.calls] == ["", "a", "b"]


def test_search_caps_limit_at_the_people_api_maximum(tmp_path):
    many = [_person(f"people/c{i}", f"Person {i}") for i in range(35)]
    fake = _FakeGet(
        _Response(200, {"results": []}),
        _Response(200, _search_results(*many)),
    )
    contacts = _connector(tmp_path, fake).search("person", limit=100)

    assert len(contacts) == 30
    assert fake.calls[1]["params"]["pageSize"] == 30


def test_search_requires_a_query(tmp_path):
    connector = _connector(tmp_path, _FakeGet())
    with pytest.raises(GoogleContactsError) as raised:
        connector.search("   ")
    assert raised.value.reason == "invalid_request"


def test_search_empty_result_is_empty_not_an_error(tmp_path):
    fake = _FakeGet(
        _Response(200, {"results": []}),
        _Response(200, {"results": []}),
    )
    assert _connector(tmp_path, fake).search("nobody") == []


# -- scope -----------------------------------------------------------------


def test_missing_contacts_scope_is_typed_and_names_the_scope(tmp_path):
    fake = _FakeGet()
    connector = _connector(
        tmp_path, fake,
        token_path=_write_token(
            tmp_path,
            scopes=("https://www.googleapis.com/auth/gmail.readonly",),
        ))
    with pytest.raises(GoogleContactsError) as raised:
        connector.search("alice")
    error = raised.value
    assert error.reason == "missing_scope"
    assert error.scope == CONTACTS_SCOPE
    assert CONTACTS_SCOPE in error.detail
    assert fake.calls == []  # never turns a missing scope into an empty result


def test_space_delimited_scope_string_is_understood(tmp_path):
    fake = _FakeGet(_Response(200, {"connections": []}))
    token = _write_token(tmp_path, scopes=f"{CONTACTS_SCOPE} openid")
    assert _connector(tmp_path, fake, token_path=token).list() == []


def test_full_contacts_scope_satisfies_the_readonly_check(tmp_path):
    fake = _FakeGet(_Response(200, {"connections": []}))
    token = _write_token(tmp_path, scopes=(CONTACTS_FULL_SCOPE,))
    assert _connector(tmp_path, fake, token_path=token).list() == []


def test_missing_scope_field_is_not_treated_as_a_silent_success(tmp_path):
    fake = _FakeGet(_Response(403, {"error": {"message": "forbidden"}}))
    token = _write_token(tmp_path, scopes=None)
    payload = json.loads(token.read_text(encoding="utf-8"))
    payload.pop("scopes")
    token.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(GoogleContactsError) as raised:
        _connector(tmp_path, fake, token_path=token).list()
    assert raised.value.reason == "forbidden"


# -- HTTP failures ---------------------------------------------------------


@pytest.mark.parametrize(("status", "reason"), [
    (401, "unauthorized"),
    (403, "forbidden"),
    (429, "rate_limited"),
    (500, "http_error"),
])
def test_http_failures_have_distinct_typed_reasons(tmp_path, status, reason):
    fake = _FakeGet(_Response(status, {"error": "nope"}, text=f"token {ACCESS_TOKEN}"))
    connector = _connector(tmp_path, fake)

    with pytest.raises(GoogleContactsError) as raised:
        connector.list()
    error = raised.value
    assert error.reason == reason
    assert error.status == status
    assert ACCESS_TOKEN not in error.detail
    assert ACCESS_TOKEN not in str(error)
    assert "nope" not in error.detail


def test_timeout_is_a_typed_tool_error(tmp_path):
    fake = _FakeGet(TimeoutError("timed out"))
    with pytest.raises(GoogleContactsError) as raised:
        _connector(tmp_path, fake).list()
    assert raised.value.reason == "timeout"
    assert "timed out" in raised.value.detail


def test_transport_error_is_typed(tmp_path):
    fake = _FakeGet(OSError("connection refused"))
    with pytest.raises(GoogleContactsError) as raised:
        _connector(tmp_path, fake).list()
    assert raised.value.reason == "network_error"


def test_invalid_json_is_typed(tmp_path):
    fake = _FakeGet(_Response(200, ValueError("bad json")))
    with pytest.raises(GoogleContactsError) as raised:
        _connector(tmp_path, fake).list()
    assert raised.value.reason == "invalid_response"


def test_redaction_failure_fails_closed(monkeypatch, tmp_path):
    import connectors.google_contacts as module

    def boom(_text):
        raise RuntimeError("redactor exploded")

    monkeypatch.setattr("agent.redact.redact_for_egress", boom)
    error = module.GoogleContactsError("forbidden", "secret ya29.SUPER_SECRET")
    assert error.detail == "[redacted]"


# -- refresh ---------------------------------------------------------------


class _RefreshResponse:
    status = 200
    data = json.dumps({
        "access_token": "new-access-token",
        "expires_in": 3600,
        "token_type": "Bearer",
    }).encode("utf-8")
    headers: dict[str, str] = {}


class _FakeRefresh:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        return _RefreshResponse()


def test_expired_token_refreshes_and_writes_back_to_the_same_file(tmp_path):
    token = _write_token(tmp_path, expiry="2000-01-01T00:00:00Z")
    fake_get = _FakeGet(_Response(200, {"connections": []}))
    refresh = _FakeRefresh()
    connector = GoogleContactsConnector(
        token_path=token, http_get=fake_get, refresh_request=refresh)

    assert connector.list() == []
    assert refresh.calls and refresh.calls[0]["url"] == "https://oauth2.googleapis.com/token"
    assert fake_get.calls[0]["headers"]["Authorization"] == "Bearer new-access-token"
    payload = json.loads(token.read_text(encoding="utf-8"))
    assert payload["token"] == "new-access-token"
    assert payload["type"] == "authorized_user"
    assert payload["scopes"] == [CONTACTS_SCOPE]


def test_failed_refresh_is_typed_and_redacted(tmp_path):
    token = _write_token(tmp_path, expiry="2000-01-01T00:00:00Z")

    class _FailingRefresh:
        def __call__(self, **kwargs):
            raise RuntimeError(f"invalid_grant for {ACCESS_TOKEN}")

    connector = GoogleContactsConnector(
        token_path=token, http_get=_FakeGet(), refresh_request=_FailingRefresh())
    with pytest.raises(GoogleContactsError) as raised:
        connector.list()
    assert raised.value.reason == "reauthorize_required"
    assert ACCESS_TOKEN not in raised.value.detail


# -- no network ------------------------------------------------------------


def test_injected_http_get_means_no_live_network(monkeypatch, tmp_path):
    import httpx

    def forbidden(*_args, **_kwargs):
        raise AssertionError("live network must not be used")

    monkeypatch.setattr(httpx, "get", forbidden)
    fake = _FakeGet(_Response(200, _connections(_person("people/c1", "Alice"))))
    assert [contact.name for contact in _connector(tmp_path, fake).list()] == ["Alice"]


def test_defaults_are_bounded():
    assert 0 < DEFAULT_LIMIT <= MAX_LIMIT


def test_error_payload_with_http_200_is_typed(tmp_path):
    fake = _FakeGet(_Response(200, {"error": {"code": 403, "message": "denied"}}))
    conn = GoogleContactsConnector(token_path=_write_token(tmp_path), http_get=fake)
    with pytest.raises(GoogleContactsError) as exc:
        conn.list()
    assert exc.value.reason == "http_error"


def test_wrong_shaped_connections_is_invalid_response(tmp_path):
    fake = _FakeGet(_Response(200, {"connections": "oops"}))
    conn = GoogleContactsConnector(token_path=_write_token(tmp_path), http_get=fake)
    with pytest.raises(GoogleContactsError) as exc:
        conn.list()
    assert exc.value.reason == "invalid_response"


def test_wrong_shaped_search_results_is_invalid_response(tmp_path):
    fake = _FakeGet(
        _Response(200, {"results": []}),  # warmup
        _Response(200, {"results": "oops"}),
    )
    conn = GoogleContactsConnector(token_path=_write_token(tmp_path), http_get=fake)
    with pytest.raises(GoogleContactsError) as exc:
        conn.search("ada")
    assert exc.value.reason == "invalid_response"
