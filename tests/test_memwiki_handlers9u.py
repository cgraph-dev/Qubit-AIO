"""memory-wiki READ surface (installed user dashboard plugin).

The plugin's tab JS calls ``/api/plugins/memory-wiki/*`` but its FastAPI
backend cannot mount on this listener, so five read-only compat handlers call
the plugin's own core against a read-only state.db. Proved live in the
browser (/memory-wiki renders real subjects); these pin the wire contract
hermetically with a stub core (no network, no live DB).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_CORE = '''
def build_memory_overview(db, *, subject_limit=50, day_limit=30):
    return {"subjects": [{"slug": "s"}], "daily_logs": [{"date": "d"}],
            "recent_sessions": [], "notes": {}}

def build_memory_subjects(db, *, limit=100, query=None):
    return [{"slug": "s", "query": query, "limit": limit}]

def get_memory_subject(db, slug):
    return {"slug": slug} if slug == "s" else None

def build_daily_logs(db, *, limit_days=60):
    return [{"date": "2026-01-01", "limit_days": limit_days}]

def get_daily_log(db, date):
    return {"date": date} if date == "2026-01-01" else None
'''


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seed_plugin(tmp_path):
    dash = (tmp_path / "qhome" / "plugins" / "memory-wiki" / "dashboard")
    dash.mkdir(parents=True, exist_ok=True)
    (dash / "memory_wiki_core.py").write_text(_CORE)


def test_missing_plugin_is_typed_404(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/memory-wiki/overview", {}, {})
    assert code == 404
    assert out["code"] == "PLUGIN_NOT_FOUND"


def test_overview_subjects_days_shapes(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_plugin(tmp_path)
    monkeypatch.setattr(HC, "_memwiki_db", lambda cfg: object())
    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/memory-wiki/overview", {}, {})
    assert code == 200, out
    assert out["subjects"] == [{"slug": "s"}]
    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/memory-wiki/subjects", {"q": "hi"},
        {})
    assert code == 200, out
    assert out["query"] == "hi" and out["subjects"][0]["limit"] == 100
    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/memory-wiki/days", {"limit": "7"}, {})
    assert code == 200, out
    assert out["limit"] == 7


def test_subject_and_day_miss_are_404(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_plugin(tmp_path)
    monkeypatch.setattr(HC, "_memwiki_db", lambda cfg: object())
    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/memory-wiki/subjects/nope", {}, {})
    assert code == 404 and out["code"] == "NOT_FOUND", (code, out)
    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/memory-wiki/days/1999-01-01", {}, {})
    assert code == 404 and out["code"] == "NOT_FOUND", (code, out)
    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/memory-wiki/subjects/s", {}, {})
    assert code == 200 and out["subject"] == {"slug": "s"}, (code, out)
