"""Hermetic tests for the read-only Google Calendar connector.

No live network: every Calendar API call goes through an injected fake
``http_get``. The token is a throwaway authorized-user payload under
``tmp_path``.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from connectors.google_calendar import (
    CALENDAR_FULL_SCOPE,
    CALENDAR_SCOPE,
    DEFAULT_LIMIT,
    MAX_ATTENDEE_COUNT,
    MAX_LIMIT,
    GoogleCalendarConnector,
    GoogleCalendarError,
)

FUTURE = "2099-01-01T00:00:00Z"
ACCESS_TOKEN = "ya29.SUPER_SECRET_ACCESS_TOKEN"
TIME_MIN = "2026-10-01T00:00:00Z"
TIME_MAX = "2026-11-01T00:00:00Z"


def _write_token(
    tmp_path: Path,
    *,
    scopes: object = (CALENDAR_SCOPE,),
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


def _event(
    event_id: str,
    title: str = "",
    *,
    start: object = None,
    end: object = None,
    attendees: object = None,
    status: str = "confirmed",
) -> dict:
    event: dict = {"id": event_id}
    if title:
        event["summary"] = title
    event["start"] = start if start is not None else {
        "dateTime": "2026-10-02T09:00:00Z"}
    event["end"] = end if end is not None else {
        "dateTime": "2026-10-02T10:00:00Z"}
    if attendees is not None:
        event["attendees"] = attendees
    if status:
        event["status"] = status
    return event


def _items(*events: dict, next_page: str | None = None) -> dict:
    data: dict = {"items": list(events)}
    if next_page:
        data["nextPageToken"] = next_page
    return data


def _connector(tmp_path: Path, fake_get: _FakeGet, **kwargs) -> GoogleCalendarConnector:
    token = kwargs.pop("token_path", None)
    return GoogleCalendarConnector(
        token_path=token or _write_token(tmp_path), http_get=fake_get, **kwargs)


# -- configured / missing credential ---------------------------------------


def test_configured_false_without_token(tmp_path):
    connector = GoogleCalendarConnector(token_path=tmp_path / "google_token.json")
    assert connector.configured() is False


def test_configured_true_with_token(tmp_path):
    connector = GoogleCalendarConnector(token_path=_write_token(tmp_path))
    assert connector.configured() is True


def test_missing_token_is_a_typed_missing_credential(tmp_path):
    connector = GoogleCalendarConnector(token_path=tmp_path / "google_token.json")
    with pytest.raises(GoogleCalendarError) as raised:
        connector.list_events(TIME_MIN, TIME_MAX)
    assert raised.value.reason == "missing_credential"
    assert "google-workspace setup" in raised.value.detail
    assert ACCESS_TOKEN not in str(raised.value)


def test_corrupt_token_is_a_typed_missing_credential(tmp_path):
    path = tmp_path / "google_token.json"
    path.write_text("{not json", encoding="utf-8")
    connector = GoogleCalendarConnector(token_path=path)
    with pytest.raises(GoogleCalendarError) as raised:
        connector.list_events(TIME_MIN, TIME_MAX)
    assert raised.value.reason == "missing_credential"


def test_unreadable_token_bytes_are_a_typed_missing_credential(tmp_path):
    path = tmp_path / "google_token.json"
    path.write_bytes(b"\xff\xfe\x00not-utf8")
    connector = GoogleCalendarConnector(token_path=path)
    with pytest.raises(GoogleCalendarError) as raised:
        connector.list_events(TIME_MIN, TIME_MAX)
    assert raised.value.reason == "missing_credential"


# -- list ------------------------------------------------------------------


def test_list_maps_fields_and_never_returns_raw_payload(tmp_path):
    fake = _FakeGet(_Response(200, _items(
        _event("evt-1", "Standup", attendees=[{"email": "a@co.com"}, {"email": "b@co.com"}]),
        _event("evt-2", "Holiday",
               start={"date": "2026-10-03"}, end={"date": "2026-10-04"}),
    )))
    events = _connector(tmp_path, fake).list_events(TIME_MIN, TIME_MAX)

    assert [event.event_id for event in events] == ["evt-1", "evt-2"]
    first = events[0]
    assert first.title == "Standup"
    assert first.start == "2026-10-02T09:00:00Z"
    assert first.end == "2026-10-02T10:00:00Z"
    assert first.all_day is False
    assert first.attendee_count == 2
    assert first.status == "confirmed"
    assert "items" not in first.as_dict()
    assert fake.calls[0]["url"].endswith("/calendars/primary/events")
    assert fake.calls[0]["params"]["timeMin"] == TIME_MIN
    assert fake.calls[0]["params"]["timeMax"] == TIME_MAX
    assert fake.calls[0]["params"]["singleEvents"] == "true"
    assert fake.calls[0]["params"]["orderBy"] == "startTime"
    assert "q" not in fake.calls[0]["params"]
    assert fake.calls[0]["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"

    holiday = events[1]
    assert holiday.all_day is True
    assert holiday.start == "2026-10-03"


def test_list_bounds_limit_and_field_lengths(tmp_path):
    many = [{"email": f"user{i}@example.com"} for i in range(MAX_ATTENDEE_COUNT + 50)]
    fake = _FakeGet(_Response(200, _items(_event(
        "e" * 500,
        "T" * 500,
        attendees=many,
        status="S" * 100,
    ))))
    events = _connector(tmp_path, fake).list_events(TIME_MIN, limit=999)

    assert len(events) == 1
    event = events[0]
    assert len(event.event_id) <= 256
    assert len(event.title) <= 200
    assert len(event.status) <= 32
    assert event.attendee_count == MAX_ATTENDEE_COUNT
    assert fake.calls[0]["params"]["maxResults"] == MAX_LIMIT


def test_list_omits_time_max_when_not_given(tmp_path):
    fake = _FakeGet(_Response(200, _items()))
    assert _connector(tmp_path, fake).list_events(TIME_MIN) == []
    assert "timeMax" not in fake.calls[0]["params"]


def test_list_paginates_until_limit(tmp_path):
    fake = _FakeGet(
        _Response(200, _items(
            _event("evt-1", "One"), _event("evt-2", "Two"),
            next_page="page-2")),
        _Response(200, _items(_event("evt-3", "Three"))),
    )
    events = _connector(tmp_path, fake).list_events(TIME_MIN, TIME_MAX, limit=3)

    assert [event.title for event in events] == ["One", "Two", "Three"]
    assert len(fake.calls) == 2
    assert fake.calls[0]["params"]["maxResults"] == 3
    assert "pageToken" not in fake.calls[0]["params"]
    assert fake.calls[1]["params"]["pageToken"] == "page-2"
    assert fake.calls[1]["params"]["maxResults"] == 1


def test_list_stops_when_api_repeats_a_page_token(tmp_path):
    fake = _FakeGet(
        _Response(200, _items(next_page="same")),
        _Response(200, _items(next_page="same")),
    )
    assert _connector(tmp_path, fake).list_events(TIME_MIN, limit=10) == []
    assert len(fake.calls) == 2


def test_list_empty_result_is_empty_not_an_error(tmp_path):
    fake = _FakeGet(_Response(200, {"items": []}))
    assert _connector(tmp_path, fake).list_events(TIME_MIN) == []


def test_missing_items_key_is_empty_not_an_error(tmp_path):
    fake = _FakeGet(_Response(200, {}))
    assert _connector(tmp_path, fake).list_events(TIME_MIN) == []


def test_malformed_items_payload_is_a_typed_error(tmp_path):
    fake = _FakeGet(_Response(200, {"items": "not-a-list"}))
    with pytest.raises(GoogleCalendarError) as raised:
        _connector(tmp_path, fake).list_events(TIME_MIN)
    assert raised.value.reason == "invalid_response"


def test_events_without_an_id_are_skipped(tmp_path):
    fake = _FakeGet(_Response(200, {"items": [
        {"summary": "No id", "start": {"dateTime": "2026-10-02T09:00:00Z"}},
        _event("evt-2", "Kept"),
    ]}))
    events = _connector(tmp_path, fake).list_events(TIME_MIN)
    assert [event.title for event in events] == ["Kept"]


def test_zero_limit_makes_no_request(tmp_path):
    fake = _FakeGet()
    assert _connector(tmp_path, fake).list_events(TIME_MIN, TIME_MAX, limit=0) == []
    assert fake.calls == []


def test_list_requires_time_min(tmp_path):
    fake = _FakeGet()
    connector = _connector(tmp_path, fake)
    for value in ("", "   ", None):
        with pytest.raises(GoogleCalendarError) as raised:
            connector.list_events(value)
        assert raised.value.reason == "invalid_request"
    assert fake.calls == []


def test_calendar_id_is_url_encoded(tmp_path):
    fake = _FakeGet(_Response(200, _items()))
    _connector(tmp_path, fake).list_events(
        TIME_MIN, calendar_id="team/ops@example.com")
    assert fake.calls[0]["url"].endswith(
        "/calendars/team%2Fops%40example.com/events")


# -- search ----------------------------------------------------------------


def test_search_maps_to_the_events_q_parameter(tmp_path):
    fake = _FakeGet(_Response(200, _items(_event("evt-1", "Board meeting"))))
    events = _connector(tmp_path, fake).search(
        "board", TIME_MIN, TIME_MAX, limit=5)

    assert [event.title for event in events] == ["Board meeting"]
    call = fake.calls[0]
    assert call["url"].endswith("/calendars/primary/events")
    assert call["params"]["q"] == "board"
    assert call["params"]["timeMin"] == TIME_MIN
    assert call["params"]["timeMax"] == TIME_MAX
    assert call["params"]["maxResults"] == 5


def test_search_requires_a_query(tmp_path):
    fake = _FakeGet()
    connector = _connector(tmp_path, fake)
    with pytest.raises(GoogleCalendarError) as raised:
        connector.search("   ", TIME_MIN)
    assert raised.value.reason == "invalid_request"
    assert fake.calls == []


def test_search_empty_result_is_empty_not_an_error(tmp_path):
    fake = _FakeGet(_Response(200, _items()))
    assert _connector(tmp_path, fake).search("nobody", TIME_MIN) == []


# -- scope -----------------------------------------------------------------


def test_missing_calendar_scope_is_typed_and_names_the_scope(tmp_path):
    fake = _FakeGet()
    connector = _connector(
        tmp_path, fake,
        token_path=_write_token(
            tmp_path,
            scopes=("https://www.googleapis.com/auth/gmail.readonly",),
        ))
    with pytest.raises(GoogleCalendarError) as raised:
        connector.list_events(TIME_MIN)
    error = raised.value
    assert error.reason == "missing_scope"
    assert error.scope == CALENDAR_SCOPE
    assert CALENDAR_SCOPE in error.detail
    assert fake.calls == []  # never turns a missing scope into an empty result


def test_space_delimited_scope_string_is_understood(tmp_path):
    fake = _FakeGet(_Response(200, _items()))
    token = _write_token(tmp_path, scopes=f"{CALENDAR_SCOPE} openid")
    assert _connector(tmp_path, fake, token_path=token).list_events(TIME_MIN) == []


def test_full_calendar_scope_satisfies_the_readonly_check(tmp_path):
    fake = _FakeGet(_Response(200, _items()))
    token = _write_token(tmp_path, scopes=(CALENDAR_FULL_SCOPE,))
    assert _connector(tmp_path, fake, token_path=token).list_events(TIME_MIN) == []


def test_missing_scope_field_is_not_treated_as_a_silent_success(tmp_path):
    fake = _FakeGet(_Response(403, {"error": {"message": "forbidden"}}))
    token = _write_token(tmp_path, scopes=None)
    payload = json.loads(token.read_text(encoding="utf-8"))
    payload.pop("scopes")
    token.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(GoogleCalendarError) as raised:
        _connector(tmp_path, fake, token_path=token).list_events(TIME_MIN)
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

    with pytest.raises(GoogleCalendarError) as raised:
        connector.list_events(TIME_MIN)
    error = raised.value
    assert error.reason == reason
    assert error.status == status
    assert ACCESS_TOKEN not in error.detail
    assert ACCESS_TOKEN not in str(error)
    assert "nope" not in error.detail


def test_timeout_is_a_typed_tool_error(tmp_path):
    fake = _FakeGet(TimeoutError("timed out"))
    with pytest.raises(GoogleCalendarError) as raised:
        _connector(tmp_path, fake).list_events(TIME_MIN)
    assert raised.value.reason == "timeout"
    assert "timed out" in raised.value.detail


def test_transport_error_is_typed(tmp_path):
    fake = _FakeGet(OSError("connection refused"))
    with pytest.raises(GoogleCalendarError) as raised:
        _connector(tmp_path, fake).list_events(TIME_MIN)
    assert raised.value.reason == "network_error"


def test_invalid_json_is_typed(tmp_path):
    fake = _FakeGet(_Response(200, ValueError("bad json")))
    with pytest.raises(GoogleCalendarError) as raised:
        _connector(tmp_path, fake).list_events(TIME_MIN)
    assert raised.value.reason == "invalid_response"


def test_non_object_payload_is_typed(tmp_path):
    fake = _FakeGet(_Response(200, []))
    with pytest.raises(GoogleCalendarError) as raised:
        _connector(tmp_path, fake).list_events(TIME_MIN)
    assert raised.value.reason == "invalid_response"


def test_redaction_failure_fails_closed(monkeypatch, tmp_path):
    import connectors.google_calendar as module

    def boom(_text):
        raise RuntimeError("redactor exploded")

    monkeypatch.setattr("agent.redact.redact_for_egress", boom)
    error = module.GoogleCalendarError("forbidden", "secret ya29.SUPER_SECRET")
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
    fake_get = _FakeGet(_Response(200, {"items": []}))
    refresh = _FakeRefresh()
    connector = GoogleCalendarConnector(
        token_path=token, http_get=fake_get, refresh_request=refresh)

    assert connector.list_events(TIME_MIN) == []
    assert refresh.calls and refresh.calls[0]["url"] == "https://oauth2.googleapis.com/token"
    assert fake_get.calls[0]["headers"]["Authorization"] == "Bearer new-access-token"
    payload = json.loads(token.read_text(encoding="utf-8"))
    assert payload["token"] == "new-access-token"
    assert payload["type"] == "authorized_user"
    assert payload["scopes"] == [CALENDAR_SCOPE]


def test_concurrent_refresh_reuses_the_token_another_process_wrote(tmp_path):
    """Two callers that both saw an expired token must not both refresh.

    Each thread starts from the same stale payload. The shared advisory lock
    admits one refresh; the other re-reads the atomically written token and
    returns it without touching Google.
    """
    token = _write_token(tmp_path, expiry="2000-01-01T00:00:00Z")
    stale_payload = json.loads(token.read_text(encoding="utf-8"))
    fake_get = _FakeGet(_Response(200, {"items": []}))
    refresh = _FakeRefresh()
    connector = GoogleCalendarConnector(
        token_path=token, http_get=fake_get, refresh_request=refresh)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def worker() -> None:
        barrier.wait()
        results.append(connector._access_token(dict(stale_payload)))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert results == ["new-access-token", "new-access-token"]
    assert len(refresh.calls) == 1
    payload = json.loads(token.read_text(encoding="utf-8"))
    assert payload["token"] == "new-access-token"


def test_failed_refresh_is_typed_and_redacted(tmp_path):
    token = _write_token(tmp_path, expiry="2000-01-01T00:00:00Z")

    class _FailingRefresh:
        def __call__(self, **kwargs):
            raise RuntimeError(f"invalid_grant for {ACCESS_TOKEN}")

    connector = GoogleCalendarConnector(
        token_path=token, http_get=_FakeGet(), refresh_request=_FailingRefresh())
    with pytest.raises(GoogleCalendarError) as raised:
        connector.list_events(TIME_MIN)
    assert raised.value.reason == "reauthorize_required"
    assert ACCESS_TOKEN not in raised.value.detail


# -- no network ------------------------------------------------------------


def test_injected_http_get_means_no_live_network(monkeypatch, tmp_path):
    import httpx

    def forbidden(*_args, **_kwargs):
        raise AssertionError("live network must not be used")

    monkeypatch.setattr(httpx, "get", forbidden)
    fake = _FakeGet(_Response(200, _items(_event("evt-1", "One"))))
    assert [event.title for event in _connector(tmp_path, fake).list_events(TIME_MIN)] == [
        "One"]


def test_defaults_are_bounded():
    assert 0 < DEFAULT_LIMIT <= MAX_LIMIT
