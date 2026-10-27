"""Hermetic tests for the unwired read-only Dropbox tool definition."""

from __future__ import annotations

import json

from connectors.dropbox import (
    DropboxEntry,
    DropboxError,
    REASON_MISSING_CREDENTIAL,
)
from tools.dropbox_tool import (
    DROPBOX_TOOL_SCHEMA,
    build_dropbox_tool,
    dropbox_tool_configured,
    dropbox_tool_handler,
)
from tools.registry import registry


class _FakeConnector:
    def __init__(self, *, entries=None, error=None) -> None:
        self.entries = entries or []
        self.error = error
        self.calls: list[tuple[str, object, object, object]] = []

    def configured(self) -> bool:
        return True

    def list_folder(self, path="", *, limit=20, recursive=False):
        self.calls.append(("list_folder", path, limit, recursive))
        if self.error:
            raise self.error
        return list(self.entries)

    def get_metadata(self, path):
        self.calls.append(("metadata", path, None, None))
        if self.error:
            raise self.error
        return self.entries[0]

    def search(self, query, *, path="", limit=20):
        self.calls.append(("search", query, path, limit))
        if self.error:
            raise self.error
        return list(self.entries)


def _entry(name: str = "report.pdf") -> DropboxEntry:
    return DropboxEntry(
        entry_type="file",
        name=name,
        path=f"/Docs/{name}",
        entry_id="id:file1",
        size=1234,
        client_modified="2026-01-02T03:04:05Z",
        server_modified="2026-01-02T03:04:06Z",
        rev="01abc",
        content_hash="hash1",
    )


def _dispatch(args, *, fake=None):
    fake = fake or _FakeConnector(entries=[_entry()])
    return json.loads(
        dropbox_tool_handler(args, connector_factory=lambda: fake)), fake


def test_tool_is_registered_in_its_own_toolset():
    entry = registry.get_entry("dropbox")
    assert entry is not None
    assert entry.toolset == "dropbox"
    assert entry.schema is DROPBOX_TOOL_SCHEMA
    assert entry.handler is dropbox_tool_handler
    assert entry.check_fn is dropbox_tool_configured


def test_schema_is_read_only_and_has_no_write_actions():
    params = DROPBOX_TOOL_SCHEMA["parameters"]["properties"]
    assert set(params) == {"action", "path", "query", "recursive", "limit"}
    assert params["action"]["enum"] == ["list_folder", "metadata", "search"]
    assert DROPBOX_TOOL_SCHEMA["parameters"]["required"] == ["action"]
    description = DROPBOX_TOOL_SCHEMA["description"].lower()
    assert "read-only" in description
    for forbidden in ("upload", "create", "move", "copy", "delete", "share"):
        assert forbidden in description
    assert "cannot" in description


def test_list_folder_returns_bounded_json():
    payload, fake = _dispatch({"action": "list_folder", "path": "/Docs", "limit": 3})

    assert payload["source"] == "dropbox"
    assert payload["action"] == "list_folder"
    assert payload["count"] == 1
    assert payload["entries"][0]["name"] == "report.pdf"
    assert payload["entries"][0]["size"] == 1234
    assert payload["path"] == "/Docs"
    assert payload["recursive"] is False
    assert fake.calls == [("list_folder", "/Docs", 3, False)]


def test_list_folder_passes_recursive():
    payload, fake = _dispatch(
        {"action": "list_folder", "recursive": True, "limit": 5})
    assert payload["recursive"] is True
    assert fake.calls == [("list_folder", "", 5, True)]


def test_search_passes_query_path_and_limit():
    payload, fake = _dispatch(
        {"action": "search", "query": "report", "path": "/Docs", "limit": 7})

    assert payload["action"] == "search"
    assert payload["query"] == "report"
    assert payload["path"] == "/Docs"
    assert fake.calls == [("search", "report", "/Docs", 7)]


def test_metadata_returns_one_entry():
    payload, fake = _dispatch({"action": "metadata", "path": "/Docs/report.pdf"})

    assert payload["action"] == "metadata"
    assert payload["count"] == 1
    assert payload["entries"][0]["path"] == "/Docs/report.pdf"
    assert fake.calls == [("metadata", "/Docs/report.pdf", None, None)]


def test_search_without_query_is_a_typed_error():
    payload, fake = _dispatch({"action": "search"})
    assert payload["reason"] == "invalid_request"
    assert "query" in payload["error"]
    assert fake.calls == []


def test_metadata_without_path_is_a_typed_error():
    payload, fake = _dispatch({"action": "metadata"})
    assert payload["reason"] == "invalid_request"
    assert "path" in payload["error"]
    assert fake.calls == []


def test_unknown_write_action_is_rejected():
    for action in ("upload", "delete", "create", "share", "move", "copy"):
        payload, fake = _dispatch({"action": action})
        assert payload["reason"] == "invalid_request"
        assert "read-only" in payload["error"]
        assert fake.calls == []


def test_missing_action_is_rejected():
    payload, _ = _dispatch({})
    assert payload["reason"] == "invalid_request"
    assert "read-only" in payload["error"]


def test_non_object_arguments_are_rejected():
    raw = dropbox_tool_handler(["list_folder"])
    payload = json.loads(raw)
    assert payload["reason"] == "invalid_request"


def test_missing_credential_keeps_the_typed_reason():
    error = DropboxError(
        REASON_MISSING_CREDENTIAL,
        "dropbox_token.json not found; provide a Dropbox access token")
    payload, _ = _dispatch(
        {"action": "list_folder"},
        fake=_FakeConnector(error=error))
    assert payload["reason"] == "missing_credential"
    assert "dropbox_token.json" in payload["error"]


def test_http_status_is_preserved_in_the_error_payload():
    error = DropboxError("rate_limited", "retry later", status=429)
    payload, _ = _dispatch({"action": "list_folder"}, fake=_FakeConnector(error=error))
    assert payload["reason"] == "rate_limited"
    assert payload["status"] == 429


def test_generic_failure_does_not_leak_the_exception_body():
    class _Boom(_FakeConnector):
        def list_folder(self, *_args, **_kwargs):
            raise RuntimeError("secret internal detail")

    raw = dropbox_tool_handler(
        {"action": "list_folder"}, connector_factory=_Boom)
    payload = json.loads(raw)
    assert payload["reason"] == "tool_error"
    assert "secret internal detail" not in raw


def test_build_dropbox_tool_returns_a_definition_without_replacing_the_registered_tool():
    before = registry.get_entry("dropbox")
    fake = _FakeConnector(entries=[_entry("built.txt")])
    tool = build_dropbox_tool(connector_factory=lambda: fake)

    assert tool.name == "dropbox"
    assert tool.toolset == "dropbox"
    assert tool.schema["name"] == "dropbox"
    assert callable(tool.handler)
    assert callable(tool.check_fn)
    payload = json.loads(tool.handler({"action": "list_folder"}))
    assert payload["entries"][0]["name"] == "built.txt"
    assert registry.get_entry("dropbox") is before


def test_build_copies_the_schema_so_callers_cannot_mutate_the_shared_one():
    tool = build_dropbox_tool(connector_factory=lambda: _FakeConnector())
    tool.schema["description"] = "mutated"
    assert DROPBOX_TOOL_SCHEMA["description"] != "mutated"
    assert build_dropbox_tool().schema["description"] != "mutated"


def test_registry_kwargs_expose_the_definition_without_replacing_the_registered_tool():
    before = registry.get_entry("dropbox")
    tool = build_dropbox_tool(connector_factory=lambda: _FakeConnector())
    kwargs = tool.as_registry_kwargs()
    assert kwargs["name"] == "dropbox"
    assert kwargs["toolset"] == "dropbox"
    assert kwargs["schema"]["name"] == "dropbox"
    assert kwargs["handler"] is tool.handler
    assert kwargs["check_fn"] is tool.check_fn
    assert registry.get_entry("dropbox") is before


def test_configured_probe_uses_the_documented_token_file(monkeypatch, tmp_path):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    assert dropbox_tool_configured() is False
    (tmp_path / "dropbox_token.json").write_text(
        json.dumps({"access_token": "sl.fake"}), encoding="utf-8")
    assert dropbox_tool_configured() is True
