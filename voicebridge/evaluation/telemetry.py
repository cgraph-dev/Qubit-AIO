"""OC-D32 — opt-in-gated OpenTelemetry-style traces, spans, and metrics.

Qubit records OTLP-shaped telemetry locally and exports **nothing** unless the
operator explicitly opts in. The gate is checked on every export:

* opt-in is an explicit truthy flag in config ``telemetry.enabled`` or the
  ``QUBIT_OTEL_ENABLED`` environment variable;
* an endpoint (``telemetry.endpoint`` / ``QUBIT_OTEL_ENDPOINT``) must be set;
* export requires a caller-supplied transport, so there is no implicit network
  client and no dependency on the OpenTelemetry SDK.

Trace context follows the W3C ``traceparent`` shape so spans can be correlated
across the single audit path. Nothing here records prompt/response content —
attribute values are caller-supplied metadata only.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

SCHEMA = "qubit-otel/1"
W3C_VERSION = "00"

OTEL_DISABLED = "OTEL_DISABLED"
OTEL_MALFORMED = "OTEL_MALFORMED"

_TRACEPARENT = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace>[0-9a-f]{32})-"
    r"(?P<span>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$")
_TRUTHY = {"1", "true", "yes", "on"}


class TelemetryError(Exception):
    """Typed telemetry failure (never a traceback at a boundary)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _get(cfg: Any, *path: str, default: Any = None) -> Any:
    get = getattr(cfg, "get", None)
    if callable(get):
        try:
            return get(*path, default=default)
        except TypeError:
            pass
    node: Any = cfg
    for p in path:
        if isinstance(node, dict) and p in node:
            node = node[p]
        else:
            return default
    return node


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in _TRUTHY


def telemetry_enabled(cfg: Any = None,
                      environ: Optional[Mapping[str, str]] = None) -> bool:
    """True only when the operator opted in AND an endpoint is configured."""
    import os

    env = environ if environ is not None else os.environ
    opt_in = _truthy(env.get("QUBIT_OTEL_ENABLED", ""))
    endpoint = str(env.get("QUBIT_OTEL_ENDPOINT", "") or "").strip()
    if cfg is not None:
        opt_in = opt_in or _truthy(_get(cfg, "telemetry", "enabled",
                                        default=False))
        endpoint = endpoint or str(
            _get(cfg, "telemetry", "endpoint", default="") or "").strip()
    return bool(opt_in and endpoint)


def endpoint_for(cfg: Any = None,
                 environ: Optional[Mapping[str, str]] = None) -> str:
    import os

    env = environ if environ is not None else os.environ
    if cfg is not None:
        value = str(_get(cfg, "telemetry", "endpoint", default="") or "")
        if value.strip():
            return value.strip()
    return str(env.get("QUBIT_OTEL_ENDPOINT", "") or "").strip()


def traceparent(trace_id: str, span_id: str, sampled: bool = True) -> str:
    return f"{W3C_VERSION}-{trace_id}-{span_id}-{'01' if sampled else '00'}"


def parse_traceparent(header: str) -> Optional[Dict[str, Any]]:
    """Parse a W3C traceparent header, or None when malformed."""
    m = _TRACEPARENT.match(str(header or "").strip().lower())
    if not m:
        return None
    if (m.group("version") == "ff" or set(m.group("trace")) == {"0"}
            or set(m.group("span")) == {"0"}):
        return None
    return {"trace_id": m.group("trace"), "span_id": m.group("span"),
            "sampled": bool(int(m.group("flags"), 16) & 0x01)}


class Span:
    def __init__(self, name: str, *, trace_id: str, span_id: str,
                 parent_span_id: str = "", attributes: Optional[dict] = None,
                 start: Optional[float] = None) -> None:
        self.name = name
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.attributes: Dict[str, Any] = dict(attributes or {})
        self.events: List[Dict[str, Any]] = []
        self.status = "UNSET"
        self.start = time.time() if start is None else float(start)
        self.end: Optional[float] = None

    def add_event(self, name: str, attributes: Optional[dict] = None) -> None:
        self.events.append({"name": str(name), "time": time.time(),
                            "attributes": dict(attributes or {})})

    def set_status(self, status: str) -> None:
        self.status = str(status).upper()

    def finish(self) -> None:
        if self.end is None:
            self.end = time.time()

    @property
    def duration_ms(self) -> float:
        end = self.end if self.end is not None else time.time()
        return round((end - self.start) * 1000.0, 3)

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "trace_id": self.trace_id,
                "span_id": self.span_id, "parent_span_id": self.parent_span_id,
                "duration_ms": self.duration_ms, "status": self.status,
                "attributes": dict(self.attributes), "events": list(self.events)}


class _SpanContext:
    def __init__(self, tracer: "Tracer", span: Span) -> None:
        self._tracer = tracer
        self.span = span

    def __enter__(self) -> Span:
        return self.span

    def __exit__(self, *exc: Any) -> bool:
        if exc and exc[0] is not None:
            self.span.set_status("ERROR")
            self.span.add_event("exception",
                                {"type": exc[0].__name__})
        self.span.finish()
        self._tracer._record(self.span)
        return False


class Tracer:
    """Local, bounded span/metric recorder. Export is separately gated."""

    def __init__(self, *, enabled: bool = False, service: str = "qubit",
                 max_spans: int = 1000,
                 id_factory: Callable[[int], str] = secrets.token_hex) -> None:
        self.enabled = bool(enabled)
        self.service = str(service)
        self.max_spans = int(max_spans)
        self._id_factory = id_factory
        self._spans: List[Span] = []
        self._metrics: List[Dict[str, Any]] = []

    def _new_trace(self) -> str:
        return self._id_factory(16)

    def _new_span(self) -> str:
        return self._id_factory(8)

    def start_span(self, name: str, *, parent: Optional[Span] = None,
                   attributes: Optional[dict] = None,
                   trace_id: str = "") -> _SpanContext:
        tid = trace_id or (parent.trace_id if parent else "") or self._new_trace()
        span = Span(str(name), trace_id=tid, span_id=self._new_span(),
                    parent_span_id=parent.span_id if parent else "",
                    attributes=attributes)
        return _SpanContext(self, span)

    def _record(self, span: Span) -> None:
        self._spans.append(span)
        if len(self._spans) > self.max_spans:
            del self._spans[: len(self._spans) - self.max_spans]

    def spans(self) -> List[Dict[str, Any]]:
        return [s.as_dict() for s in sorted(self._spans, key=lambda s: s.start)]

    # -------------------------------------------------------------- metrics
    def counter(self, name: str, value: float = 1.0, *,
                attributes: Optional[dict] = None) -> None:
        self._metric(name, value, "counter", attributes)

    def histogram(self, name: str, value: float, *,
                  attributes: Optional[dict] = None) -> None:
        self._metric(name, value, "histogram", attributes)

    def _metric(self, name: str, value: float, kind: str,
                attributes: Optional[dict]) -> None:
        self._metrics.append({
            "name": str(name), "kind": kind, "value": float(value),
            "attributes": dict(attributes or {}), "time": time.time(),
        })

    def metrics(self) -> List[Dict[str, Any]]:
        return list(self._metrics)

    def metric_summary(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for m in self._metrics:
            bucket = out.setdefault(m["name"], {"kind": m["kind"],
                                                "count": 0, "sum": 0.0,
                                                "min": None, "max": None})
            bucket["count"] += 1
            bucket["sum"] = round(bucket["sum"] + m["value"], 6)
            bucket["min"] = m["value"] if bucket["min"] is None \
                else min(bucket["min"], m["value"])
            bucket["max"] = m["value"] if bucket["max"] is None \
                else max(bucket["max"], m["value"])
        return out

    def clear(self) -> None:
        self._spans = []
        self._metrics = []


def export_payload(tracer: Tracer, cfg: Any = None,
                   environ: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Build the OTLP-shaped export body — refuses unless opt-in is set."""
    if not (tracer.enabled or telemetry_enabled(cfg, environ)):
        raise TelemetryError(
            OTEL_DISABLED,
            "telemetry is not opted in (set telemetry.enabled + endpoint or "
            "QUBIT_OTEL_ENABLED + QUBIT_OTEL_ENDPOINT); nothing was exported")
    return {"schema": SCHEMA, "service": tracer.service,
            "endpoint": endpoint_for(cfg, environ),
            "resource_spans": [{"scope": tracer.service,
                                "spans": tracer.spans()}],
            "metrics": tracer.metrics()}


def export(tracer: Tracer, transport: Callable[[Dict[str, Any]], Any], *,
           cfg: Any = None, environ: Optional[Mapping[str, str]] = None,
           dry_run: bool = True) -> Dict[str, Any]:
    """Export via a caller-supplied transport, gated on opt-in.

    ``dry_run`` (default) builds the payload without invoking the transport, so
    a one-off export can be inspected. Passing ``dry_run=False`` invokes the
    injected transport; Qubit never opens a network socket itself.
    """
    payload = export_payload(tracer, cfg, environ)
    if dry_run:
        return {"state": "DRY_RUN", "exported": False,
                "spans": len(payload["resource_spans"][0]["spans"]),
                "payload": payload}
    result = transport(payload)
    return {"state": "EXPORTED", "exported": True,
            "result": result, "payload_size": len(json.dumps(payload))}
