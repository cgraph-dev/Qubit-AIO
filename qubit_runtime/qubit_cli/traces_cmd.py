"""``qubit traces`` handler. Reading a store never requires tracing to be enabled."""
from __future__ import annotations

import json
import sys

from traces.errors import TraceError
from traces.settings import load_settings
from traces.store import TraceStore


def _settings(args):
    settings = load_settings()
    override = getattr(args, "db", None)
    if override:
        from dataclasses import replace

        settings = replace(settings, db_path=str(override))
    return settings


def _store(args) -> TraceStore:
    return TraceStore(_settings(args).path, create=False)


def _row_line(row) -> str:
    latency = "?" if row.latency_ms is None else f"{row.latency_ms:.1f}"
    return "\t".join([
        row.id,
        row.created_at,
        row.engine or "-",
        row.model or "-",
        "ok" if row.success else (row.error_code or "failed"),
        latency,
    ])


def _print_rows(rows, as_json: bool) -> None:
    if as_json:
        print(json.dumps([row.as_dict() for row in rows], ensure_ascii=False))
        return
    for row in rows:
        print(_row_line(row))


def cmd_traces(args) -> int:
    action = getattr(args, "traces_action", "")
    try:
        with _store(args) as store:
            if action == "list":
                rows = store.list(
                    limit=int(getattr(args, "limit", 50) or 50),
                    session_id=getattr(args, "session", None))
                _print_rows(rows, bool(getattr(args, "json", False)))
                return 0
            if action == "show":
                row = store.get(args.trace_id)
                if getattr(args, "json", False):
                    print(json.dumps(row.as_dict(), ensure_ascii=False))
                else:
                    for key, value in row.as_dict().items():
                        print(f"{key}: {value}")
                return 0
            if action == "export":
                output = getattr(args, "output", None)
                count = store.export_jsonl(output)
                if output:
                    print(f"exported {count} trace(s) to {output}", file=sys.stderr)
                return 0
            if action == "prune":
                settings = _settings(args)
                max_age = getattr(args, "max_age_days", None)
                max_records = getattr(args, "max_records", None)
                deleted = store.prune(
                    max_age_days=settings.max_age_days if max_age is None else max_age,
                    max_records=settings.max_records if max_records is None else max_records)
                if getattr(args, "json", False):
                    print(json.dumps({"pruned": deleted, "remaining": store.count()}))
                else:
                    print(f"pruned {deleted} trace(s)")
                return 0
    except TraceError as exc:
        print(f"traces: [{exc.code}] {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"traces: export failed: {exc}", file=sys.stderr)
        return 1
    print(f"traces: unknown action {action!r}", file=sys.stderr)
    return 2
