"""A failed connector entry is classed by its gateway error code, never a NameError.

The connector branch reads ``result.error`` from the event; a JSON string, a dict, unparseable text
and a non-connector tool all resolve without raising, and every class it can emit is a declared
``TOOL_ERROR_CLASSES`` value.
"""

from __future__ import annotations

import json

import pytest

from qubit_cli.observability import shared_metrics_contract as contract


@pytest.mark.parametrize(("code", "family", "expected"), [
    ("CONNECTION_REQUIRED", "product", "connection_required"),
    ("CONNECTION_EXPIRED", "product", "connection_required"),
    ("TOOL_NOT_ALLOWED", "product", "not_allowed"),
    ("ALIAS_REQUIRED", "product", "alias"),
    ("ALIAS_UNKNOWN", "product", "alias"),
    ("INVALID_ARGUMENTS", "product", "invalid_arguments"),
    ("PROVIDER_TIMEOUT", "backend", "connector_backend"),
    ("UNLISTED_BACKEND_CODE", "backend", "connector_backend"),
    ("UNLISTED_PRODUCT_CODE", "product", "tool_error"),
    ("FILE_NOT_ALLOWED", "product", "not_allowed"),
    ("FILE_TOO_LARGE", "product", "invalid_arguments"),
])
def test_a_failed_connector_entry_is_classed_by_its_gateway_code(code, family, expected):
    result = json.dumps({"error": {"code": code, "family": family, "message": "private text"}})
    event = {"tool_name": "connectors__gmail__SEND_EMAIL", "status": "error",
             "error_type": "tool_error", "result": result}

    assert contract.connector_error_class(event) == (None if expected == "tool_error" else expected)
    assert contract.tool_error_class(event) == expected
    assert expected in contract.TOOL_ERROR_CLASSES


def test_a_connector_error_result_may_be_a_dict_or_unparseable_text():
    event = {"tool_name": "connectors__gmail__SEND_EMAIL", "status": "error",
             "error_type": "tool_error", "result": {"error": {"code": "TOOL_NOT_ALLOWED"}}}
    assert contract.tool_error_class(event) == "not_allowed"

    text = {**event, "result": "not json"}
    assert contract.connector_error_class(text) is None
    assert contract.tool_error_class(text) == "tool_error"


def test_a_non_connector_tool_keeps_the_generic_class():
    event = {"tool_name": "mcp_github_x", "status": "error", "error_type": "tool_error",
             "result": json.dumps({"error": {"code": "TOOL_NOT_ALLOWED", "family": "product"}})}

    assert contract.connector_error_class(event) is None
    assert contract.tool_error_class(event) == "tool_error"
