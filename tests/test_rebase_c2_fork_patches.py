# tests/test_rebase_c2_fork_patches.py
"""Step C2 gate: the ``agent/`` + ``tui_gateway/`` genuine fork patches on the
v0.21.5 reset (``FORK_PATCH_MATRIX.md`` §2.1, §4).

Each test pins one matrix row: either a re-applied patch (behavior/source
asserted) or a "take OURS / already satisfied" row whose fork behavior the reset
already carries (asserted so it cannot silently regress).
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "qubit_runtime"


@pytest.fixture
def runtime_on_path(tmp_path, monkeypatch):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.syspath_prepend(str(RUNTIME))
    yield


def _src(rel: str) -> str:
    return (RUNTIME / rel).read_text(encoding="utf-8")


# ── matrix #11 — tui_gateway/server.py: vendored-src-root import guard ────────
def test_server_seed_vendored_src_root(runtime_on_path):
    server = importlib.import_module("tui_gateway.server")
    root = server._vendored_src_root()
    assert root == RUNTIME.resolve()
    env = server._seed_vendored_src_root_pythonpath({"PYTHONPATH": "/somewhere/else"})
    assert env["PYTHONPATH"].split(":")[0] == str(RUNTIME)
    assert env["HERMES_PYTHON_SRC_ROOT"] == str(RUNTIME)
    assert env["QUBIT_PYTHON_SRC_ROOT"] == str(RUNTIME)
    # idempotent: re-seeding must not duplicate the root.
    again = server._seed_vendored_src_root_pythonpath(dict(env))
    assert again["PYTHONPATH"].count(str(RUNTIME)) == 1


def test_server_slash_worker_uses_seed():
    src = _src("tui_gateway/server.py")
    assert "_seed_vendored_src_root_pythonpath(env)" in src
    # the old hand-rolled inline prepend must be gone (superseded by the helper)
    assert "module_root = str(Path(__file__).resolve().parent.parent)" not in src


# ── matrix #1 — agent/skill_utils.py W-BRAND skill suite ─────────────────────
def test_skill_utils_debrand_suite(runtime_on_path):
    su = importlib.import_module("agent.skill_utils")
    for name in (
        "_qubit_metadata", "debrand_skill_text", "debrand_skill_tree",
        "iter_skill_text_files", "migrate_legacy_skill_dirs",
        "resolve_legacy_skill_name", "_debrand_case_like", "LEGACY_SKILL_NAME_RENAMES",
    ):
        assert hasattr(su, name), name
    assert hasattr(su, "skill_matches_apps")
    assert su.resolve_legacy_skill_name("hermes-voice-mode") == "qubit-voice-mode"
    # read-old metadata.hermes still accepted
    assert su._qubit_metadata({"metadata": {"hermes": {"x": 1}}}) == {"x": 1}
    assert su._qubit_metadata({"metadata": {"qubit": {"y": 2}}}) == {"y": 2}


# ── matrix #2 — agent/prompt_builder.py context-file candidates ──────────────
def test_prompt_builder_qubit_context_files(runtime_on_path):
    pb = importlib.import_module("agent.prompt_builder")
    assert hasattr(pb, "_find_qubit_md")
    assert hasattr(pb, "_load_qubit_md")
    src = _src("agent/prompt_builder.py")
    assert '".qubit.md", "QUBIT.md", ".hermes.md", "HERMES.md"' in src


# ── matrix #9/#10 — agent/lsp qubit dirs ─────────────────────────────────────
def test_lsp_qubit_dirs(runtime_on_path):
    from agent.lsp import install
    assert install.qubit_lsp_bin_dir().parent.name == "lsp"
    assert "qubit_lsp_session_dir" in _src("agent/lsp/servers.py")
    for rel in ("agent/lsp/install.py", "agent/lsp/servers.py"):
        assert "hermes_lsp_" not in _src(rel)


# ── matrix #3–#8 — verified "OURS supersedes" rows ───────────────────────────
def test_model_metadata_no_hermes_home_no_chatgpt_helper():
    src = _src("agent/model_metadata.py")
    assert "get_hermes_home" not in src and "mkdir_under_hermes_home" not in src
    # no caller anywhere needs the old inline extractor (moved to qubit_cli in OURS)
    assert "_extract_chatgpt_account_id" not in "".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in RUNTIME.rglob("*.py"))


def test_anthropic_credentials_qubit_oauth_family(runtime_on_path):
    ac = importlib.import_module("agent.anthropic_credentials")
    for name in (
        "read_qubit_oauth_credentials", "_get_qubit_oauth_file",
        "_root_qubit_oauth_file", "_write_qubit_oauth_credentials",
        "run_qubit_oauth_login_pure", "anthropic_route_is_oauth",
    ):
        assert hasattr(ac, name), name
    # path resolves under QUBIT_HOME
    assert ac._get_qubit_oauth_file().name == ".anthropic_oauth.json"


def test_credential_pool_uses_shared_retry_parser(runtime_on_path):
    src = _src("agent/credential_pool.py")
    assert "from agent.retry_utils import reset_delay_from_message" in src
    assert "_extract_retry_delay_seconds" not in src


def test_memory_manager_ctx_bound_threads(runtime_on_path):
    src = _src("agent/memory_manager.py")
    assert "spawn_context_thread" in src
    mp = _src("agent/memory_provider.py")
    assert "def ctx_bound(" in mp and "def spawn_context_thread(" in mp


def test_codex_replay_name_coercion(runtime_on_path):
    from agent.message_sanitization import coerce_tool_name
    assert coerce_tool_name("ok-name_1") == "ok-name_1"
    bad = coerce_tool_name("bad name!with🎉chars")
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", bad)
    src = _src("agent/codex_responses_adapter.py")
    assert "coerce_tool_name" in src


def test_chat_completion_wait_notice_present(runtime_on_path):
    wn = importlib.import_module("agent.chat_completion_wait_notice")
    text = wn.wait_notice_text("m", 42.0, "first_chunk", ("TTFB", 5.0))
    assert "auto-reconnect" in text


# ── matrix #12–#21 — tui_gateway renames / take-OURS ─────────────────────────
@pytest.mark.parametrize("rel,needle", [
    ("tui_gateway/methods_tools.py", "reset_qubit_home_override"),
    ("tui_gateway/methods_profiles.py", "_qubit_home_scope"),
    ("tui_gateway/methods_session.py", "profile_name_for_home"),
    ("tui_gateway/methods_connectors.py", "_account_home(request)"),
    ("tui_gateway/model_switch.py", "persist_model_selection"),
    ("tui_gateway/host_supervisor.py", "expected_qubit_home"),
    ("tui_gateway/launch_profile_policy.py", "set_qubit_home_override"),
    ("tui_gateway/methods_prompt.py", '"status": "expired"'),
])
def test_tui_gateway_fork_surfaces(rel, needle):
    assert needle in _src(rel), f"{rel} missing {needle!r}"


def test_no_hermes_home_accessors_in_buckets():
    offenders = []
    for p in list((RUNTIME / "agent").rglob("*.py")) + list((RUNTIME / "tui_gateway").rglob("*.py")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"\bget_process_hermes_home\b|\bget_hermes_home\b", text):
            offenders.append(str(p.relative_to(RUNTIME)))
    assert offenders == [], offenders
