"""REDACT-FIX — the ``_BEARER`` matcher must not corrupt ordinary prose.

Preserved source behavior: real Basic/Bearer credential material is still
masked. CGraph enhancement: the scheme word is only treated as credential
material when the following token has a credential shape (>=8 chars with at
least one non-letter), so a plain dictionary word run after "basic"/"bearer"
no longer redacts.
"""
from __future__ import annotations

import pytest

from voicebridge.redaction import MASK, redact_text


# ---------------------------------------------------------------- acceptance 1/2
@pytest.mark.parametrize("text", [
    "basic lifecycle",
    "bearer lifecycle",
    "Our basic operations guide",
    "bearer tokens are documented",
])
def test_lowercase_prose_after_scheme_is_preserved(text):
    assert redact_text(text) == text


# ----------------------------------------------------------------- acceptance 3
def test_authorization_bearer_credential_still_redacts():
    out = redact_text("Authorization: Bearer abc.def.ghi")
    assert "abc.def.ghi" not in out
    assert MASK in out


# ----------------------------------------------------------------- acceptance 4
def test_basic_auth_credential_still_redacts():
    secret = "dXNlcjpwYXNzd29yZA=="
    out = redact_text(f"Authorization: Basic {secret}")
    assert secret not in out
    assert MASK in out


# ----------------------------------------------------------------- acceptance 5
def test_mixed_opaque_bearer_token_still_redacts():
    secret = "AbC123._-xyzToken9"
    out = redact_text(f"Bearer {secret}")
    assert secret not in out
    assert MASK in out


# ----------------------------------------------------------------- acceptance 6
def test_kanban_style_description_preserved_verbatim():
    description = (
        "Basic lifecycle for the release board: create the task, attach the "
        "owner, document the bearer tokens convention, then archive it."
    )
    assert redact_text(description) == description
    assert "lifecycle" in redact_text(description)


# ----------------------------------------------------------------- acceptance 7
def test_description_with_embedded_credential_still_masks():
    secret = "dXNlcjpwYXNzd29yZA=="
    description = (
        "Basic lifecycle rollout notes. Rotate credentials using "
        f"Authorization: Basic {secret} before shipping."
    )
    out = redact_text(description)
    assert secret not in out, "embedded Basic credential leaked"
    assert "Basic lifecycle rollout notes" in out
    assert MASK in out


# ----------------------------------------------------------------- acceptance 8
def test_other_secret_shapes_still_redact():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF12345"
    sk = "sk-" + "A" * 24
    userinfo = "https://user:secretpw@example.com/path"
    query = "https://example.com/?token=q123&x=1"

    out_jwt = redact_text(f"token {jwt}")
    assert jwt not in out_jwt and MASK in out_jwt

    out_sk = redact_text(f"api_key={sk}")
    assert sk not in out_sk and MASK in out_sk

    out_userinfo = redact_text(userinfo)
    assert "secretpw" not in out_userinfo and MASK in out_userinfo
    assert "example.com" in out_userinfo

    out_query = redact_text(query)
    assert "q123" not in out_query and MASK in out_query
