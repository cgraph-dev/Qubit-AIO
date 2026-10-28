"""Hermetic tests for the read-only Dropbox connector.

No live network: every Dropbox API call goes through an injected fake
``http_post``. The token is a throwaway JSON file under ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from connectors.dropbox import (
    DEFAULT_LIMIT,
    DROPBOX_API_BASE,
    MAX_LIMIT,
    MAX_PAGES,
    TOKEN_FILENAME,
    DropboxConnector,
    DropboxError,
)

ACCESS_TOKEN = "sl.SUPER_SECRET_DROPBOX_ACCESS_TOKEN"
LIST_URL = f"{DROPBOX_API_BASE}/files/list_folder"
CONTINUE_URL = f"{DROPBOX_API_BASE}/files/list_folder/continue"
METADATA_URL = f"{DROPBOX_API_BASE}/files/get_metadata"
SEARCH_URL = f"{DROPBOX_API_BASE}/files/search_v2"


def _write_token(
    tmp_path: Path,
    *,
    token: str = ACCESS_TOKEN,
    key: str = "access_token",
    delete_token: bool = False,
    **extra: object,
) -> Path:
    payload: dict[str, object] = {key: token}
    payload.update(extra)
    if delete_token:
        payload.pop(key, None)
    path = tmp_path / TOKEN_FILENAME
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _Response:
    def __init__(self, status: int, payload: object = None, *, json_error: object = None) -> None:
        self.status_code = status
        self._payload = payload
        self._json_error = json_error
        self.text = "raw response body that must never reach an error"

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _FakePost:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def __call__(self, url, *, body, headers, timeout):
        self.calls.append({
            "url": url,
            "body": dict(body),
            "headers": dict(headers),
            "timeout": timeout,
        })
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _file(
    name: str = "report.pdf",
    path: str = "/Docs/report.pdf",
    *,
    entry_id: str = "id:file1",
    size: object = 1234,
    rev: str = "01abc",
    content_hash: str = "hash1",
) -> dict:
    return {
        ".tag": "file",
        "name": name,
        "path_display": path,
        "id": entry_id,
        "size": size,
        "client_modified": "2026-01-02T03:04:05Z",
        "server_modified": "2026-01-02T03:04:06Z",
        "rev": rev,
        "content_hash": content_hash,
        "path_lower": path.lower(),
    }


def _folder(name: str = "Docs", path: str = "/Docs", *, entry_id: str = "id:folder1") -> dict:
    return {
        ".tag": "folder",
        "name": name,
        "path_display": path,
        "id": entry_id,
        "path_lower": path.lower(),
    }


def _listing(*entries: dict, cursor: str = "", has_more: bool = False) -> dict:
    payload: dict = {"entries": list(entries), "has_more": has_more}
    if cursor:
        payload["cursor"] = cursor
    return payload


def _match(metadata: dict) -> dict:
    return {"metadata": {".tag": "metadata", "metadata": metadata}}


def _search(*matches: dict, has_more: bool = False) -> dict:
    return {"matches": list(matches), "has_more": has_more}


def _connector(tmp_path: Path, fake_post: _FakePost, **kwargs) -> DropboxConnector:
    token = kwargs.pop("token_path", None)
    return DropboxConnector(
        token_path=token or _write_token(tmp_path), http_post=fake_post, **kwargs)


# -- credential path -------------------------------------------------------


def test_default_token_path_is_under_qubit_home(monkeypatch, tmp_path):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    assert DropboxConnector().token_path == tmp_path / TOKEN_FILENAME


def test_configured_false_without_token(tmp_path):
    assert DropboxConnector(token_path=tmp_path / TOKEN_FILENAME).configured() is False


def test_configured_true_with_access_token(tmp_path):
    assert DropboxConnector(token_path=_write_token(tmp_path)).configured() is True


def test_configured_true_with_legacy_token_key(tmp_path):
    path = _write_token(tmp_path, key="token")
    assert DropboxConnector(token_path=path).configured() is True


def test_missing_token_is_a_typed_missing_credential(tmp_path):
    connector = DropboxConnector(token_path=tmp_path / TOKEN_FILENAME)
    with pytest.raises(DropboxError) as raised:
        connector.list_folder()
    assert raised.value.reason == "missing_credential"
    assert TOKEN_FILENAME in raised.value.detail


def test_corrupt_token_is_a_typed_missing_credential(tmp_path):
    path = tmp_path / TOKEN_FILENAME
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(DropboxError) as raised:
        DropboxConnector(token_path=path).search("x")
    assert raised.value.reason == "missing_credential"


def test_unreadable_token_bytes_are_a_typed_missing_credential(tmp_path):
    path = tmp_path / TOKEN_FILENAME
    path.write_bytes(b"\xff\xfe\x00not-utf8")
    with pytest.raises(DropboxError) as raised:
        DropboxConnector(token_path=path).list_folder()
    assert raised.value.reason == "missing_credential"


def test_refresh_only_token_is_missing_without_an_oauth_flow(tmp_path):
    path = _write_token(tmp_path, delete_token=True, refresh_token="refresh-secret")
    with pytest.raises(DropboxError) as raised:
        DropboxConnector(token_path=path).list_folder()
    assert raised.value.reason == "missing_credential"
    assert "OAuth" in raised.value.detail
    assert "refresh-secret" not in str(raised.value)


def test_missing_credential_never_returns_an_empty_list(tmp_path):
    connector = DropboxConnector(token_path=tmp_path / TOKEN_FILENAME)
    with pytest.raises(DropboxError):
        connector.search("anything")


# -- list_folder -----------------------------------------------------------


def test_list_maps_bounded_fields_and_never_returns_raw_payload(tmp_path):
    fake = _FakePost(_Response(200, _listing(
        _file(), _folder(),
    )))
    entries = _connector(tmp_path, fake).list_folder()

    assert [entry.entry_type for entry in entries] == ["file", "folder"]
    first = entries[0]
    assert first.name == "report.pdf"
    assert first.path == "/Docs/report.pdf"
    assert first.entry_id == "id:file1"
    assert first.size == 1234
    assert first.rev == "01abc"
    assert first.content_hash == "hash1"
    payload = first.as_dict()
    assert "path_lower" not in payload
    assert "entries" not in payload
    assert payload["size"] == 1234
    assert "size" not in entries[1].as_dict()
    assert "rev" not in entries[1].as_dict()

    call = fake.calls[0]
    assert call["url"] == LIST_URL
    assert call["body"]["path"] == ""
    assert call["body"]["recursive"] is False
    assert call["body"]["include_deleted"] is False
    assert call["body"]["limit"] == DEFAULT_LIMIT
    assert call["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert call["headers"]["Content-Type"] == "application/json"


def test_list_bounds_limit_and_field_lengths(tmp_path):
    long = "x" * 2000
    fake = _FakePost(_Response(200, _listing(
        _file(long, "/" + long, entry_id=long, rev=long, content_hash=long),
    )))
    entries = _connector(tmp_path, fake).list_folder(limit=999)

    assert len(entries) == 1
    entry = entries[0]
    assert len(entry.name) <= 255
    assert len(entry.path) <= 1024
    assert len(entry.entry_id) <= 128
    assert len(entry.rev) <= 64
    assert len(entry.content_hash) <= 64
    assert fake.calls[0]["body"]["limit"] == MAX_LIMIT


def test_list_paginates_until_limit(tmp_path):
    fake = _FakePost(
        _Response(200, _listing(_file("a"), _file("b"), cursor="page-2", has_more=True)),
        _Response(200, _listing(_file("c"), has_more=False)),
    )
    entries = _connector(tmp_path, fake).list_folder(limit=3)

    assert [entry.name for entry in entries] == ["a", "b", "c"]
    assert [call["url"] for call in fake.calls] == [LIST_URL, CONTINUE_URL]
    assert fake.calls[0]["body"]["limit"] == 3
    assert fake.calls[1]["body"] == {"cursor": "page-2"}


def test_list_stops_when_the_api_repeats_a_cursor(tmp_path):
    fake = _FakePost(
        _Response(200, _listing(cursor="same", has_more=True)),
        _Response(200, _listing(cursor="same", has_more=True)),
    )
    assert _connector(tmp_path, fake).list_folder(limit=10) == []
    assert len(fake.calls) == 2


def test_list_bounds_an_endless_stream_of_fresh_cursors(tmp_path):
    class _EndlessPost:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def __call__(self, url, *, body, headers, timeout):
            self.calls.append(dict(body))
            return _Response(200, _listing(cursor=f"cursor-{len(self.calls)}", has_more=True))

    fake = _EndlessPost()
    assert _connector(tmp_path, fake).list_folder(limit=10) == []
    assert len(fake.calls) == MAX_PAGES


def test_list_skips_deleted_and_unknown_entries(tmp_path):
    fake = _FakePost(_Response(200, _listing(
        {".tag": "deleted", "name": "gone.txt", "path_display": "/gone.txt"},
        {"name": "no-tag.txt", "path_display": "/no-tag.txt"},
        _file("kept.txt", "/kept.txt"),
    )))
    entries = _connector(tmp_path, fake).list_folder()
    assert [entry.name for entry in entries] == ["kept.txt"]


def test_list_empty_result_is_empty_not_an_error(tmp_path):
    fake = _FakePost(_Response(200, _listing()))
    assert _connector(tmp_path, fake).list_folder() == []


def test_list_zero_limit_makes_no_request(tmp_path):
    fake = _FakePost()
    assert _connector(tmp_path, fake).list_folder(limit=0) == []
    assert fake.calls == []


def test_list_passes_recursive_and_path(tmp_path):
    fake = _FakePost(_Response(200, _listing()))
    _connector(tmp_path, fake).list_folder("/Docs", recursive=True, limit=5)
    assert fake.calls[0]["body"]["path"] == "/Docs"
    assert fake.calls[0]["body"]["recursive"] is True


# -- metadata --------------------------------------------------------------


def test_metadata_returns_one_bounded_entry(tmp_path):
    fake = _FakePost(_Response(200, _file()))
    entry = _connector(tmp_path, fake).get_metadata("/Docs/report.pdf")

    assert entry.entry_type == "file"
    assert entry.name == "report.pdf"
    call = fake.calls[0]
    assert call["url"] == METADATA_URL
    assert call["body"]["path"] == "/Docs/report.pdf"
    assert call["body"]["include_deleted"] is False


def test_metadata_requires_a_path(tmp_path):
    fake = _FakePost()
    with pytest.raises(DropboxError) as raised:
        _connector(tmp_path, fake).get_metadata("   ")
    assert raised.value.reason == "invalid_request"
    assert fake.calls == []


def test_metadata_not_found_is_typed(tmp_path):
    fake = _FakePost(_Response(
        409, {"error_summary": "path/not_found/..", "error": {".tag": "path"}}))
    with pytest.raises(DropboxError) as raised:
        _connector(tmp_path, fake).get_metadata("/missing")
    assert raised.value.reason == "not_found"
    assert raised.value.status == 409


def test_metadata_other_conflict_stays_a_conflict(tmp_path):
    fake = _FakePost(_Response(409, {"error_summary": "path/not_folder/.."}))
    with pytest.raises(DropboxError) as raised:
        _connector(tmp_path, fake).get_metadata("/Docs/report.pdf/child")
    assert raised.value.reason == "conflict"
    assert raised.value.status == 409


def test_metadata_unexpected_payload_is_typed(tmp_path):
    fake = _FakePost(_Response(200, {".tag": "deleted"}))
    with pytest.raises(DropboxError) as raised:
        _connector(tmp_path, fake).get_metadata("/gone")
    assert raised.value.reason == "invalid_response"


# -- search ----------------------------------------------------------------


def test_search_unwraps_the_search_v2_match_union(tmp_path):
    fake = _FakePost(_Response(200, _search(
        _match(_file("report.pdf", "/Docs/report.pdf")),
        _match(_folder("Docs", "/Docs")),
        {"metadata": {".tag": "deleted"}},
    )))
    entries = _connector(tmp_path, fake).search("report", limit=5)

    assert [entry.name for entry in entries] == ["report.pdf", "Docs"]
    call = fake.calls[0]
    assert call["url"] == SEARCH_URL
    assert call["body"]["query"] == "report"
    assert call["body"]["options"]["max_results"] == 5
    assert call["body"]["options"]["file_status"] == "active"
    assert "path" not in call["body"]["options"]


def test_search_passes_path_scope_and_caps_limit(tmp_path):
    many = [_match(_file(f"f{i}.txt", f"/f{i}.txt")) for i in range(150)]
    fake = _FakePost(_Response(200, _search(*many)))
    entries = _connector(tmp_path, fake).search("f", path="/Docs", limit=999)

    assert len(entries) == MAX_LIMIT
    assert fake.calls[0]["body"]["options"]["max_results"] == MAX_LIMIT
    assert fake.calls[0]["body"]["options"]["path"] == "/Docs"


def test_search_requires_a_query(tmp_path):
    fake = _FakePost()
    with pytest.raises(DropboxError) as raised:
        _connector(tmp_path, fake).search("   ")
    assert raised.value.reason == "invalid_request"
    assert fake.calls == []


def test_search_empty_result_is_empty_not_an_error(tmp_path):
    fake = _FakePost(_Response(200, _search()))
    assert _connector(tmp_path, fake).search("nobody") == []


def test_search_accepts_a_direct_metadata_row(tmp_path):
    fake = _FakePost(_Response(200, _search(
        {"metadata": _file("direct.txt", "/direct.txt")},
    )))
    assert [entry.name for entry in _connector(tmp_path, fake).search("direct")] == [
        "direct.txt"
    ]


# -- HTTP failures ---------------------------------------------------------


@pytest.mark.parametrize(("status", "reason"), [
    (401, "unauthorized"),
    (403, "forbidden"),
    (429, "rate_limited"),
    (500, "http_error"),
])
def test_http_failures_have_distinct_typed_reasons(tmp_path, status, reason):
    fake = _FakePost(_Response(status, {"error_summary": f"token {ACCESS_TOKEN}"}))
    connector = _connector(tmp_path, fake)

    with pytest.raises(DropboxError) as raised:
        connector.list_folder()
    error = raised.value
    assert error.reason == reason
    assert error.status == status
    assert ACCESS_TOKEN not in error.detail
    assert ACCESS_TOKEN not in str(error)
    assert "raw response body" not in error.detail


def test_bad_request_is_typed(tmp_path):
    fake = _FakePost(_Response(400, {"error_summary": "bad_args"}))
    with pytest.raises(DropboxError) as raised:
        _connector(tmp_path, fake).list_folder()
    assert raised.value.reason == "invalid_request"
    assert raised.value.status == 400


def test_timeout_is_a_typed_error(tmp_path):
    fake = _FakePost(TimeoutError("timed out"))
    with pytest.raises(DropboxError) as raised:
        _connector(tmp_path, fake).list_folder()
    assert raised.value.reason == "timeout"


def test_transport_error_is_typed(tmp_path):
    fake = _FakePost(OSError("connection refused"))
    with pytest.raises(DropboxError) as raised:
        _connector(tmp_path, fake).search("x")
    assert raised.value.reason == "network_error"


def test_invalid_json_is_typed(tmp_path):
    fake = _FakePost(_Response(200, json_error=ValueError("bad json")))
    with pytest.raises(DropboxError) as raised:
        _connector(tmp_path, fake).list_folder()
    assert raised.value.reason == "invalid_response"


def test_non_object_payload_is_typed(tmp_path):
    fake = _FakePost(_Response(200, ["not", "an", "object"]))
    with pytest.raises(DropboxError) as raised:
        _connector(tmp_path, fake).list_folder()
    assert raised.value.reason == "invalid_response"


def test_redaction_failure_fails_closed(monkeypatch):
    import connectors.dropbox as module

    def boom(_text):
        raise RuntimeError("redactor exploded")

    monkeypatch.setattr("agent.redact.redact_for_egress", boom)
    error = module.DropboxError("forbidden", f"secret {ACCESS_TOKEN}")
    assert error.detail == "[redacted]"


# -- no network ------------------------------------------------------------


def test_injected_http_post_means_no_live_network(monkeypatch, tmp_path):
    import httpx

    def forbidden(*_args, **_kwargs):
        raise AssertionError("live network must not be used")

    monkeypatch.setattr(httpx, "post", forbidden)
    fake = _FakePost(_Response(200, _listing(_file())))
    assert [entry.name for entry in _connector(tmp_path, fake).list_folder()] == [
        "report.pdf"
    ]


def test_defaults_are_bounded():
    assert 0 < DEFAULT_LIMIT <= MAX_LIMIT


def test_list_missing_entries_key_is_empty_not_an_error(tmp_path):
    fake = _FakePost(_Response(200, {"has_more": False}))
    assert _connector(tmp_path, fake).list_folder() == []


def test_list_has_more_without_a_cursor_stops(tmp_path):
    fake = _FakePost(_Response(200, _listing(_file(), has_more=True)))
    assert [entry.name for entry in _connector(tmp_path, fake).list_folder(limit=10)] == [
        "report.pdf"
    ]
    assert len(fake.calls) == 1


def test_metadata_folder_is_typed_and_has_no_size(tmp_path):
    fake = _FakePost(_Response(200, _folder()))
    entry = _connector(tmp_path, fake).get_metadata("/Docs")
    assert entry.entry_type == "folder"
    assert "size" not in entry.as_dict()


def test_search_missing_matches_key_is_empty_not_an_error(tmp_path):
    fake = _FakePost(_Response(200, {"has_more": False}))
    assert _connector(tmp_path, fake).search("x") == []
