"""Connector batch, chain and batch-shape rows in the shared metrics."""

from types import SimpleNamespace

from qubit_cli.observability import shared_metrics_connectors as connectors
from qubit_cli.observability.shared_metrics_connectors import (
    ConnectorSessionState,
    batch_entry_ran,
    is_connector_batch,
    tool_batch_fields,
)

_ROUTE = {"model": "m", "provider": "openrouter"}


def _call(name, arguments):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=arguments))


def test_connector_batch_is_recognized_only_when_every_entry_is_a_connector():
    entry = {"name": "connectors__gmail__SEND_EMAIL", "arguments": {}}
    wrapper = {"tool_name": "tool_call", "args": {"calls": [entry]}}
    assert is_connector_batch(wrapper) is True
    mixed = {"tool_name": "tool_call", "args": {"calls": [entry, {"name": "read_file", "arguments": {}}]}}
    assert is_connector_batch(mixed) is False
    assert is_connector_batch({"tool_name": "read_file", "args": {}}) is False


def test_batch_entry_identity_matches_only_its_wrapper():
    wrapper = ("t1", "tool_call", "tc1")
    assert batch_entry_ran({("t1", "tool_call", "tc1#0")}, wrapper) is True
    assert batch_entry_ran({("t1", "tool_call", "tc2#0")}, wrapper) is False
    assert batch_entry_ran({wrapper}, wrapper) is False


def test_chain_continues_across_a_compression_handoff():
    state = ConnectorSessionState()
    state.observe({"tool_name": "tool_search"})
    state.observe({"tool_name": "tool_describe", "args": {"names": ["connectors__gmail__SEND_EMAIL"]}})
    assert state.observe({"tool_name": "connectors__gmail__SEND_EMAIL", "status": "ok"}) == {
        "described_first": "yes", "outcome": "success", "searches_before_bucket": "1",
    }
    # The handoff copies the object, so the rotated-to segment keeps the reset counter.
    carried = state
    assert carried.observe({"tool_name": "connectors__slack__POST", "status": "ok"})["searches_before_bucket"] == "0"


def test_batch_row_counts_entries_as_logical_calls():
    batch = _call("tool_call", '{"calls": [{"name": "connectors__gmail__SEND_EMAIL", "arguments": {}}, '
                                '{"name": "connectors__slack__POST", "arguments": {}}]}')
    assert tool_batch_fields(_ROUTE, [batch]) == {**_ROUTE, "connector_share": "all", "size_bucket": "2"}
    mixed = tool_batch_fields(_ROUTE, [batch, _call("read_file", "{}")])
    assert mixed == {**_ROUTE, "connector_share": "some", "size_bucket": "3_to_5"}
    assert tool_batch_fields(_ROUTE, []) is None
