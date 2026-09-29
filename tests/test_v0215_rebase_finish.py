# tests/test_v0215_rebase_finish.py
# T1 — de-brand upstream Hermes docs URLs on shipped product surfaces.
"""Guard for the v0.21.5 rebase finish (T1).

The rebase left the upstream Hermes documentation host
(``hermes-agent.nousresearch.com/docs``) hard-coded across shipped product
surfaces: printed CLI help, the messaging/OAuth docs catalogs, the model and
plugin catalog fetchers, the skills index, the agent system prompt, plugin
adapters and the vendored dashboard. T1 routes every printed/fetched docs URL
through the single-source ``qubit_cli.install_source`` helpers, which default to
the Qubit-owned repository and are environment-overridable.

These tests pin:

* zero occurrences of the upstream docs host anywhere in the shipped runtime
  tree (the vendored ``qubit_runtime/``), excluding non-shipped dev/docs trees;
* the docs/raw helpers used by the conversion are Qubit-owned and configurable.
"""
from __future__ import annotations

from rebase_bucket_support import RUNTIME, runtime_on_path  # noqa: F401

#: The exact upstream docs host the rebase must not print or fetch.
_UPSTREAM_DOCS_HOST = b"hermes-agent.nousresearch.com/docs"

#: Directory components excluded from the shipped-surface scan. ``tests``,
#: ``website`` and ``evals`` are dev/doc trees; ``archive`` and ``.git`` are
#: frozen/historical; ``node_modules``/``__pycache__`` are build caches.
_SCAN_EXCLUDED_PARTS = frozenset(
    {"tests", "website", "evals", "archive", "node_modules", ".git", "__pycache__"}
)


def _iter_shipped_files(root):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SCAN_EXCLUDED_PARTS for part in path.parts):
            continue
        yield path


def test_no_upstream_docs_host_on_shipped_surfaces():
    """No printed/fetched upstream Hermes docs URL may remain on a shipped file."""
    offenders = []
    for path in _iter_shipped_files(RUNTIME):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if _UPSTREAM_DOCS_HOST in data:
            offenders.append(str(path.relative_to(RUNTIME)))
    assert not offenders, (
        "upstream Hermes docs host still printed/fetched on shipped product "
        f"surfaces; route it through qubit_cli.install_source: {sorted(offenders)}"
    )


def test_install_source_docs_helpers_are_qubit_owned_and_configurable(
    runtime_on_path, monkeypatch
):
    """``docs_url``/``docs_base_url``/``raw_asset_url`` are Qubit-owned + overridable."""
    from qubit_cli import install_source as s

    # Assert the untouched defaults (clear any operator override first).
    monkeypatch.delenv("QUBIT_DOCS_BASE_URL", raising=False)
    monkeypatch.delenv("QUBIT_RAW_ASSET_BASE_URL", raising=False)

    docs_base = s.docs_base_url()
    assert "qubit-aio" in docs_base.lower()
    assert "hermes" not in docs_base.lower()
    assert "nousresearch" not in docs_base.lower()

    assert s.docs_url("user-guide/cli") == f"{docs_base}/user-guide/cli"
    # A leading slash is tolerated (callers pass both shapes).
    assert s.docs_url("/user-guide/cli") == f"{docs_base}/user-guide/cli"
    assert s.docs_url() == docs_base

    raw = s.raw_asset_url("website/static/api/model-catalog.json")
    assert "qubit-aio" in raw.lower()
    assert "hermes" not in raw.lower()
    assert "nousresearch" not in raw.lower()

    # A single override redirects every derived URL to the canonical host.
    monkeypatch.setenv("QUBIT_DOCS_BASE_URL", "https://docs.qubit.example")
    assert s.docs_base_url() == "https://docs.qubit.example"
    assert s.docs_url("x/y") == "https://docs.qubit.example/x/y"

    monkeypatch.setenv("QUBIT_RAW_ASSET_BASE_URL", "https://raw.qubit.example")
    assert s.raw_asset_url("a/b.json") == "https://raw.qubit.example/a/b.json"


# ---------------------------------------------------------------------------
# T2 — the internal release-asset host and GitHub repository identity.
#
# ``source_releases.py`` and ``update_cmd_git.py`` consumed hardcoded
# ``hermes-assets.nousresearch.com`` / ``NousResearch/hermes-agent`` values, and
# ``scripts/releases/r2.py`` defaulted its public base to the same upstream
# host. T2 derives all of them from the single configurable installer source
# (``qubit_cli.install_source``) while preserving the update-channel protocol
# (``ChannelReader`` / ``_release_pointer`` / GitHub-API fallback).
# ---------------------------------------------------------------------------


def _assert_t2_qubit_owned(value: str) -> None:
    lowered = value.lower()
    assert "qubit-aio" in lowered, value
    for legacy in ("hermes", "nousresearch", "nousresearch.com", "openclaw"):
        assert legacy not in lowered, (value, legacy)


def test_t2_install_source_identity_is_qubit_owned(runtime_on_path):
    """One configurable, Qubit-owned repo identity — never a Hermes org/repo."""
    from qubit_cli import install_source as s

    _assert_t2_qubit_owned(s.official_repository())
    assert s.official_repository() == s.DEFAULT_REPOSITORY
    urls = s.official_repo_urls()
    assert s.official_repo_url() in urls
    assert all(s.official_repository() in url for url in urls)
    assert all("github.com" in url for url in urls)


def test_t2_release_assets_base_is_env_configurable(runtime_on_path, monkeypatch):
    """The internal release-asset host is overridable in one place."""
    from qubit_cli import install_source as s

    monkeypatch.setenv("QUBIT_RELEASE_ASSETS_BASE_URL", "https://assets.qubit.example")
    assert s.release_assets_base_url() == "https://assets.qubit.example"
    monkeypatch.delenv("QUBIT_RELEASE_ASSETS_BASE_URL", raising=False)
    assert s.release_assets_base_url() == s.DEFAULT_RELEASE_ASSETS_BASE_URL


def test_t2_source_releases_derive_identity_from_install_source(runtime_on_path, monkeypatch):
    """``source_releases`` consumes the central helpers instead of literals."""
    from qubit_cli import install_source as s
    from qubit_cli import source_releases

    monkeypatch.delenv("QUBIT_REPOSITORY", raising=False)
    monkeypatch.delenv("QUBIT_RELEASE_ASSETS_BASE_URL", raising=False)
    assert source_releases.OFFICIAL_REPOSITORY == s.official_repository() == s.DEFAULT_REPOSITORY
    assert (
        source_releases._PUBLIC_BASE
        == s.release_assets_base_url()
        == s.DEFAULT_RELEASE_ASSETS_BASE_URL
    )
    _assert_t2_qubit_owned(source_releases.OFFICIAL_REPOSITORY)
    _assert_t2_qubit_owned(source_releases._PUBLIC_BASE)


def test_t2_update_cmd_git_derive_origin_identity(runtime_on_path, monkeypatch):
    """The updater's origin identity is derived, not hardcoded."""
    from qubit_cli import install_source as s
    from qubit_cli import update_cmd_git

    monkeypatch.delenv("QUBIT_REPOSITORY", raising=False)
    assert update_cmd_git.OFFICIAL_REPO_URLS == s.official_repo_urls()
    assert update_cmd_git.OFFICIAL_REPO_URL == s.official_repo_url()
    _assert_t2_qubit_owned(update_cmd_git.OFFICIAL_REPO_URL)


def test_t2_fetch_failure_ssh_hint_names_qubit_repo(runtime_on_path):
    """The SSH-recovery hint points at the Qubit repo, not the upstream org."""
    from qubit_cli import install_source as s
    from qubit_cli import update_cmd_git

    message = update_cmd_git._classify_fetch_failure(
        "git@github.com: Permission denied (publickey).\n"
        "fatal: Could not read from remote repository."
    )
    assert s.official_repo_url() in message
    assert "NousResearch/hermes-agent" not in message


def test_t2_r2_default_public_url_derives_from_install_source(runtime_on_path, monkeypatch):
    """The release transport's fallback public base is Qubit-owned."""
    from qubit_cli import install_source as s
    from scripts.releases import r2

    monkeypatch.delenv("CLOUDFLARE_R2_PUBLIC_URL", raising=False)
    monkeypatch.delenv("QUBIT_RELEASE_ASSETS_BASE_URL", raising=False)
    assert r2.default_public_url() == s.release_assets_base_url()
    assert r2.public_base_url() == s.release_assets_base_url()
    assert "hermes-assets" not in r2.default_public_url().lower()


# ---------------------------------------------------------------------------
# T2 sweep — remaining shipped product code that hardcoded the upstream
# ``NousResearch/hermes-agent`` repo identity / raw-asset URL. T1 owns docs URLs;
# T2 owns release-asset + repo identity. The binary/asset *mirror* host
# (``hermes-assets`` in ``install*.{sh,ps1}`` and ``pm/artifact-mirror.json``) is
# an owner gate: no Qubit asset host exists yet, so it is left functional.
# ---------------------------------------------------------------------------

#: Shipped installer sources whose repository identity must be Qubit-owned.
_INSTALLER_REPO_FILES = (
    RUNTIME / "scripts" / "install.sh",
    RUNTIME / "scripts" / "install.ps1",
    RUNTIME / "scripts" / "install.cmd",
    RUNTIME / "apps" / "bootstrap-installer" / "src-tauri" / "src" / "install_script.rs",
)


def test_t2_skills_hub_official_repo_is_qubit_owned(runtime_on_path):
    """The Skills Hub official source names the Qubit repo, not the upstream org."""
    from qubit_cli import install_source as s
    from tools.skills_hub_official import OptionalSkillSource

    expected = s.official_repository()
    assert OptionalSkillSource.OFFICIAL_REPO == expected
    _assert_t2_qubit_owned(OptionalSkillSource.OFFICIAL_REPO)


def test_t2_local_runtime_catalog_url_is_qubit_owned(runtime_on_path, monkeypatch):
    """The local-runtime catalog refresh reads the Qubit repo's raw asset URL."""
    from qubit_cli import install_source as s
    from qubit_cli.local_runtime import catalog

    monkeypatch.delenv("QUBIT_RAW_ASSET_BASE_URL", raising=False)
    assert catalog._CATALOG_URL == s.raw_asset_url("qubit_cli/local_runtime/catalog.json")
    _assert_t2_qubit_owned(catalog._CATALOG_URL)


def test_t2_installer_repo_identity_is_qubit_owned():
    """Installer sources clone/boot from the Qubit repo, never the upstream one."""
    for path in _INSTALLER_REPO_FILES:
        text = path.read_text(encoding="utf-8")
        assert "NousResearch/hermes-agent" not in text, path
        assert "hermes-agent.git" not in text, path
        assert "cgraph-dev/Qubit-AIO" in text, path



# ---------------------------------------------------------------------------
# T5 — deep de-brand of product-surface identifiers in the gateway + cron
# modules (bounded slice: ``qubit_runtime/gateway/**`` and
# ``qubit_runtime/cron/**``).
#
# The remaining ``Hermes``/``hermes_``/``hermes-`` tokens in those trees are
# almost entirely load-bearing wire/persisted/foreign-service contracts
# (``HERMES_*`` env names, ``X-Hermes-Session-*`` read-old headers,
# ``metadata.hermes``/``hermes_profile``/``hermes_plugin_id`` persisted keys,
# the ``hermes-agent`` virtual model, ``hermes-gateway`` legacy kind/pipe and
# foreign-service detection).  T5 de-branded only the genuinely-local
# identifiers and stale ``~/.hermes`` home references that clearly mean Qubit's
# own runtime, and pinned each preserved contract so a future sweep cannot
# over-rebrand them.
# ---------------------------------------------------------------------------

_T5_GATEWAY = RUNTIME / "gateway"
_T5_CRON = RUNTIME / "cron"


def _t5_read(path) -> str:
    return path.read_text(encoding="utf-8")


def test_t5_gateway_update_spawn_renamed_local_identifier():
    """The detached-update helper's local cmd vars are Qubit-named, not Hermes."""
    text = _t5_read(_T5_GATEWAY / "slash_commands.py")
    assert "def _spawn_detached_update(qubit_cmd, output_path, exit_code_path)" in text
    assert "_spawn_detached_update(qubit_cmd, output_path, exit_code_path)" in text
    assert "qubit_cmd_str" in text
    assert "hermes_cmd_str" not in text
    assert "hermes_cmd =" not in text
    # The i18n message key is a locale contract and must stay resolvable.
    assert "gateway.update.hermes_cmd_not_found" in text


def test_t5_cron_store_docs_name_the_qubit_home():
    """Cron store docstrings/comments name <QUBIT_HOME>, not the legacy home."""
    text = _t5_read(_T5_CRON / "jobs.py")
    assert "<QUBIT_HOME>/cron/jobs.json" in text
    assert "<QUBIT_HOME>/profiles/coder/cron/jobs.json" in text
    assert "~/.hermes/cron/jobs.json" not in text
    assert "~/.hermes/profiles/coder" not in text

    scheduler = _t5_read(_T5_CRON / "scheduler.py")
    assert "<QUBIT_HOME>/cron/.tick.lock" in scheduler
    assert "~/.hermes/cron/.tick.lock" not in scheduler


def test_t5_user_facing_home_strings_are_qubit_owned():
    """User-facing messages point at the Qubit home / scripts dir."""
    lifecycle = _t5_read(_T5_CRON / "lifecycle_guard.py")
    assert "<QUBIT_HOME>/scripts/" in lifecycle

    base = _t5_read(_T5_GATEWAY / "platforms" / "base.py")
    assert "add the key to <QUBIT_HOME>/.env manually." in base

    qqbot = _t5_read(_T5_GATEWAY / "platforms" / "qqbot" / "adapter.py")
    assert "<QUBIT_HOME>/.update_response" in qqbot
    assert "~/.hermes/.update_response" not in qqbot


def test_t5_stale_home_docstrings_rebranded():
    """Docstrings/comments that describe Qubit's own runtime home are de-branded."""
    for rel, old, new in (
        ("gateway/config.py", "env > ~/.hermes/config.yaml >", "env > <QUBIT_HOME>/config.yaml >"),
        ("gateway/hooks.py", "Hooks live in ~/.hermes/hooks/", "Hooks live in <QUBIT_HOME>/hooks/"),
        ("gateway/pairing.py", "Storage: ~/.hermes/pairing/", "Storage: <QUBIT_HOME>/pairing/"),
        ("gateway/sticker_cache.py", "``~/.hermes/sticker_cache.json``", "``<QUBIT_HOME>/sticker_cache.json``"),
        ("gateway/session_persistence.py", "table in ~/.hermes/state.db)", "table in <QUBIT_HOME>/state.db)"),
        ("gateway/run_turn.py", "go to ~/.hermes/logs/tool_calls.log", "go to <QUBIT_HOME>/logs/tool_calls.log"),
        ("gateway/hosted_rooms.py", "``~/.hermes/profiles/<name>/``", "``<QUBIT_HOME>/profiles/<name>/``"),
    ):
        text = _t5_read(RUNTIME / rel)
        assert old not in text, (rel, old)
        assert new in text, (rel, new)


def test_t5_preserved_wire_and_persisted_contracts_still_present():
    """The load-bearing Hermes contracts T5 deliberately did NOT touch."""
    status = _t5_read(_T5_GATEWAY / "status.py")
    assert '_LEGACY_HOME_KEY = "hermes_home"' in status
    assert '_LEGACY_GATEWAY_KIND = "hermes-gateway"' in status

    api = _t5_read(_T5_GATEWAY / "platforms" / "api_server.py")
    assert '"X-Hermes-Session-Key"' in api
    assert '"hermes-browser-control-v1"' in api

    openai = _t5_read(_T5_GATEWAY / "platforms" / "api_server_openai_routes.py")
    assert '"X-Hermes-Session-Id"' in openai

    base = _t5_read(_T5_GATEWAY / "platforms" / "base.py")
    assert '"hermes_voice"' in base
    assert '"hermes_profile"' in base

    hooks = _t5_read(_T5_GATEWAY / "hooks.py")
    assert 'f"hermes_hook_{hook_name}"' in hooks

    lifecycle = _t5_read(_T5_CRON / "lifecycle_guard.py")
    assert "hermes-gateway" in lifecycle
    assert "ai.hermes.gateway" in lifecycle


# --------------------------------------------------------------------------
# T3b — v0.21.5 parity closure: the two genuine local-models download-control
# gaps (`POST /api/local-models/download/{pause,resume}`). The vendored handler
# lives at ``qubit_runtime/qubit_cli/web_routers/local_models.py`` (708/721);
# Qubit exposes the same ``{"job_id": ...}`` contract on its canonical authority
# path (compat route -> real ``voicebridge.local_models`` gate), never a stub.
# --------------------------------------------------------------------------
_T3B_PAUSE = "/api/local-models/download/pause"
_T3B_RESUME = "/api/local-models/download/resume"


def test_t3b_local_models_pause_resume_routes_registered():
    """Both reference routes resolve to a registered, non-gated compat handler."""
    from voicebridge import qubit_compat as qc

    for path, handler in (
        (_T3B_PAUSE, "h_local_models_download_pause"),
        (_T3B_RESUME, "h_local_models_download_resume"),
    ):
        route, _params = qc.match("POST", path)
        assert route is not None, path
        assert route["handler"] == handler, route
        assert route["disposition"] != "gated", route


def test_t3b_pause_resume_dispatch_to_real_typed_handler(monkeypatch):
    """The compat routes dispatch end-to-end to the real Qubit policy functions.

    With no managed local-runtime downloader, the same request/response contract
    as the vendored handler yields Qubit's real typed gates (BAD_INPUT ->
    CONFIRM -> BROKERED), never a fabricated `{"ok": true, "paused"/"resumed"}`.
    """
    from voicebridge import local_models as LM
    from voicebridge import qubit_compat as qc

    monkeypatch.setattr(LM, "estop_engaged", lambda _cfg: False)

    for path in (_T3B_PAUSE, _T3B_RESUME):
        # Full dispatch path (route -> handler registry -> policy function).
        status, body = qc.dispatch(object(), "POST", path, {},
                                   {"job_id": "job-1"})
        assert status == 403 and body["code"] == LM.CONFIRM_CODE, (path, body)

        status, body = qc.dispatch(object(), "POST", path, {},
                                   {"job_id": "job-1", "confirm": True})
        assert status == 403, (path, status)
        assert body["state"] == "REFUSED", (path, body)
        assert body["code"] == LM.BROKERED_CODE, (path, body)
        assert body["owner"] == "qubit-policy" and body["rollback"], (path, body)
        # Never the reference success shape: no pause/resume was performed.
        assert body.get("paused") is not True and body.get("resumed") is not True

        _status, body = qc.dispatch(object(), "POST", path, {}, {})
        assert body["code"] == LM.BAD_INPUT_CODE, (path, body)


def test_t3b_pause_resume_voice_path_and_backend_actions(monkeypatch):
    """The dashboard-owned voice guards still resolve; the backend gates stand.

    The spoken form of a brokered download control is a typed refusal with
    guidance (``VOICE_BROKERED_ONLY``); the new ``_b_local_models`` actions the
    compat routes dispatch to resolve to the same Qubit broker gate.
    """
    from voicebridge import local_models as LM
    from voicebridge import voice_commands as VC

    monkeypatch.setattr(LM, "estop_engaged", lambda _cfg: False)

    for text, guard_id in (
        ("qubit, pause local model download", "guard_local_models_download_pause"),
        ("qubit, resume local model download", "guard_local_models_download_resume"),
    ):
        res = VC.resolve(text)
        assert res.kind == VC.MATCH and res.entry.id == guard_id, (text, res)

    class _Ctx:
        cfg = object()

    for action in ("download_pause", "download_resume"):
        out = VC._BACKENDS["local_models"](
            _Ctx(), {"action": action, "job_id": "job-1", "confirmed": True})
        result = out["local_models"]
        assert result["state"] == "REFUSED", (action, result)
        assert result["code"] == LM.BROKERED_CODE, (action, result)
