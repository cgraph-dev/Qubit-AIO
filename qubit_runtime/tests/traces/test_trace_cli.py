from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stderr, redirect_stdout

from qubit_cli.traces_cmd import cmd_traces
from traces.recorder import record_trace
from traces.settings import TraceSettings


def _args(action, **kw):
    base = dict(traces_action=action, json=False, db=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _seed(db):
    settings = TraceSettings(enabled=True, db_path=str(db), max_excerpt_chars=80)
    record_trace(prompt="first", response="one", engine="native", latency_ms=10,
                 session_id="s1", settings=settings)
    record_trace(prompt="second", response="two", engine="native", latency_ms=20,
                 session_id="s2", success=False, error_code="boom", settings=settings)


def test_list_json_and_filter(tmp_path):
    db = tmp_path / "traces.db"
    _seed(db)
    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_traces(_args("list", limit=10, session=None, json=True, db=str(db))) == 0
    payload = json.loads(out.getvalue())
    assert len(payload) == 2
    assert payload[0]["session_id"] == "s2"

    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_traces(_args("list", limit=10, session="s1", json=True, db=str(db))) == 0
    payload = json.loads(out.getvalue())
    assert [row["session_id"] for row in payload] == ["s1"]


def test_show_and_missing_id(tmp_path):
    db = tmp_path / "traces.db"
    _seed(db)
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        assert cmd_traces(_args("show", trace_id="missing", json=True, db=str(db))) == 2
    assert "trace_not_found" in err.getvalue()

    from traces.store import TraceStore
    with TraceStore(db) as store:
        trace_id = store.list()[0].id
    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_traces(_args("show", trace_id=trace_id, json=True, db=str(db))) == 0
    assert json.loads(out.getvalue())["id"] == trace_id


def test_export_jsonl_and_prune(tmp_path):
    db = tmp_path / "traces.db"
    out_path = tmp_path / "out.jsonl"
    _seed(db)
    err = io.StringIO()
    with redirect_stderr(err):
        assert cmd_traces(_args("export", output=str(out_path), db=str(db))) == 0
    assert "exported 2" in err.getvalue()
    assert len(out_path.read_text().splitlines()) == 2

    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_traces(_args("prune", max_age_days=0, max_records=1, json=True, db=str(db))) == 0
    payload = json.loads(out.getvalue())
    assert payload == {"pruned": 1, "remaining": 1}


def test_malformed_store_is_a_typed_exit_two(tmp_path):
    db = tmp_path / "traces.db"
    db.write_bytes(b"not sqlite at all")
    err = io.StringIO()
    with redirect_stderr(err):
        assert cmd_traces(_args("list", limit=10, session=None, json=True, db=str(db))) == 2
    assert "trace_store_corrupt" in err.getvalue()


def test_missing_store_is_a_typed_exit_two(tmp_path):
    db = tmp_path / "never-created.db"
    err = io.StringIO()
    with redirect_stderr(err):
        assert cmd_traces(_args("list", limit=10, session=None, json=True, db=str(db))) == 2
    assert "trace_store_missing" in err.getvalue()
    assert not db.exists()
