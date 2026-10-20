"""``same_create_retry``: an exact ``session.create`` retry vs a genuinely different request.

The helper is the shared identity check for a retry that finds the session already committed
(``gateway/session_local.py``) and for an owner-resolved key overlapping the attempt that holds the
key slot (``gateway/session_policy.py::bind_launch_key``). Requests are compared as decoded JSON, so
key order never matters, and the client's ``client_env`` is judged by the rules frozen with the
committed policy rather than by the live profile.
"""

from __future__ import annotations

import json

from qubit_state_local import client_request, same_create_request, same_create_retry


def _policy(request, *, client_env=None, rules=None):
    return {
        'request_json': json.dumps(request, sort_keys=True),
        'client_env_json': None if client_env is None else json.dumps(client_env),
        'client_env_rules_json': None if rules is None else json.dumps(rules),
    }


def test_exact_retry_is_order_independent():
    saved = _policy({'source': 'cli', 'cwd': '/tmp', 'model': 'm', 'toolsets': ['web']})
    retry = json.dumps({'toolsets': ['web'], 'model': 'm', 'cwd': '/tmp', 'source': 'cli'})
    assert same_create_retry(saved, retry, {}) is True


def test_different_model_is_not_a_retry():
    saved = _policy({'source': 'cli', 'cwd': '/tmp', 'model': 'm-a'})
    assert same_create_retry(saved, json.dumps({'source': 'cli', 'cwd': '/tmp', 'model': 'm-b'}), {}) is False


def test_different_request_payload_is_not_a_retry():
    saved = _policy({'source': 'cli', 'cwd': '/tmp', 'model': 'm', 'toolsets': []})
    retry = json.dumps({'source': 'cli', 'cwd': '/tmp', 'model': 'm', 'toolsets': [], 'yolo': True})
    assert same_create_retry(saved, retry, {}) is False


def test_alias_retry_compares_the_typed_name_not_the_derived_route():
    saved = _policy({
        'source': 'cli', 'cwd': '/tmp', 'model': 'owner-model', 'model_requested': 'fast',
        'provider': 'custom', 'base_url': 'http://127.0.0.1:1/v1', 'route_derived': ['provider', 'base_url']})
    # A ``.env``/alias endpoint change between overlapping attempts keeps the typed name.
    rotated = json.dumps({
        'source': 'cli', 'cwd': '/tmp', 'model': 'owner-model', 'model_requested': 'fast',
        'provider': 'custom', 'base_url': 'http://127.0.0.1:2/v1', 'route_derived': ['provider', 'base_url']})
    assert same_create_retry(saved, rotated, {}) is True
    other_alias = json.dumps({
        'source': 'cli', 'cwd': '/tmp', 'model': 'owner-model', 'model_requested': 'slow',
        'provider': 'custom', 'base_url': 'http://127.0.0.1:1/v1', 'route_derived': ['provider', 'base_url']})
    assert same_create_retry(saved, other_alias, {}) is False


def test_receipt_from_before_an_alias_compares_exactly():
    saved = _policy({'source': 'cli', 'cwd': '/tmp', 'model': 'm'})
    retry = json.dumps({'source': 'cli', 'cwd': '/tmp', 'model': 'm', 'model_requested': 'm',
                        'route_derived': ['provider']})
    assert same_create_request(saved['request_json'], retry) is True


def test_client_request_strips_owner_provenance():
    request = json.dumps({'model': 'owner-model', 'model_requested': 'fast', 'provider': 'custom',
                          'base_url': 'http://127.0.0.1:1/v1', 'route_derived': ['provider', 'base_url']})
    assert client_request(request, ['provider', 'base_url']) == {
        'model_requested': 'fast', 'route_derived': ['provider', 'base_url']}
    assert client_request(request) == {'model_requested': 'fast', 'provider': 'custom'}


def test_client_env_is_judged_by_the_frozen_rules():
    rules = {'behaviour': [], 'names': ['PATH'], 'prefixes': [], 'secret': []}
    saved = _policy({'source': 'cli', 'cwd': '/tmp', 'model': 'm'},
                    client_env={'PATH': '/bin'}, rules=rules)
    same = json.dumps({'source': 'cli', 'cwd': '/tmp', 'model': 'm'})
    assert same_create_retry(saved, same, {'client_env': {'PATH': '/bin'}}) is True
    assert same_create_retry(saved, same, {'client_env': {'PATH': '/usr/bin'}}) is False


def test_client_env_the_rules_drop_never_counts():
    rules = {'behaviour': [], 'names': ['PATH'], 'prefixes': [], 'secret': []}
    saved = _policy({'source': 'cli', 'cwd': '/tmp', 'model': 'm'}, client_env={}, rules=rules)
    retry = json.dumps({'source': 'cli', 'cwd': '/tmp', 'model': 'm'})
    # ``NOT_KEPT`` is filtered out by the frozen rules, so the normalised map still matches.
    assert same_create_retry(saved, retry, {'client_env': {'NOT_KEPT': 'x'}}) is True
    # A session committed without a map matches only a retry that sends none.
    no_map = _policy({'source': 'cli', 'cwd': '/tmp', 'model': 'm'})
    assert same_create_retry(no_map, retry, {'client_env': {'PATH': '/bin'}}) is False
