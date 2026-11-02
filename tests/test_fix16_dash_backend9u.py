"""Fix lane 16 — backend parity gaps vs the reference dashboard (:9119).

Hermetic coverage for the served surfaces this lane owns:

* ``GET /api/system/stats`` carries real psutil CPU/memory/disk/uptime/load
  metrics when psutil is importable (not just the identity block).
* ``GET /api/memory/providers`` is a 200 provider list, never the old
  ``NOT_FOUND``; the local row is preserved as the honest fallback.
* The memory-wiki read surface is routed to the Qubit compat handlers, bounds
  the per-session message window, and caches the aggregate so a populated
  profile cannot hang the page.
* Plugin install accepts a Git URL (``url`` field / URL-shaped identifier) and
  still requires confirmation + the e-stop gate.
* Config defaults carry the reference's ``worktrees`` and ``hosted_rooms``
  sections.

No live service, network, or workspace mutation.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
# The venv carries an editable install pointing at the main checkout; put THIS
# worktree's runtime first so the tests exercise the code under test.
_RUNTIME = str(REPO_ROOT / "qubit_runtime")
if _RUNTIME in sys.path:
    sys.path.remove(_RUNTIME)
sys.path.insert(0, _RUNTIME)

pytestmark = pytest.mark.unit


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ── system stats ─────────────────────────────────────────────────────────

def test_system_stats_carry_psutil_metrics(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.h_system_stats(cfg, {}, {}, {})
    assert code == 200, out
    assert out["psutil"] is True, out
    for key in ("memory", "disk", "cpu_percent", "uptime_seconds",
                "load_avg", "process"):
        assert key in out, (key, out)
    assert out["memory"]["total"] > 0
    assert out["disk"]["total"] > 0
    assert out["qubit_version"], out


# ── memory providers ─────────────────────────────────────────────────────

def test_memory_providers_list_is_not_404(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    # Keep the compat fallback hermetic: no runtime import/subprocess probes.
    monkeypatch.setattr(HC, "_vendored_runtime",
                        lambda _cfg: (_ for _ in ()).throw(RuntimeError("test")))
    code, out = HC.dispatch(cfg, "GET", "/api/memory/providers", {}, {})
    assert code == 200, out
    names = [row.get("name") for row in out.get("providers", [])]
    assert "qubit-local" in names, out
    assert out.get("local_only") is True


# ── memory-wiki: routing, bounding, caching ──────────────────────────────

_CORE = '''
CALLS = {"overview": 0, "seen": []}

def build_memory_overview(db, *, subject_limit=50, day_limit=30):
    CALLS["overview"] += 1
    CALLS["seen"].append(db.get_messages("s1"))
    return {"subjects": [], "daily_logs": [], "recent_sessions": [], "notes": {}}

def build_memory_subjects(db, *, limit=100, query=None):
    return []

def get_memory_subject(db, slug):
    return None

def build_daily_logs(db, *, limit_days=60):
    return []

def get_daily_log(db, date):
    return None
'''


class _FakeDB:
    def __init__(self, messages):
        self.messages = messages

    def get_messages(self, _sid):
        return list(self.messages)


def _seed_memwiki(tmp_path):
    dash = (tmp_path / "qhome" / "plugins" / "memory-wiki" / "dashboard")
    dash.mkdir(parents=True, exist_ok=True)
    (dash / "memory_wiki_core.py").write_text(_CORE)
    return dash


def test_memory_wiki_is_qubit_routed_and_cached(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_memwiki(tmp_path)
    messages = [{"role": "user", "content": str(i)} for i in range(100)]
    monkeypatch.setattr(HC, "_memwiki_db", lambda _cfg: _FakeDB(messages))
    # Fresh cache for this test's temp path.
    monkeypatch.setattr(HC, "_MEMWIKI_CACHE", {})

    from voicebridge import webui

    assert webui.qubit_read_authority("/api/plugins/memory-wiki/overview")
    first = HC.dispatch(cfg, "GET", "/api/plugins/memory-wiki/overview", {}, {})
    second = HC.dispatch(cfg, "GET", "/api/plugins/memory-wiki/overview", {}, {})
    assert first[0] == 200 and second[0] == 200, (first, second)

    core = sys.modules[HC._MEMWIKI_CORE_MODULE]
    assert core.CALLS["overview"] == 1, core.CALLS
    # The bounded proxy clips a long session to the tail window.
    assert len(core.CALLS["seen"][0]) == HC._MEMWIKI_MAX_MESSAGES_PER_SESSION


def test_bounded_session_messages_clips_tail():
    from voicebridge import qubit_compat as HC

    bounded = HC._BoundedSessionMessages(_FakeDB([1, 2, 3, 4, 5]), 3)
    assert bounded.get_messages("s") == [3, 4, 5]
    assert bounded.get_messages("s") == [3, 4, 5]


# ── webhooks: enable state + confirmation ────────────────────────────────

def test_webhooks_expose_enable_state_and_require_confirm(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/webhooks", {}, {})
    assert code == 200 and "enabled" in out, (code, out)

    code, staged = HC.dispatch(cfg, "POST", "/api/webhooks/enable", {}, {})
    assert code == 409, staged
    assert staged.get("confirmation_id"), staged


# ── plugins: Git URL install keeps the gates ─────────────────────────────

def test_git_url_install_routes_to_runtime_with_confirmation(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    assert HC._looks_like_git_url("https://github.com/owner/repo.git")
    assert not HC._looks_like_git_url("/tmp/local-plugin")

    # Without confirmation the real helper stages and never reaches the runtime.
    code, staged = HC.dispatch(
        cfg, "POST", "/api/dashboard/agent-plugins/install", {},
        {"url": "https://github.com/owner/repo.git"})
    assert code == 409, staged
    assert staged.get("code") == "CONFIRM_REQUIRED", staged
    assert staged.get("state") == "NEEDS_CONFIRMATION", staged

    calls = {}

    def _fake_install(_cfg, source, **kwargs):
        calls["source"] = source
        calls.update(kwargs)
        return 200, {"ok": True, "plugin_name": "repo"}

    monkeypatch.setattr(HC, "_install_git_plugin", _fake_install)
    code, out = HC.dispatch(
        cfg, "POST", "/api/dashboard/agent-plugins/install", {},
        {"url": "https://github.com/owner/repo.git", "confirm": True})
    assert code == 200, out
    assert calls["source"] == "https://github.com/owner/repo.git"
    assert calls["confirm"] is True


def test_install_body_accepts_url_only(tmp_path):
    runtime = REPO_ROOT / "qubit_runtime"
    if str(runtime) not in sys.path:
        sys.path.insert(0, str(runtime))
    from qubit_cli.web_models import _AgentPluginInstallBody

    body = _AgentPluginInstallBody(url="https://github.com/owner/repo.git")
    assert body.identifier == ""
    assert body.url == "https://github.com/owner/repo.git"


# ── config schema defaults ───────────────────────────────────────────────

def test_config_defaults_carry_worktrees_and_hosted_rooms():
    runtime = REPO_ROOT / "qubit_runtime"
    if str(runtime) not in sys.path:
        sys.path.insert(0, str(runtime))
    from qubit_cli.config_defaults import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["worktrees"]["install_state_retention_days"] == 14
    assert DEFAULT_CONFIG["worktrees"]["archive_dirty"] is True
    assert DEFAULT_CONFIG["hosted_rooms"]["discussion"]["max_rounds"] == 3
    assert DEFAULT_CONFIG["hosted_rooms"]["discussion"]["max_messages"] == 10
