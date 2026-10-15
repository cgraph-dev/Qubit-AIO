"""Conversation-owned routing policy; never changes a profile's fallback chain."""

from __future__ import annotations


def session_fallback_disabled(db, session_id: str | None) -> bool:
    """Only an explicit persisted boolean disables fallback for this conversation.

    Read through the agent's own store so hosted profiles cannot share the policy.
    Database errors propagate: an unreadable policy must not authorize a switch.
    """
    if db is None or not session_id:
        return False
    return db.get_session_model_config_value(
        session_id, "session_fallback_disabled", False
    ) is True


def agent_fallback_disabled(agent) -> bool:
    return session_fallback_disabled(
        getattr(agent, "_session_db", None), getattr(agent, "session_id", None)
    )
