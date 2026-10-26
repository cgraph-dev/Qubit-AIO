"""Opt-in local traces: redacted SQLite records, no network, no default telemetry."""
from traces.errors import (
    TraceError,
    TraceInputError,
    TraceNotFoundError,
    TraceStoreCorruptError,
    TraceStoreError,
    TraceStoreVersionError,
)
from traces.recorder import new_trace_id, record_trace, try_record_trace
from traces.settings import TraceSettings, default_db_path, load_settings, resolve_settings
from traces.store import CURRENT_SCHEMA_VERSION, TraceRow, TraceStore

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "TraceError",
    "TraceInputError",
    "TraceNotFoundError",
    "TraceRow",
    "TraceSettings",
    "TraceStore",
    "TraceStoreCorruptError",
    "TraceStoreError",
    "TraceStoreVersionError",
    "default_db_path",
    "load_settings",
    "new_trace_id",
    "record_trace",
    "resolve_settings",
    "try_record_trace",
]
