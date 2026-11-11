"""Redaction keeps credentials out without hiding control-plane limits."""
from voicebridge.business.util import redact


def test_token_limits_are_not_redacted():
    out = redact(
        {
            "max_tokens": 8192,
            "max_tokens_per_turn": 4096,
            "prompt_tokens": 12,
            "completion_tokens": 34,
            "total_tokens": 46,
            "estimated_prompt_tokens": 56,
            "projected_tokens": 78,
            "remaining_tokens": 90,
        }
    )
    assert out == {
        "max_tokens": 8192,
        "max_tokens_per_turn": 4096,
        "prompt_tokens": 12,
        "completion_tokens": 34,
        "total_tokens": 46,
        "estimated_prompt_tokens": 56,
        "projected_tokens": 78,
        "remaining_tokens": 90,
    }


def test_real_secrets_are_still_redacted():
    out = redact(
        {
            "access_token": "abc",
            "bot_token": "def",
            "api_key": "ghi",
            "authorization": "Bearer xyz",
            "max_tokens": 8192,
        }
    )
    assert out["access_token"] == "***REDACTED***"
    assert out["bot_token"] == "***REDACTED***"
    assert out["api_key"] == "***REDACTED***"
    assert out["authorization"] == "***REDACTED***"
    assert out["max_tokens"] == 8192
