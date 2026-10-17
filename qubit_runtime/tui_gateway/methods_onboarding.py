import threading

from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method

# These handlers run on the RPC pool. Two overlapping kickoffs must not both find no setup
# profile and create a second one, or both find an empty setup chat and seed it twice.
_setup_profile_lock = threading.Lock()


@method("onboarding.ensure_setup_profile")
def _(rid, params: dict) -> dict:
    from qubit_cli.setup_profile import ensure_setup_profile
    try:
        with _setup_profile_lock:
            setup = ensure_setup_profile()
            if setup.created:
                _mirror_launch_credentials(setup.path, {"share_auth": True})
    except Exception as e:
        return _err(rid, 5073, str(e))
    return _ok(rid, {"name": setup.name, "path": str(setup.path), "created": setup.created, "role": "setup"})


@method("onboarding.ensure_setup_session")
def _(rid, params: dict) -> dict:
    from qubit_cli.setup_profile import SETUP_CHAT_TITLE, ensure_setup_profile
    from qubit_state_registry import acquire, release_or_close
    try:
        with _setup_profile_lock:
            setup = ensure_setup_profile()
            if setup.created:
                _mirror_launch_credentials(setup.path, {"share_auth": True})
            db = acquire(setup.path / "state.db")
            try:
                row = db.get_session_by_title(SETUP_CHAT_TITLE)
                if row is None:
                    row = {"id": db.create_session(new_session_id(), "desktop"), "message_count": 0}
                    db.set_session_title(row["id"], SETUP_CHAT_TITLE)
                if not row["message_count"]:
                    row["message_count"] = db.append_messages_batch(row["id"], _coerce_seed_history(params.get("messages")))
            finally:
                release_or_close(db)
    except Exception as e:
        logger.exception("onboarding.ensure_setup_session failed")
        return _err(rid, 5075, str(e))
    return _ok(rid, {"profile": setup.name, "session_id": row["id"], "empty": not row["message_count"]})


@method("onboarding.state")
def _(rid, params: dict) -> dict:
    from qubit_cli.setup_profile import read_state, resumable_session, settle_returning_user

    def settle_then_read() -> dict:
        settle_returning_user()
        return {**read_state(), "resumable_session": resumable_session()}
    return _onboarding_state_result(rid, settle_then_read)


@method("onboarding.record_failed_start")
def _(rid, params: dict) -> dict:
    from qubit_cli.setup_profile import record_failed_start
    return _onboarding_state_result(rid, record_failed_start)


@method("onboarding.mark_seen")
def _(rid, params: dict) -> dict:
    from qubit_cli.setup_profile import mark_intro_seen
    return _onboarding_state_result(rid, mark_intro_seen)


@method("onboarding.reset_setup_profile")
def _(rid, params: dict) -> dict:
    from qubit_cli.setup_profile import find_setup_profile, reset_setup_profile
    with _setup_profile_lock:
        found = find_setup_profile()
        if found is None:
            return _err(rid, 4072, "no setup profile to reset")
        _clear_setup_sessions(found[1])
        try:
            setup = reset_setup_profile(_launch_home())
        except Exception as e:
            return _err(rid, 5074, str(e))
    return _ok(rid, {"name": setup.name, "path": str(setup.path), "reset": True})


# The /initiate-setup slash builtin (methods_tools._SLASH_BUILTINS): the skill plus the facts block as one turn.
def _cmd_initiate_setup(rid, params, session, name, arg):
    with _session_profile_runtime_scope(session or {}):
        enabled, disabled = _session_toolsets(session)
        tools = _tools_mod("model_tools").get_tool_definitions(
            enabled_toolsets=enabled, disabled_toolsets=disabled, quiet_mode=True, skip_tool_search_assembly=True)
        surface = _resolve_agent_platform(_session_source(session))
        primary = _tools_mod("qubit_cli.setup_profile").primary_profile(_launch_home())
        message = _tools_mod("agent.initiate_setup_prompt").build_initiate_setup_prompt(
            surface, [tool["function"]["name"] for tool in tools], primary, (session or {}).get("session_key"))
    return _ok(rid, {"type": "send", "message": message, "display": "/initiate-setup"})

def _onboarding_state_result(rid, change) -> dict:
    from qubit_cli.setup_profile import find_setup_profile, onboarding_eligible
    try:
        state = change()
        found = find_setup_profile()
    except Exception as e:
        logger.exception("onboarding state update failed")
        return _err(rid, 5076, str(e))
    return _ok(rid, {"eligible": onboarding_eligible(), "profile": found[0] if found else None, **state})


def start_due_boot() -> None:
    """Serve boot, desktop's local backend: work out the onboarding answer off the RPC path.

    Bound to OUR state, not upstream's ``onboarding_run``. Upstream warms
    ``onboarding_due`` (a saved-run model); Qubit's onboarding is a SETUP PROFILE
    (``qubit_cli.setup_profile``), so the question worth pre-answering here is our own:
    is a fresh user due the intro, and is there a setup profile to resume?

    Why this exists as a callable at all: ``qubit_cli/web_server_app.py`` calls
    ``tui_gateway.server.start_due_boot()`` in its boot lifespan. Upstream defines it in
    this module; ours did not, so every desktop ``serve`` boot raised AttributeError.
    The call ran with the server's globals (``bind_module``), which is why it resolves
    through the server rather than by direct import.

    Non-fatal by construction: the desktop's first ``setup.status`` asks the same
    question over RPC and must never wait on a dead thread, so a failure here is logged
    and swallowed -- boot proceeds and the RPC path still answers.
    """
    def compute() -> None:
        from qubit_cli.setup_profile import read_state, resumable_session
        state = read_state()
        logger.debug("onboarding due at boot: intro=%s failed_starts=%s resumable=%s",
                     state.get("intro"), state.get("failed_starts"), resumable_session())

    try:
        from agent.memory_provider import spawn_context_thread
    except Exception:
        logger.debug("onboarding due boot probe skipped: no context-thread helper", exc_info=True)
        return
    try:
        spawn_context_thread(compute, name="onboarding-due").start()
    except Exception:
        logger.debug("onboarding due boot probe failed to start", exc_info=True)

def _clear_setup_sessions(profile_dir) -> None:
    target = Path(profile_dir).resolve()
    with _sessions_lock:
        live = [sid for sid, sess in _sessions.items()
                if Path(sess.get("profile_home") or _qubit_home).resolve() == target]
    for sid in live:
        _close_session_by_id(sid, end_reason="setup_reset")
    from qubit_state_registry import acquire, release_or_close
    db = acquire(target / "state.db")
    try:
        ids = [row[0] for row in db._read_all("SELECT id FROM sessions")]
        db.delete_sessions(ids, sessions_dir=target / "sessions")
    finally:
        release_or_close(db)


def register(server) -> None:
    bind_module(globals(), server, skip=("_",))
