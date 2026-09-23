"""REBUILD_ON_RESUME: a resumed session whose stored system prompt predates the
Qubit rebrand must rebuild from current context instead of replaying the legacy
Hermes assistant identity to the model.

The stored ``system_prompts.prompt`` snapshot is replayed byte-for-byte on resume
as long as Model/Provider/cwd/Platform still match. A migrated session therefore
kept sending ``You are Hermes Agent, built by Nous Research`` as the model system
message. ``_stored_prompt_matches_runtime`` now also requires the current Qubit
runtime-identity heading, so the existing ``stale_runtime`` branch rebuilds.

Historical bytes are never rewritten or deleted by the rebuild: it persists a new
``system_prompts`` row and repoints the session's ``system_prompt_hash``.
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

from agent.conversation_loop import _restore_or_build_system_prompt
from agent.prompt_builder import RUNTIME_ENVIRONMENT_END, RUNTIME_ENVIRONMENT_HEADING
from agent.runtime_cwd import resolve_agent_cwd

# The pre-rebrand assistant identity and runtime anchor the 80 migrated rows carry.
_LEGACY_IDENTITY = "You are Hermes Agent, built by Nous Research. Be direct"
_LEGACY_HEADING = "# Hermes runtime environment"
_LEGACY_END = "<!-- End Hermes runtime environment -->"

_MODEL = "test-model"
_PROVIDER = "test-provider"
_PLATFORM = "cli"
_SESSION_ID = "resume-session"


def _legacy_prompt(cwd: str) -> str:
    """A stored prompt from the Hermes era whose runtime footer already matches."""
    return (
        f"{_LEGACY_IDENTITY}\n\n"
        f"{_LEGACY_HEADING}\n\n"
        "Host: Legacy host\n"
        "User home directory: /legacy/home\n"
        f"Current working directory: {cwd}\n"
        f"Platform: {_PLATFORM}\n"
        f"Model: {_MODEL}\n"
        f"Provider: {_PROVIDER}\n"
        f"{_LEGACY_END}"
    )


def _qubit_prompt(cwd: str) -> str:
    """What the current renderer builds: Qubit identity + Qubit runtime anchor."""
    return (
        "You are Qubit Agent, built by Qubit Labs. Be direct\n\n"
        f"{RUNTIME_ENVIRONMENT_HEADING}\n\n"
        "Host: Current host\n"
        "User home directory: /current/home\n"
        f"Current working directory: {cwd}\n"
        f"Platform: {_PLATFORM}\n"
        f"Model: {_MODEL}\n"
        f"Provider: {_PROVIDER}\n"
        f"{RUNTIME_ENVIRONMENT_END}"
    )


def _make_agent(session_db, rebuilt_prompt: str, *, session_id: str = _SESSION_ID):
    agent = MagicMock()
    agent._cached_system_prompt = None
    agent._cached_system_prompt_static = None
    agent._use_prompt_caching = False
    agent.session_id = session_id
    agent.model = _MODEL
    agent.provider = _PROVIDER
    agent.platform = _PLATFORM
    agent._session_db = session_db
    agent._build_system_prompt = MagicMock(return_value=rebuilt_prompt)
    return agent


def _resume(db, rebuilt_prompt: str, *, session_id: str = _SESSION_ID):
    agent = _make_agent(db, rebuilt_prompt, session_id=session_id)
    _restore_or_build_system_prompt(
        agent, None, [{"role": "user", "content": "hi"}]
    )
    return agent


# ---------------------------------------------------------------------------
# 1. A matching-footprint legacy prompt is still rebuilt
# ---------------------------------------------------------------------------


def test_resumed_legacy_session_rebuilds_qubit_prompt():
    cwd = str(resolve_agent_cwd())
    legacy = _legacy_prompt(cwd)
    rebuilt = _qubit_prompt(cwd)
    db = MagicMock()
    db.get_session.return_value = {"system_prompt": legacy}

    agent = _resume(db, rebuilt)

    # The stored legacy bytes are NOT what the model would receive.
    assert agent._cached_system_prompt != legacy
    assert agent._cached_system_prompt == rebuilt
    agent._build_system_prompt.assert_called_once_with(None)


# ---------------------------------------------------------------------------
# 2. The upstream identity never reaches the model on resume
# ---------------------------------------------------------------------------


def test_legacy_identity_bytes_are_not_sent_to_the_model():
    cwd = str(resolve_agent_cwd())
    db = MagicMock()
    db.get_session.return_value = {"system_prompt": _legacy_prompt(cwd)}

    agent = _resume(db, _qubit_prompt(cwd))
    sent = agent._cached_system_prompt

    assert _LEGACY_IDENTITY not in sent
    assert _LEGACY_HEADING not in sent
    assert RUNTIME_ENVIRONMENT_HEADING in sent


# ---------------------------------------------------------------------------
# 3. Historical rows are not rewritten or deleted by the rebuild
# ---------------------------------------------------------------------------


def test_legacy_system_prompts_row_survives_untouched(tmp_path):
    from qubit_state import SessionDB

    cwd = str(resolve_agent_cwd())
    legacy = _legacy_prompt(cwd)
    legacy_hash = hashlib.sha256(legacy.encode("utf-8")).hexdigest()

    with SessionDB(db_path=tmp_path / "state.db") as db:
        db.ensure_session(_SESSION_ID, source="cli", model=_MODEL)
        # The migrated fleet shares prompt rows across sessions; a second holder
        # keeps the legacy row referenced after the first session repoints.
        db.ensure_session("other-session", source="cli", model=_MODEL)
        db.update_system_prompt(_SESSION_ID, legacy)
        db.update_system_prompt("other-session", legacy)

        agent = _resume(db, _qubit_prompt(cwd))

        # The resumed session now points at the freshly built Qubit prompt...
        assert agent._cached_system_prompt == _qubit_prompt(cwd)
        assert db.get_session(_SESSION_ID)["system_prompt"] == _qubit_prompt(cwd)
        # ...and the original legacy row is still present with its original bytes.
        row = db._read_one(
            "SELECT prompt FROM system_prompts WHERE hash = ?", (legacy_hash,)
        )
        assert row is not None
        assert row["prompt"] == legacy


# ---------------------------------------------------------------------------
# 4. A current Qubit prompt is still reused verbatim (identity check is additive)
# ---------------------------------------------------------------------------


def test_current_qubit_prompt_is_reused_verbatim():
    cwd = str(resolve_agent_cwd())
    stored = _qubit_prompt(cwd)
    db = MagicMock()
    db.get_session.return_value = {"system_prompt": stored}

    agent = _resume(db, "SHOULD NOT BE REBUILT")

    assert agent._cached_system_prompt == stored
    assert agent._cached_system_prompt.encode("utf-8") == stored.encode("utf-8")
    agent._build_system_prompt.assert_not_called()
    db.update_system_prompt.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Continuity: same session, and the rebuild persists for the next turn
# ---------------------------------------------------------------------------


def test_rebuild_persists_so_next_turn_restores(tmp_path):
    from qubit_state import SessionDB

    cwd = str(resolve_agent_cwd())
    rebuilt = _qubit_prompt(cwd)

    with SessionDB(db_path=tmp_path / "state.db") as db:
        db.ensure_session(_SESSION_ID, source="cli", model=_MODEL)
        db.update_system_prompt(_SESSION_ID, _legacy_prompt(cwd))

        first = _resume(db, rebuilt)
        assert first.session_id == _SESSION_ID
        assert first._cached_system_prompt == rebuilt
        assert db.get_session(_SESSION_ID)["system_prompt"] == rebuilt

        # A fresh agent on the same session (gateway builds one per turn) restores
        # the rebuilt bytes instead of rebuilding again.
        second = _resume(db, "SHOULD NOT BE REBUILT")
        assert second._cached_system_prompt == rebuilt
        second._build_system_prompt.assert_not_called()
