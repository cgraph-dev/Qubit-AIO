"""Shared fixtures and platform mocks for the ported gateway test tree.

Ported from upstream 43faacfb tests/gateway/conftest.py and rebranded through
scripts/qubit_rebrand.py. Two deliberate departures from upstream:

1. voicebridge.qubit_runtime.ensure_path() runs at import so the ported files
   resolve the fork's gateway.* / qubit_state* runtime modules by bare name
   (upstream's repo root was the package root; our runtime lives under
   qubit_runtime/).
2. Upstream's collection-time plugin-adapter anti-pattern guard is NOT ported:
   it reads test source text via ast and fails the session on a textual
   pattern. Tests in this repo are banned outright from reading source-code
   text, and a collection hook that does so is the same violation one layer up.

The _ensure_telegram_mock / _ensure_discord_mock helpers and the owner / api /
hosted_owner fixtures are ported verbatim (modulo the rebrand).
"""

from voicebridge import qubit_runtime as _qubit_runtime
_qubit_runtime.ensure_path()

"""Shared fixtures for gateway tests.

The ``_ensure_telegram_mock`` helper guarantees that a minimal mock of
the ``telegram`` package is registered in :data:`sys.modules` **before**
any test file triggers ``from plugins.platforms.telegram.adapter import ...``.

Without this, pytest sessions that happen to collect
``test_telegram_caption_merge.py`` (bare top-level import, no per-file
mock) first will cache ``ChatType = None`` from the production
ImportError fallback, causing 30+ downstream test failures wherever
``ChatType.GROUP`` / ``ChatType.SUPERGROUP`` is accessed.

Individual test files may still call their own ``_ensure_telegram_mock``
— it short-circuits when the mock is already present.

Plugin-adapter anti-pattern guard
---------------------------------
Tests for platform plugins (``plugins/platforms/<name>/adapter.py``)
must load the adapter via
:func:`tests.gateway._plugin_adapter_loader.load_plugin_adapter`, not by
adding the plugin directory to ``sys.path`` and doing a bare
``from adapter import ...``. The guard at the bottom of this file
scans test module ASTs at collection time and fails collection with a
pointer to the helper if the anti-pattern is detected.

Rationale: every plugin ships its own ``adapter.py``, and two tests each
inserting their plugin dir on ``sys.path[0]`` race for
``sys.modules["adapter"]`` in the same pytest session. Whichever collects
first wins; the other fails with ``ImportError``, and the polluted
``sys.path`` cascades into unrelated tests. See PR #17764 for the
incident.
"""

import ast
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def private_db_probe_cleanup(tmp_path):
    """Release only this test's probe fds after its explicit SQLite-handle finalizers.

    Production intentionally keeps these probes open: closing one while SQLite still
    owns the inode cancels its POSIX locks. Opt-in tests must first register normal
    runner/store cleanup and use only databases underneath their private tmp_path.
    """
    import qubit_state_dbfile as dbfile

    with dbfile._HEADER_PROBE_LOCK:
        existing = set(dbfile._HEADER_PROBE_FDS)
    yield
    with dbfile._HEADER_PROBE_LOCK:
        for key in set(dbfile._HEADER_PROBE_FDS) - existing:
            if Path(key).is_relative_to(tmp_path):
                fd, _dev, _ino = dbfile._HEADER_PROBE_FDS.pop(key)
                os.close(fd)


@pytest.fixture(scope="session", autouse=True)
def _bind_lark_sdk_globals_when_installed():
    """Bind the feishu adapter's lark SDK globals once per test session.

    The adapter defers ``import lark_oapi`` to first use
    (``_load_lark_oapi`` — called from connect()/probe_bot()/standalone
    send), so the request-builder globals (``CreateMessageRequestBody``
    etc.) stay ``None`` at module import time. Feishu tests across many
    files inject a mock ``_client`` and skip connect() entirely, then call
    send paths that reference those globals. Bind them eagerly when the
    SDK is installed; when it isn't, the affected tests already skip via
    their own ``skipUnless`` guards.
    """
    try:
        import lark_oapi
    except ImportError:
        yield
        return
    try:
        from plugins.platforms.feishu.adapter import _load_lark_oapi

        _load_lark_oapi()
    except Exception:
        pass  # adapter not importable in this environment — tests will skip
    yield


def make_async_session_db(sync_mock=None):
    """Wrap a sync mock SessionDB in AsyncSessionDB so gateway code that awaits
    the facade works in tests. Returns (facade, sync_mock); configure return
    values and assert calls on sync_mock."""
    from qubit_state import AsyncSessionDB
    sync_mock = sync_mock if sync_mock is not None else MagicMock()
    return AsyncSessionDB(sync_mock), sync_mock


class _FakeEnumMember(str):
    """A python-telegram-bot-faithful stand-in for a ``StrEnum`` member.

    PTB constants (``ParseMode``, ``ChatType``) are ``StrEnum`` members:
    ``str(x)`` and equality give the *value* (``"supergroup"``) while
    ``repr(x)`` shows the qualified *member name*
    (``<ChatType.SUPERGROUP>``). Test stubs that pick only one of those
    shapes break the other consumer: plain strings fail assertions like
    ``"MARKDOWN_V2" in repr(parse_mode)``, while auto-generated MagicMock
    attributes fail the adapter's ``str(chat.type)`` normalization
    (``adapter.py`` ``_build_message_event``). This class satisfies both,
    so every telegram test sees the same semantics regardless of which
    file's mock installed first.
    """

    _qualname: str

    def __new__(cls, enum_name: str, member_name: str, value: str):
        obj = str.__new__(cls, value)
        obj._qualname = f"{enum_name}.{member_name}"
        return obj

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"<{self._qualname}: {str.__repr__(self)}>"


def _fake_str_enum(enum_name: str, **members: str):
    """Build a ``SimpleNamespace``-like enum of :class:`_FakeEnumMember`."""
    from types import SimpleNamespace

    return SimpleNamespace(
        **{name: _FakeEnumMember(enum_name, name, value) for name, value in members.items()}
    )


def _ensure_telegram_mock() -> None:
    """Install a comprehensive telegram mock in sys.modules.

    Idempotent — skips when the real library is already imported.
    Uses ``sys.modules[name] = mod`` (overwrite) instead of
    ``setdefault`` so it wins even if a partial/broken import
    already cached a module with ``ChatType = None``.
    """
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return  # Real library is installed — nothing to mock

    mod = MagicMock()
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    # One shared PTB-faithful enum namespace per constant, attached to BOTH
    # access paths: ``sys.modules["telegram.constants"]`` is registered as
    # the root mock below, so ``from telegram.constants import ParseMode``
    # resolves ``mod.ParseMode`` — while config/docs-style access reads
    # ``telegram.constants.ParseMode``. Binding the same object to both
    # keeps every consumer comparing against identical members.
    _parse_mode = _fake_str_enum(
        "ParseMode", MARKDOWN="Markdown", MARKDOWN_V2="MarkdownV2", HTML="HTML"
    )
    _chat_type = _fake_str_enum(
        "ChatType",
        PRIVATE="private",
        GROUP="group",
        SUPERGROUP="supergroup",
        CHANNEL="channel",
    )
    mod.ParseMode = _parse_mode
    mod.constants.ParseMode = _parse_mode
    mod.ChatType = _chat_type
    mod.constants.ChatType = _chat_type

    # Mirror PTB's exception hierarchy: BadRequest is a semantic API error,
    # but inherits from NetworkError in python-telegram-bot 22.x.
    mod.error.TelegramError = type("TelegramError", (Exception,), {})
    mod.error.NetworkError = type("NetworkError", (mod.error.TelegramError,), {})
    mod.error.TimedOut = type("TimedOut", (mod.error.NetworkError,), {})
    mod.error.BadRequest = type("BadRequest", (mod.error.NetworkError,), {})
    mod.error.Forbidden = type("Forbidden", (mod.error.TelegramError,), {})
    mod.error.InvalidToken = type("InvalidToken", (mod.error.TelegramError,), {})

    class RetryAfter(mod.error.TelegramError):
        def __init__(self, retry_after=1):
            self.retry_after = retry_after

    mod.error.RetryAfter = RetryAfter
    mod.error.Conflict = type("Conflict", (mod.error.TelegramError,), {})

    # Update.ALL_TYPES used in start_polling()
    mod.Update.ALL_TYPES = []

    # PerChatUpdateProcessor subclasses this at import time: a MagicMock base
    # would turn the subclass itself into a mock that fails on its second call.
    class SimpleUpdateProcessor:
        def __init__(self, max_concurrent_updates):
            self.max_concurrent_updates = max_concurrent_updates

    mod.SimpleUpdateProcessor = SimpleUpdateProcessor

    for name in (
        "telegram",
        "telegram.ext",
        "telegram.constants",
        "telegram.request",
    ):
        sys.modules[name] = mod
    sys.modules["telegram.error"] = mod.error


def _ensure_discord_mock() -> None:
    """Install a comprehensive discord mock in sys.modules.

    Idempotent — skips when the real library is already imported.
    Uses ``sys.modules[name] = mod`` (overwrite) instead of
    ``setdefault`` so it wins even if a partial/broken import already
    cached the module.

    This mock is comprehensive — it includes **all** attributes needed by
    every gateway discord test file.  Individual test files should call
    this function (it short-circuits when already present) rather than
    maintaining their own mock setup.
    """
    if "discord" in sys.modules and hasattr(sys.modules["discord"], "__file__"):
        return  # Real library is installed — nothing to mock

    from types import SimpleNamespace

    discord_mod = MagicMock()
    discord_mod.Intents.default.return_value = MagicMock()
    discord_mod.Client = MagicMock
    discord_mod.File = MagicMock
    discord_mod.DMChannel = type("DMChannel", (), {})
    discord_mod.Thread = type("Thread", (), {})
    discord_mod.ForumChannel = type("ForumChannel", (), {})
    discord_mod.Interaction = object
    discord_mod.Message = type("Message", (), {})

    # Embed: accept the kwargs production code / tests use
    # (title, description, color). MagicMock auto-attributes work too,
    # but some tests construct and inspect .title/.description directly.
    class _FakeEmbed:
        def __init__(self, *, title=None, description=None, color=None, **_):
            self.title = title
            self.description = description
            self.color = color
            self.fields = []
            self.footer = None
        def add_field(self, *, name=None, value=None, inline=False, **_):
            self.fields.append({"name": name, "value": value, "inline": inline})
            return self
        def set_footer(self, *, text=None, icon_url=None, **_):
            self.footer = {"text": text, "icon_url": icon_url}
            return self
    discord_mod.Embed = _FakeEmbed

    # ui.View / ui.Select / ui.Button: real classes (not MagicMock) so
    # tests that subclass ModelPickerView / iterate .children / clear
    # items work.
    class _FakeView:
        def __init__(self, timeout=None):
            self.timeout = timeout
            self.children = []
        def add_item(self, item):
            self.children.append(item)
        def clear_items(self):
            self.children.clear()

    class _FakeSelect:
        def __init__(self, *, placeholder=None, options=None, custom_id=None, **_):
            self.placeholder = placeholder
            self.options = options or []
            self.custom_id = custom_id
            self.callback = None
            self.disabled = False

    class _FakeButton:
        def __init__(self, *, label=None, style=None, custom_id=None, emoji=None,
                     url=None, disabled=False, row=None, sku_id=None, **_):
            self.label = label
            self.style = style
            self.custom_id = custom_id
            self.emoji = emoji
            self.url = url
            self.disabled = disabled
            self.row = row
            self.sku_id = sku_id
            self.callback = None

    class _FakeSelectOption:
        def __init__(self, *, label=None, value=None, description=None, **_):
            self.label = label
            self.value = value
            self.description = description
    discord_mod.SelectOption = _FakeSelectOption

    # AudioSource: real class so VoiceMixer(discord.AudioSource) can subclass
    # it cleanly in tests.  MagicMock auto-attributes would make is_opus()
    # return a Mock instead of False, breaking 9 TestVoiceMixerCore tests.
    class _FakeAudioSource:
        def is_opus(self):
            return False
        def read(self):
            return b"\x00" * 3840  # one silent stereo s16 frame
        def cleanup(self):
            pass
    discord_mod.AudioSource = _FakeAudioSource

    discord_mod.ui = SimpleNamespace(
        View=_FakeView,
        Select=_FakeSelect,
        Button=_FakeButton,
        button=lambda *a, **k: (lambda fn: fn),
    )
    discord_mod.ButtonStyle = SimpleNamespace(
        success=1, primary=2, secondary=2, danger=3,
        green=1, grey=2, blurple=2, red=3,
    )
    discord_mod.Color = SimpleNamespace(
        orange=lambda: 1, green=lambda: 2, blue=lambda: 3,
        red=lambda: 4, purple=lambda: 5, greyple=lambda: 6,
        gold=lambda: 7,
    )

    # app_commands — needed by _register_slash_commands auto-registration
    class _FakeGroup:
        def __init__(self, *, name, description, parent=None):
            self.name = name
            self.description = description
            self.parent = parent
            self._children: dict = {}
            if parent is not None:
                parent.add_command(self)

        def add_command(self, cmd):
            self._children[cmd.name] = cmd

    class _FakeCommand:
        def __init__(self, *, name, description, callback, parent=None):
            self.name = name
            self.description = description
            self.callback = callback
            self.parent = parent

    discord_mod.app_commands = SimpleNamespace(
        describe=lambda **kwargs: (lambda fn: fn),
        choices=lambda **kwargs: (lambda fn: fn),
        Choice=lambda **kwargs: SimpleNamespace(**kwargs),
        Group=_FakeGroup,
        Command=_FakeCommand,
    )

    ext_mod = MagicMock()
    commands_mod = MagicMock()
    commands_mod.Bot = MagicMock
    ext_mod.commands = commands_mod

    for name in ("discord", "discord.ext", "discord.ext.commands"):
        sys.modules[name] = discord_mod
    sys.modules["discord.ext"] = ext_mod
    sys.modules["discord.ext.commands"] = commands_mod


# Run at collection time — before any test file's module-level imports.
_ensure_telegram_mock()
_ensure_discord_mock()


# ---------------------------------------------------------------------------
# Plugin-adapter anti-pattern guard
# ---------------------------------------------------------------------------
@pytest.fixture
def owner(tmp_path):
    """A canonical ``SessionAuthority`` over a fresh profile ``state.db``."""
    from types import SimpleNamespace

    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import SessionAuthority
    from qubit_state import SessionDB
    from qubit_state_runtime import begin_runtime_epoch

    db = SessionDB(tmp_path / 'state.db')
    store = SessionStore(config=GatewayConfig(), sessions_dir=tmp_path / 'sessions')
    store._db = db  # production: the routing store and the authority share the profile's state.db
    runner = SimpleNamespace(_draining=False, session_store=store)
    authority = SessionAuthority(runner, profile_id='default', instance_id='first', db=db,
                                 epoch=begin_runtime_epoch(db, instance_id='first'))
    runner.session_authority = authority
    yield authority
    db.close()


@pytest.fixture
def api(owner, tmp_path, monkeypatch):
    """The API server adapter bound to ``owner``'s runner and database."""
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    monkeypatch.setenv('QUBIT_HOME', str(tmp_path))
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    adapter.gateway_runner = owner.runner
    adapter._session_db = owner.db
    owner.runner._adapter_for_source = lambda source: adapter
    yield adapter
    adapter._response_store.close()
    adapter._run_idempotency_store.close()


@pytest.fixture
def hosted_owner(tmp_path, monkeypatch):
    """A hosted-room source authority on its own loop thread: ``(authority, loop, principal, agent)``."""
    import asyncio
    import threading
    from types import SimpleNamespace

    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_contract import Principal
    from gateway import run, session_policy
    monkeypatch.setattr(run, '_load_gateway_config', lambda: {'model': {'default': 'fixture'}, 'platform_toolsets': {'cli': []}})
    monkeypatch.setattr(run, '_resolve_gateway_model', lambda cfg: 'fixture')
    # Parent-owned private restore hook, explicitly not an ordinary-daemon proof.
    original = session_policy.restore_policy
    def restore(data):
        from dataclasses import replace
        if data['source'] == 'bot_room':
            return replace(original({**data, 'source': 'gui', 'platform': 'desktop'}), source='bot_room', platform='bot_room')
        return original(data)
    monkeypatch.setattr(session_policy, 'restore_policy', restore)
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    agent = SimpleNamespace(interrupted=False)
    agent.interrupt = lambda: setattr(agent, 'interrupted', True)
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
                             _cached_agent_for=lambda route: agent)
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    authority = asyncio.run_coroutine_threadsafe(initialize_session_authority(runner, profile_id='owned', instance_id='first'), loop).result()
    monkeypatch.setattr(authority, '_schedule', lambda ref: None)
    principal = Principal('durable-room-owner', 'owned', frozenset({'session:create', 'session:read', 'session:submit', 'session:control', 'session:approve'}), 'room-worker')
    yield authority, loop, principal, agent
    loop.call_soon_threadsafe(loop.stop)
    thread.join()
    loop.close()
    store._db.close()
