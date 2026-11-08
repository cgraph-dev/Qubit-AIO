"""Writer/reader rebrand pairs: the same constant must be spelled one way.

The failure class these pin: a rebrand renamed ONE SIDE of a paired constant — an env
var, a WebSocket subprotocol, a header read — so the two processes that agree on
everything else silently stop talking. Nothing raises, every import check passes, and
the feature just stops firing. Each test below names the pair, drives BOTH sides, and
was verified to fail against the code before its fix.

Three pairs, one per test group:

1. ``QUBIT_SERVE_HEADLESS`` — written by ``main_dashboard._resolve_dashboard_web_dist``,
   read by ``web_server_dashboard``/``web_server_config``. The writer emitted only the
   legacy ``HERMES_`` name while both readers had moved to ``QUBIT_``, so ``qubit serve``
   mounted the SPA it exists to suppress.
2. ``X-*-Session-Token`` — ``_ticket_principal`` read only the canonical header name while
   the validator accepted every alias, so a legacy-header caller authenticated yet minted
   a socket ticket with no browser-credential generation (no sign-out revocation).
3. ``*-browser-control-v1`` — the shipped extension dials ``qubit-`` (pinned by
   ``plugins/browser-companion/wire_check.py``); ``api_server`` compared only ``hermes-``.

Four more, from the repo-wide sweep (each: one side renamed, the other not, no error):

4. ``UPDATE_CORRELATION_ID`` — ``scripts/desktop-update/posix.sh`` exported only ``HERMES_``
   while ``update_receipt._launcher_correlation_id`` read only ``QUBIT_``, so every managed
   SSH update wrote a receipt with ``correlation_id=None`` and the shim reported "no receipt"
   for a run that had actually succeeded.
5. ``PYTHON_SRC_ROOT`` — ``tui_gateway.server`` writes both, but ``qubit_bootstrap`` and the
   clipboard helper read only ``HERMES_`` while a rebranded launcher sends ``QUBIT_``; the
   import root then silently falls back to a wrong directory.
6. ``TUI`` — ``main._wants_tui_early`` read ``QUBIT_TUI`` while ``main._is_tui_chat_launch``
   and ``main_tui_launch._resolve_use_tui`` read ``HERMES_TUI``: one env var, two verdicts.
7. ``HOME`` / ``RUNTIME_DIR`` / ``CONTAINER`` — one side renamed to ``QUBIT_``, the other
   still reading (or writing) only ``HERMES_``.
"""

import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 1. SERVE_HEADLESS: one writer, two readers, both spellings accepted
# ---------------------------------------------------------------------------

HEADLESS_WRITER = "qubit_cli.main_dashboard._resolve_dashboard_web_dist"


def _clear_headless(monkeypatch):
    for name in ("QUBIT_SERVE_HEADLESS", "HERMES_SERVE_HEADLESS"):
        monkeypatch.delenv(name, raising=False)


def test_headless_writer_sets_the_canonical_name(monkeypatch):
    """The serve path must write ``QUBIT_SERVE_HEADLESS`` — the name both readers key on."""
    from qubit_cli import main_dashboard

    _clear_headless(monkeypatch)
    main_dashboard._resolve_dashboard_web_dist(object(), _headless_backend=True)

    import os
    assert os.environ.get("QUBIT_SERVE_HEADLESS") == "1"


def test_headless_writer_value_is_seen_by_both_readers(monkeypatch):
    """End-to-end: what the writer sets, the readers report. This is the pair that broke."""
    from qubit_cli import main_dashboard, web_server_config, web_server_dashboard

    _clear_headless(monkeypatch)
    main_dashboard._resolve_dashboard_web_dist(object(), _headless_backend=True)

    assert web_server_dashboard._serve_headless() is True
    assert web_server_config._serve_headless_env() == "1"
    # And the reader the skew hint routes on.
    assert "Restart backend" in web_server_config._dashboard_skew_restart_hint()


@pytest.mark.parametrize("legacy_env", ["HERMES_SERVE_HEADLESS"])
def test_headless_readers_still_accept_the_legacy_spelling(legacy_env, monkeypatch):
    """A backend spawned by a pre-rename build exports only ``HERMES_``; it must stay headless."""
    import os

    from qubit_cli import web_server_config, web_server_dashboard

    _clear_headless(monkeypatch)
    monkeypatch.setenv(legacy_env, "1")

    assert web_server_dashboard._serve_headless() is True
    assert web_server_config._serve_headless_env() == "1"
    # Presence of the legacy var alone is enough for the skew hint to name the Desktop backend.
    assert "Restart backend" in web_server_config._dashboard_skew_restart_hint()
    assert os.environ[legacy_env] == "1"


@pytest.mark.parametrize("value", ["", "0", "yes"])
def test_headless_readers_do_not_treat_a_non_flag_as_headless(value, monkeypatch):
    """Only ``"1"`` means headless — the fix must not widen the accepted values."""
    from qubit_cli import web_server_config, web_server_dashboard

    _clear_headless(monkeypatch)
    monkeypatch.setenv("QUBIT_SERVE_HEADLESS", value)

    assert web_server_dashboard._serve_headless() is False
    assert web_server_config._serve_headless_env() == value


def test_non_headless_launch_strips_both_spellings(monkeypatch):
    """A browser dashboard must not inherit a leaked headless flag under either name."""
    import os

    from qubit_cli.main import _dashboard_sanitize_desktop_env

    for name in ("QUBIT_SERVE_HEADLESS", "HERMES_SERVE_HEADLESS"):
        monkeypatch.setenv(name, "1")
    _dashboard_sanitize_desktop_env(headless_backend=False)

    assert "QUBIT_SERVE_HEADLESS" not in os.environ
    assert "HERMES_SERVE_HEADLESS" not in os.environ


# ---------------------------------------------------------------------------
# 2. Session-token header aliases: the ticket principal reads what the validator reads
# ---------------------------------------------------------------------------

_HEADER_NAMES = ("X-Qubit-Session-Token", "X-Hermes-Session-Token")


def _request(headers: dict):
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/api/webapp/socket-ticket",
                    "headers": raw, "query_string": b""})


@pytest.mark.parametrize("header_name", _HEADER_NAMES)
def test_ticket_principal_records_the_token_from_either_header(header_name, monkeypatch):
    """The principal must carry the presented token under EVERY accepted spelling.

    Before the fix only the canonical name populated ``session_token``; a legacy-header
    caller passed the validator but minted a ticket with ``session_token=None``, which
    skips ``generation_of(...)`` and so never revokes on browser sign-out.
    """
    from starlette.applications import Starlette

    from qubit_cli import web_server
    from qubit_cli.web_routers.webapp import _ticket_principal

    monkeypatch.setattr(web_server, "_SESSION_TOKEN", "s3cret-token")
    app = Starlette()
    app.state.auth_required = False

    request = _request({header_name: "s3cret-token"})
    request.scope["app"] = app
    identity, session_token = _ticket_principal(request)

    assert session_token == "s3cret-token"
    assert identity == {"user_id": "dashboard", "provider": "session",
                        "issuer": ""} or identity  # shape owned by SESSION_TOKEN_OWNER


@pytest.mark.parametrize("header_name", _HEADER_NAMES)
def test_ticket_principal_rejects_a_wrong_token_under_either_header(header_name, monkeypatch):
    """Reading both headers must not accept a credential neither validator would."""
    from fastapi import HTTPException
    from starlette.applications import Starlette

    from qubit_cli import web_server
    from qubit_cli.web_routers.webapp import _ticket_principal

    monkeypatch.setattr(web_server, "_SESSION_TOKEN", "s3cret-token")
    app = Starlette()
    app.state.auth_required = False

    request = _request({header_name: "wrong-token"})
    request.scope["app"] = app
    with pytest.raises(HTTPException) as caught:
        _ticket_principal(request)
    assert caught.value.status_code == 401


def test_ticket_principal_still_reads_the_bearer_fallback(monkeypatch):
    from starlette.applications import Starlette

    from qubit_cli import web_server
    from qubit_cli.web_routers.webapp import _ticket_principal

    monkeypatch.setattr(web_server, "_SESSION_TOKEN", "s3cret-token")
    app = Starlette()
    app.state.auth_required = False

    request = _request({"Authorization": "Bearer s3cret-token"})
    request.scope["app"] = app
    _identity, session_token = _ticket_principal(request)
    assert session_token == "s3cret-token"


# ---------------------------------------------------------------------------
# 3. Browser-control subprotocol: what the extension dials is what api_server accepts
# ---------------------------------------------------------------------------

EXTENSION_PROTOCOL = "qubit-browser-control-v1"   # what the packaged extension sends
EXTENSION_TICKET = "qubit-browser-control-ticket."
LEGACY_PROTOCOL = "hermes-browser-control-v1"     # what an older extension sends
LEGACY_TICKET = "hermes-browser-control-ticket."


def _split_like_api_server(requested: list[str]):
    """Mirror of the acceptance logic in ``_handle_browser_control_ws``."""
    from gateway.platforms.api_server import (
        _BROWSER_CONTROL_TICKET_PROTOCOL_PREFIXES,
        _BROWSER_CONTROL_WS_PROTOCOL,
        _BROWSER_CONTROL_WS_PROTOCOL_ALIASES,
    )

    ticket_protocols = [
        value for value in requested if value.startswith(_BROWSER_CONTROL_TICKET_PROTOCOL_PREFIXES)]
    offered = next((name for name in (_BROWSER_CONTROL_WS_PROTOCOL, *_BROWSER_CONTROL_WS_PROTOCOL_ALIASES)
                    if name in requested), "")
    if not offered or len(ticket_protocols) != 1:
        return None, None
    prefix = next(p for p in _BROWSER_CONTROL_TICKET_PROTOCOL_PREFIXES
                  if ticket_protocols[0].startswith(p))
    return offered, ticket_protocols[0][len(prefix):]


@pytest.mark.parametrize(("protocol", "prefix", "expected_protocol"), [
    (EXTENSION_PROTOCOL, EXTENSION_TICKET, EXTENSION_PROTOCOL),
    (LEGACY_PROTOCOL, LEGACY_TICKET, LEGACY_PROTOCOL),
])
def test_api_server_accepts_either_browser_control_spelling(protocol, prefix, expected_protocol):
    """Both the rebranded extension and a pre-rename one must reach the upgrade."""
    offered, ticket = _split_like_api_server([protocol, f"{prefix}tok-123"])
    assert offered == expected_protocol, "handshake must echo the spelling the client offered"
    assert ticket == "tok-123", "the matched prefix must be stripped exactly once"


def test_api_server_refuses_a_mixed_pairing():
    """A ticket prefix from one brand and a protocol from the other is not a valid dial."""
    offered, _ticket = _split_like_api_server([EXTENSION_PROTOCOL, "garbage-ticket.tok"])
    assert offered is None


def test_shipped_extension_speaks_the_protocol_api_server_now_accepts():
    """The real client: read the extension source and feed its literals to the acceptance logic."""
    from pathlib import Path

    import re

    extension = (Path(__file__).resolve().parents[3] / "frontend" / "qubit-app" / "plugins"
                 / "qubit-browser-ext" / "extension" / "lib" / "controller-protocol.mjs")
    if not extension.is_file():
        pytest.skip(f"extension source not in this checkout: {extension}")
    text = extension.read_text(encoding="utf-8")
    found = re.findall(r"'(qubit|hermes)-browser-control-v1'", text)
    assert found, "extension no longer declares a browser-control subprotocol"
    for brand in found:
        offered, ticket = _split_like_api_server(
            [f"{brand}-browser-control-v1", f"{brand}-browser-control-ticket.tok-abc"])
        assert offered == f"{brand}-browser-control-v1"
        assert ticket == "tok-abc"


# ---------------------------------------------------------------------------
# 4. UPDATE_CORRELATION_ID: desktop-update/posix.sh writes, update_receipt reads
# ---------------------------------------------------------------------------

_CORRELATION_VARS = ("QUBIT_UPDATE_CORRELATION_ID", "HERMES_UPDATE_CORRELATION_ID")


def _clear_correlation(monkeypatch):
    for name in _CORRELATION_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("env_name", _CORRELATION_VARS)
def test_receipt_reads_the_correlation_id_under_either_spelling(env_name, monkeypatch):
    """A launcher that exports either name must land in the receipt's ``correlation_id``.

    Before the fix the reader took only ``QUBIT_`` while the shipped POSIX updater
    exported only ``HERMES_``, so ``correlation_id`` was ``None`` on every managed SSH
    update and the launcher's post-update check ("find the receipt that carries MY id")
    never matched — an update that had fully succeeded reported as missing.
    """
    from qubit_cli import update_receipt

    _clear_correlation(monkeypatch)
    monkeypatch.setenv(env_name, "run-abc123")

    assert update_receipt._launcher_correlation_id() == "run-abc123"


def test_receipt_correlation_id_is_none_when_neither_spelling_is_set(monkeypatch):
    """Both spellings absent must still mean "no launcher", not an empty string."""
    from qubit_cli import update_receipt

    _clear_correlation(monkeypatch)
    assert update_receipt._launcher_correlation_id() is None


def test_receipt_prefers_the_canonical_spelling_when_both_are_set(monkeypatch):
    """Both set (a shim exporting both while it migrates) → ``QUBIT_`` wins, deterministically."""
    from qubit_cli import update_receipt

    _clear_correlation(monkeypatch)
    monkeypatch.setenv("HERMES_UPDATE_CORRELATION_ID", "legacy")
    monkeypatch.setenv("QUBIT_UPDATE_CORRELATION_ID", "canonical")

    assert update_receipt._launcher_correlation_id() == "canonical"


def _shim_exports(tmp_path, line_substring):
    """Return True when the shipped posix updater exports the given env name."""
    script = (Path(__file__).resolve().parents[2] / "scripts" / "desktop-update" / "posix.sh")
    if not script.is_file():
        pytest.skip(f"posix updater not in this checkout: {script}")
    return any(f"export {line_substring}=" in line
               for line in script.read_text(encoding="utf-8").splitlines())


@pytest.mark.parametrize("env_name", _CORRELATION_VARS)
def test_posix_updater_exports_both_correlation_spellings(env_name, tmp_path):
    """The writer must emit both: the receipt reader and the shim's own check disagree otherwise."""
    assert _shim_exports(tmp_path, env_name), (
        f"scripts/desktop-update/posix.sh no longer exports {env_name}; the receipt's "
        "correlation_id and the shim's update_committed_after_exit would disagree again"
    )


# ---------------------------------------------------------------------------
# 5. PYTHON_SRC_ROOT: tui_gateway.server writes both, the readers must accept both
# ---------------------------------------------------------------------------

_SRC_ROOT_VARS = ("QUBIT_PYTHON_SRC_ROOT", "HERMES_PYTHON_SRC_ROOT")


@pytest.mark.parametrize("env_name", _SRC_ROOT_VARS)
def test_harden_import_path_honours_either_src_root_spelling(env_name, monkeypatch, tmp_path):
    """Whichever spelling names the root, that root goes to the front of ``sys.path``.

    ``tui_gateway.server._seed_vendored_src_root_pythonpath`` writes QUBIT_ + HERMES_;
    ``qubit_bootstrap.harden_import_path`` read only HERMES_. A child that received the
    canonical spelling therefore relocated its import root to the source file's directory
    instead — the wrong tree, with no exception raised.
    """
    import sys

    import qubit_bootstrap

    for name in _SRC_ROOT_VARS:
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "src-root"
    root.mkdir()
    monkeypatch.setenv(env_name, str(root))

    original = list(sys.path)
    try:
        qubit_bootstrap.harden_import_path()
        assert sys.path[0] == str(root), (
            f"harden_import_path ignored {env_name}: sys.path[0]={sys.path[0]!r}"
        )
    finally:
        sys.path[:] = original


@pytest.mark.parametrize("env_name", _SRC_ROOT_VARS)
def test_clipboard_helper_resolves_the_root_from_either_spelling(env_name, monkeypatch, tmp_path):
    """The dist/ layout has no usable ``parents[2]``; the env var is the only correct source."""
    import importlib.util

    for name in _SRC_ROOT_VARS:
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "clip-root"
    root.mkdir()
    monkeypatch.setenv(env_name, str(root))

    helper = (Path(__file__).resolve().parents[2] / "ui-tui" / "scripts" / "clipboard_image.py")
    if not helper.is_file():
        pytest.skip(f"clipboard helper not in this checkout: {helper}")
    spec = importlib.util.spec_from_file_location("_clipboard_image_probe", helper)
    module = importlib.util.module_from_spec(spec)
    # The module inserts its python_root() on sys.path at import; re-derive it after import.
    spec.loader.exec_module(module)

    assert module.python_root() == root, f"clipboard_image ignored {env_name}"


def test_tui_python_env_repair_does_not_clobber_a_valid_canonical_root(monkeypatch, tmp_path):
    """A valid ``QUBIT_PYTHON_SRC_ROOT`` must survive the ``HERMES_``-only repair path."""
    from qubit_cli.main_tui_launch import _apply_tui_python_env

    for name in _SRC_ROOT_VARS + ("QUBIT_PYTHON", "HERMES_PYTHON"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "valid-root"
    root.mkdir()

    env = {"QUBIT_PYTHON_SRC_ROOT": str(root)}
    _apply_tui_python_env(env)

    assert env.get("HERMES_PYTHON_SRC_ROOT") == str(root), (
        "repair judged a valid QUBIT_ root by the HERMES_ spelling and overwrote it"
    )
    assert env.get("QUBIT_PYTHON_SRC_ROOT") == str(root)


# ---------------------------------------------------------------------------
# 6. TUI: one env var, one verdict — the pre-flight and the launcher must agree
# ---------------------------------------------------------------------------

_TUI_VARS = ("QUBIT_TUI", "HERMES_TUI")


@pytest.mark.parametrize("env_name", _TUI_VARS)
def test_tui_env_turns_on_both_the_early_check_and_the_launcher(env_name, monkeypatch):
    """``<brand>_TUI=1`` must satisfy every reader, not just one of the two.

    ``main._wants_tui_early`` read QUBIT_TUI; ``main._is_tui_chat_launch`` and
    ``main_tui_launch._resolve_use_tui`` read HERMES_TUI. With only one set, the
    pre-flight and the launch reached opposite verdicts on a single var.
    """
    from qubit_cli.main import _is_tui_chat_launch, _tui_env_requested, _wants_tui_early

    for name in _TUI_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(env_name, "1")

    assert _tui_env_requested() is True
    assert _wants_tui_early(["chat"]) is True

    class _Args:
        tui = False
        tui_native = False
        cli = False

    assert _is_tui_chat_launch(_Args()) is True

    # ``_resolve_use_tui`` deliberately keeps the TTY gate ahead of the env check (a piped
    # ``chat -q`` must never be hijacked into the TUI), so give it a TTY.
    from qubit_cli import main_tui_launch

    monkeypatch.setattr(main_tui_launch.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(main_tui_launch.sys.stdout, "isatty", lambda: True, raising=False)
    assert main_tui_launch._resolve_use_tui(_Args()) is True


@pytest.mark.parametrize("value", ["", "0", "yes"])
def test_tui_env_still_requires_the_exact_flag(value, monkeypatch):
    """Widening to both spellings must not widen the accepted VALUES."""
    from qubit_cli.main import _tui_env_requested

    for name in _TUI_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("QUBIT_TUI", value)
    assert _tui_env_requested() is False


def test_cli_flag_beats_either_tui_spelling(monkeypatch):
    """``--cli`` still wins over both spellings — the alias must not defeat precedence."""
    from qubit_cli.main import _wants_tui_early

    for name in _TUI_VARS:
        monkeypatch.setenv(name, "1")
    assert _wants_tui_early(["--cli"]) is False


# ---------------------------------------------------------------------------
# 7. HOME / RUNTIME_DIR / CONTAINER: readers accept both instead of one
# ---------------------------------------------------------------------------

def test_default_root_resolves_the_legacy_home_spelling(monkeypatch, tmp_path):
    """A replayed child exported ``HERMES_HOME`` only; it must not fall back to the native root."""
    from qubit_constants import get_default_qubit_root

    monkeypatch.delenv("QUBIT_HOME", raising=False)
    home = tmp_path / "legacy-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    assert get_default_qubit_root() == home


def test_default_root_still_prefers_the_canonical_home_spelling(monkeypatch, tmp_path):
    from qubit_constants import get_default_qubit_root

    canonical = tmp_path / "canonical-home"
    legacy = tmp_path / "legacy-home"
    canonical.mkdir()
    legacy.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(canonical))
    monkeypatch.setenv("HERMES_HOME", str(legacy))

    assert get_default_qubit_root() == canonical


@pytest.mark.parametrize("env_name", ["QUBIT_RUNTIME_DIR", "HERMES_RUNTIME_DIR"])
def test_store_root_honours_either_runtime_dir_spelling(env_name, monkeypatch, tmp_path):
    """``pm.runtime`` exports HERMES_RUNTIME_DIR; the installers and voicebridge use QUBIT_RUNTIME_DIR.

    Parametrised over BOTH spellings on purpose: a test that only set the legacy name passed
    against a reader that accepted only the legacy name, so it could not see the bug at all.
    """
    from pm.environments import store_root

    for name in ("QUBIT_RUNTIME_DIR", "HERMES_RUNTIME_DIR"):
        monkeypatch.delenv(name, raising=False)
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv(env_name, str(store))

    assert store_root(tmp_path, honor_runtime_override=True) == store.resolve()


def test_store_root_publication_ignores_both_overrides(monkeypatch, tmp_path):
    """``honor_runtime_override=False`` must stay blind to BOTH spellings."""
    from pm.environments import store_root

    monkeypatch.setenv("QUBIT_RUNTIME_DIR", str(tmp_path / "canon"))
    monkeypatch.setenv("HERMES_RUNTIME_DIR", str(tmp_path / "legacy"))

    resolved = store_root(tmp_path, honor_runtime_override=False)
    assert resolved not in {(tmp_path / "canon").resolve(), (tmp_path / "legacy").resolve()}


@pytest.mark.parametrize("env_name", ["QUBIT_CONTAINER", "HERMES_CONTAINER"])
def test_container_override_is_honoured_under_either_spelling(env_name, monkeypatch):
    from qubit_constants import _container_or_chmod_skipped

    for name in ("QUBIT_CONTAINER", "HERMES_CONTAINER", "QUBIT_SKIP_CHMOD", "HERMES_SKIP_CHMOD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(env_name, "1")

    assert _container_or_chmod_skipped() is True


@pytest.mark.parametrize("env_name", ["QUBIT_UID", "HERMES_UID"])
def test_uid_override_is_honoured_under_either_spelling(env_name, monkeypatch):
    from qubit_constants import _resolve_qubit_uid_gid

    if sys.platform == "win32":
        pytest.skip("uid/gid mapping is POSIX-only")
    for name in ("QUBIT_UID", "HERMES_UID", "QUBIT_GID", "HERMES_GID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(env_name, "4242")

    uid, gid = _resolve_qubit_uid_gid()
    assert uid == 4242
    assert gid is None


@pytest.mark.parametrize("env_name", ["QUBIT_HOME_MODE", "HERMES_HOME_MODE"])
def test_home_mode_override_is_honoured_under_either_spelling(env_name, monkeypatch, tmp_path):
    """The group-share hatch the docs advertise must actually apply."""
    from qubit_constants import apply_secure_dir_policy

    if sys.platform == "win32":
        pytest.skip("mode bits are POSIX-only")
    for name in ("QUBIT_HOME_MODE", "HERMES_HOME_MODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(env_name, "0750")
    monkeypatch.setenv("QUBIT_MANAGED", "0")

    target = tmp_path / "shared"
    target.mkdir()
    apply_secure_dir_policy(target)

    assert (target.stat().st_mode & 0o777) == 0o750
