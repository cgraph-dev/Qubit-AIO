"""Absent optional editor state cannot revoke an older credential reference."""
from dataclasses import asdict
import hashlib
import json


def test_policy_identity_preserves_pre_editor_credential_binding(tmp_path):
    from gateway.session_policy import build_policy
    from gateway.session_policy_credentials import policy_identity
    policy = build_policy({'source': 'cli', 'cwd': str(tmp_path), 'model': 'local'}, {})
    legacy = asdict(policy)
    legacy.pop('credential_ref')
    legacy.pop('config_secret_ref')
    # Absent/default post-release fields are not part of an older reference's identity.
    if legacy.get('editor_mcp_json') is None:
        legacy.pop('editor_mcp_json', None)
    if not legacy.get('coding_posture'):
        legacy.pop('coding_posture', None)
    for name in ('client_env_json', 'client_env_rules_json'):
        if legacy.get(name) is None:
            legacy.pop(name, None)
    if not legacy.get('client_env_remote'):
        legacy.pop('client_env_remote', None)
    if legacy.get('edit_approval_policy', 'ask') == 'ask':
        legacy.pop('edit_approval_policy', None)
    expected = hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
    assert policy_identity(policy) == expected
