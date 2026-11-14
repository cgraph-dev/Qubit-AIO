"""Dependency-light OpenTelemetry GenAI instrumentation for the Business OS.

There is intentionally **no** import of the OpenTelemetry SDK here. The module
produces the same shape an OTel exporter consumes — one trace per run, spans
named for the GenAI semantic conventions, attributes, events, metrics and eval
events — and hands it to an injectable exporter. Tests use the in-memory
exporter; production can adapt the same objects to a real OTLP exporter without
this module taking a new dependency.

Defaults that matter:

* **content capture is OFF** — prompts/completions are never recorded unless
  ``capture_content=True``;
* every attribute and event is passed through :func:`redact_attributes` before
  it reaches an exporter, with exact secret values additionally scrubbed by
  the caller-supplied redactor;
* sampling always keeps errors, timeouts, handoffs, failed evaluations and
  outliers, plus a deterministic baseline of trace ids;
* ids and the clock are injectable, so a test run is deterministic.
"""
from __future__ import annotations

import hashlib
import re
import secrets as _secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping

REDACTED = "***REDACTED***"
PII_REDACTED = "***PII***"
GEN_AI_EVAL_EVENT = "gen_ai.evaluation.result"
CONTENT_NOT_CAPTURED = "[content capture disabled]"

SPAN_INVOKE_AGENT = "invoke_agent"
SPAN_CHAT = "chat"
SPAN_EXECUTE_TOOL = "execute_tool"
SPAN_RETRIEVAL = "retrieval"
SPAN_APPROVAL = "approval"
SPAN_SANDBOX = "sandbox"
SPAN_EXTERNAL_RECEIPT = "external_receipt"
SPAN_RECONCILIATION = "reconciliation"
CHAIN_SPANS = (
    SPAN_INVOKE_AGENT,
    SPAN_CHAT,
    SPAN_EXECUTE_TOOL,
    SPAN_RETRIEVAL,
    SPAN_APPROVAL,
    SPAN_SANDBOX,
    SPAN_EXTERNAL_RECEIPT,
    SPAN_RECONCILIATION,
)

STATUS_UNSET = "UNSET"
STATUS_OK = "OK"
STATUS_ERROR = "ERROR"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_CANCELLED = "CANCELLED"
_ERROR_STATUSES = frozenset({STATUS_ERROR, STATUS_TIMEOUT})

_SENSITIVE_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "cookie",
    "private_key",
    "client_secret",
    "credential",
)
_NON_SECRET_TOKEN_KEYS = frozenset(
    {
        "max_tokens",
        "max_tokens_per_turn",
        "tokens",
        "token_count",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "estimated_prompt_tokens",
        "projected_tokens",
        "remaining_tokens",
    }
)
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+\d[\d\s().-]{7,}\d|\(\d{2,4}\)\s*[\d\s.-]{5,}\d)(?!\w)")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_PROVIDER_KEY_RE = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{6,}\b")
_AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")


def _default_clock() -> float:
    return time.time()


def _default_id_factory(kind: str) -> str:
    return _secrets.token_hex(16 if kind == "trace" else 8)


def _sensitive_key(key: str) -> bool:
    flat = key.lower().replace("-", "_")
    leaf = flat.rsplit(".", 1)[-1]
    if flat in _NON_SECRET_TOKEN_KEYS or leaf in _NON_SECRET_TOKEN_KEYS:
        return False
    return any(part in flat for part in _SENSITIVE_PARTS)


def _redact_text(value: str, *, extra_secrets: Iterable[str] = ()) -> str:
    for secret in extra_secrets:
        if secret and secret in value:
            value = value.replace(secret, REDACTED)
    value = _BEARER_RE.sub(rf"\1{REDACTED}", value)
    value = _PROVIDER_KEY_RE.sub(REDACTED, value)
    value = _AWS_KEY_RE.sub(REDACTED, value)
    value = _EMAIL_RE.sub(PII_REDACTED, value)
    value = _PHONE_RE.sub(PII_REDACTED, value)
    return value


def redact_attributes(
    attributes: Mapping[str, Any] | None,
    *,
    extra_secrets: Iterable[str] = (),
) -> dict[str, Any]:
    """Return an export-safe copy of ``attributes``.

    Credential-shaped keys are replaced wholesale; strings are scrubbed for
    bearer tokens, provider keys, e-mail addresses and phone numbers. Exact
    secret values supplied in ``extra_secrets`` are replaced first so a value
    with no recognizable shape still cannot leave the process.
    """
    secrets = tuple(s for s in extra_secrets if isinstance(s, str) and s)
    return _redact_value(dict(attributes or {}), secrets)


def _redact_value(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if _sensitive_key(text_key):
                out[text_key] = REDACTED
            else:
                out[text_key] = _redact_value(item, secrets)
        return out
    if isinstance(value, (list, tuple, set)):
        return [_redact_value(item, secrets) for item in value]
    if isinstance(value, str):
        return _redact_text(value, extra_secrets=secrets)
    return value


@dataclass(frozen=True, slots=True)
class SamplingPolicy:
    """Which runs keep a full trace."""

    baseline_rate: float = 0.1
    sample_errors: bool = True
    sample_timeouts: bool = True
    sample_handoffs: bool = True
    sample_failed_evals: bool = True
    sample_outliers: bool = True
    outlier_duration_ms: float = 30_000.0

    def __post_init__(self) -> None:
        rate = float(self.baseline_rate)
        object.__setattr__(self, "baseline_rate", min(1.0, max(0.0, rate)))
        object.__setattr__(self, "outlier_duration_ms", max(0.0, float(self.outlier_duration_ms)))

    def decide(self, trace_id: str, summary: Mapping[str, Any]) -> tuple[bool, str]:
        if summary.get("error") and self.sample_errors:
            return True, "error"
        if summary.get("timeout") and self.sample_timeouts:
            return True, "timeout"
        if summary.get("handoff") and self.sample_handoffs:
            return True, "handoff"
        if summary.get("failed_eval") and self.sample_failed_evals:
            return True, "failed_eval"
        if self.sample_outliers and _is_outlier(summary, self.outlier_duration_ms):
            return True, "outlier"
        bucket = _baseline_bucket(trace_id)
        if bucket < int(round(self.baseline_rate * 10_000)):
            return True, "baseline"
        return False, "baseline_miss"


def _baseline_bucket(trace_id: str) -> int:
    digest = hashlib.sha256(str(trace_id).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 10_000


def _is_outlier(summary: Mapping[str, Any], threshold_ms: float) -> bool:
    if summary.get("outlier"):
        return True
    try:
        return float(summary.get("duration_ms") or 0.0) >= threshold_ms > 0
    except (TypeError, ValueError):
        return False


@dataclass
class Evaluation:
    name: str
    score: float | None = None
    label: str | None = None
    passed: bool | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class Span:
    """One timed operation inside a run trace."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    start_time: float
    capture_content: bool = False
    end_time: float | None = None
    status: str = STATUS_UNSET
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    content: dict[str, str] = field(default_factory=dict)
    evaluations: list[Evaluation] = field(default_factory=list)
    _closed: bool = False

    def set_attribute(self, key: str, value: Any) -> "Span":
        if self._closed:
            return self
        self.attributes[str(key)] = value
        return self

    def set_attributes(self, values: Mapping[str, Any]) -> "Span":
        for key, value in (values or {}).items():
            self.set_attribute(key, value)
        return self

    def record_content(self, kind: str, text: str) -> "Span":
        """Record prompt/completion text only when content capture is enabled."""
        if not self.capture_content or not isinstance(text, str):
            return self
        self.content[str(kind)] = text
        self.attributes["gen_ai.content.captured"] = True
        return self

    def add_event(self, name: str, attributes: Mapping[str, Any] | None = None) -> "Span":
        self.events.append(
            {
                "name": str(name),
                "time": self.start_time,
                "attributes": dict(attributes or {}),
            }
        )
        return self

    def set_status(self, status: str) -> "Span":
        if not self._closed:
            self.status = status
        return self

    @property
    def duration_ms(self) -> float:
        end = self.end_time if self.end_time is not None else self.start_time
        return max(0.0, (end - self.start_time) * 1000.0)


@dataclass
class RunTrace:
    """One run = one trace. The root span is ``invoke_agent``."""

    trace_id: str
    root: Span
    tracer: "Tracer"
    spans: list[Span] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    evaluations: list[Evaluation] = field(default_factory=list)
    sampled: bool | None = None
    sampling_reason: str | None = None

    def __post_init__(self) -> None:
        self.spans.append(self.root)

    @contextmanager
    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[Span]:
        """Start a child span; closes it with OK unless an error was set."""
        parent = self.tracer._current_span(self) or self.root
        span = Span(
            name=str(name),
            trace_id=self.trace_id,
            span_id=self.tracer._new_id("span"),
            parent_span_id=parent.span_id if parent is not None else None,
            start_time=self.tracer._now(),
            capture_content=self.tracer.capture_content,
        )
        if attributes:
            span.set_attributes(attributes)
        self.spans.append(span)
        self.tracer._push_span(self, span)
        try:
            yield span
        except BaseException as exc:
            span.set_status(STATUS_ERROR)
            span.set_attribute("error.type", type(exc).__name__)
            span.add_event("exception", {"exception.type": type(exc).__name__})
            raise
        finally:
            if span.end_time is None:
                span.end_time = self.tracer._now()
                if span.status == STATUS_UNSET:
                    span.status = STATUS_OK
                span._closed = True
            self.tracer._pop_span(self, span)

    def evaluate(
        self,
        judged_span: Span | None,
        name: str,
        *,
        score: float | None = None,
        label: str | None = None,
        passed: bool | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> Evaluation:
        return self.tracer.record_evaluation(
            judged_span or self.root,
            name,
            score=score,
            label=label,
            passed=passed,
            attributes=attributes,
        )

    def mark_handoff(self, *, reason: str = "") -> None:
        self.summary["handoff"] = True
        self.root.set_attribute("qubit.handoff", True)
        if reason:
            self.root.set_attribute("qubit.handoff.reason", reason)

    def mark_unknown(self, *, outcome: str = "unknown") -> None:
        self.summary["unknown_outcome"] = True
        self.root.set_attribute("qubit.outcome", outcome)

    def mark_outlier(self, *, reason: str = "") -> None:
        self.summary["outlier"] = True
        self.root.set_attribute("qubit.outlier", True)
        if reason:
            self.root.set_attribute("qubit.outlier.reason", reason)


class InMemoryExporter:
    """Collect exported traces, spans, metrics and eval events for tests."""

    def __init__(self) -> None:
        self.traces: list[RunTrace] = []
        self.spans: list[Span] = []
        self.metrics: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.dropped = 0
        self._lock = threading.Lock()

    def export_trace(self, trace: RunTrace, *, redactor: Callable[..., dict] | None = None) -> None:
        redact_fn = redactor or redact_attributes
        safe = _redact_trace(trace, redact_fn)
        with self._lock:
            self.traces.append(safe)
            self.spans.extend(safe.spans)
            for span in safe.spans:
                for evaluation in span.evaluations:
                    self.events.append(
                        {
                            "name": GEN_AI_EVAL_EVENT,
                            "trace_id": safe.trace_id,
                            "span_id": span.span_id,
                            "span_name": span.name,
                            "attributes": redact_fn(evaluation.attributes),
                        }
                    )
            for event in safe.events:
                if event.get("name") == GEN_AI_EVAL_EVENT:
                    # Evaluation events are exported from their judged span
                    # above; do not count the same result twice.
                    continue
                self.events.append(dict(event))

    def record_drop(self) -> None:
        with self._lock:
            self.dropped += 1

    def record_metric(self, sample: Mapping[str, Any], *, redactor: Callable[..., dict] | None = None) -> None:
        redact_fn = redactor or redact_attributes
        row = dict(sample)
        row["attributes"] = redact_fn(dict(row.get("attributes") or {}))
        with self._lock:
            self.metrics.append(row)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "traces": list(self.traces),
                "spans": list(self.spans),
                "metrics": list(self.metrics),
                "events": list(self.events),
                "dropped": self.dropped,
            }


def _redact_trace(trace: RunTrace, redact_fn: Callable[..., dict]) -> RunTrace:
    safe = RunTrace(
        trace_id=trace.trace_id,
        root=trace.root,
        tracer=trace.tracer,
        metadata=dict(trace.metadata),
    )
    safe.summary = dict(trace.summary)
    safe.sampled = trace.sampled
    safe.sampling_reason = trace.sampling_reason
    safe.evaluations = list(trace.evaluations)
    safe.events = [
        {
            "name": event.get("name"),
            "time": event.get("time"),
            "trace_id": event.get("trace_id", trace.trace_id),
            "span_id": event.get("span_id"),
            "attributes": redact_fn(dict(event.get("attributes") or {})),
        }
        for event in trace.events
    ]
    safe.spans = []
    for span in trace.spans:
        clone = Span(
            name=span.name,
            trace_id=span.trace_id,
            span_id=span.span_id,
            parent_span_id=span.parent_span_id,
            start_time=span.start_time,
            capture_content=span.capture_content,
            end_time=span.end_time,
            status=span.status,
            attributes=redact_fn(dict(span.attributes)),
            events=[
                {
                    "name": event.get("name"),
                    "time": event.get("time"),
                    "attributes": redact_fn(dict(event.get("attributes") or {})),
                }
                for event in span.events
            ],
            content=_redact_content(span.content, redact_fn),
            evaluations=[
                Evaluation(
                    name=evaluation.name,
                    score=evaluation.score,
                    label=evaluation.label,
                    passed=evaluation.passed,
                    attributes=redact_fn(dict(evaluation.attributes)),
                )
                for evaluation in span.evaluations
            ],
            _closed=span._closed,
        )
        safe.spans.append(clone)
        if span is trace.root:
            safe.root = clone
    return safe


def _redact_content(content: Mapping[str, str], redact_fn: Callable[..., dict]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in content.items():
        redacted = redact_fn({"value": value}).get("value", REDACTED)
        safe[str(key)] = str(redacted)
    return safe


class Tracer:
    """Create one trace per run and export it according to the sampling policy."""

    def __init__(
        self,
        *,
        exporter: InMemoryExporter | Any | None = None,
        capture_content: bool = False,
        sampling: SamplingPolicy | None = None,
        clock: Callable[[], float] | None = None,
        id_factory: Callable[[str], str] | None = None,
        redactor: Callable[..., dict] | None = None,
    ) -> None:
        self.exporter = exporter
        self.capture_content = bool(capture_content)
        self.sampling = sampling or SamplingPolicy()
        self._clock = clock or _default_clock
        self._id_factory = id_factory or _default_id_factory
        self._redactor = redactor or redact_attributes
        self._local = threading.local()

    # -- internals -----------------------------------------------------
    def _now(self) -> float:
        return float(self._clock())

    def _new_id(self, kind: str) -> str:
        value = str(self._id_factory(kind))
        if kind == "trace":
            return value[:32].ljust(32, "0")
        return value[:16].ljust(16, "0")

    def _stack(self) -> list[Span]:
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = []
            self._local.stack = stack
        return stack

    def _runs(self) -> list[RunTrace]:
        runs = getattr(self._local, "runs", None)
        if runs is None:
            runs = []
            self._local.runs = runs
        return runs

    def _push_span(self, trace: RunTrace, span: Span) -> None:
        self._stack().append(span)

    def _pop_span(self, trace: RunTrace, span: Span) -> None:
        stack = self._stack()
        if stack and stack[-1] is span:
            stack.pop()
        elif span in stack:
            stack.remove(span)

    def _current_span(self, trace: RunTrace) -> Span | None:
        stack = self._stack()
        return stack[-1] if stack else None

    # -- public API ----------------------------------------------------
    @contextmanager
    def start_run(
        self,
        *,
        name: str = SPAN_INVOKE_AGENT,
        attributes: Mapping[str, Any] | None = None,
        run_id: str | None = None,
        tenant_id: str | None = None,
        owner_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        provider_request_id: str | None = None,
    ) -> Iterator[RunTrace]:
        trace_id = self._new_id("trace")
        start = self._now()
        attrs = dict(attributes or {})
        attrs.setdefault("gen_ai.operation.name", name)
        for key, value in (
            ("qubit.tenant.id", tenant_id),
            ("qubit.owner.id", owner_id),
            ("qubit.agent.id", agent_id),
            ("qubit.run.id", run_id),
            ("qubit.task.id", task_id),
            ("qubit.session.id", session_id),
            ("qubit.provider.request.id", provider_request_id),
        ):
            if value is not None:
                attrs.setdefault(key, value)
        root = Span(
            name=name,
            trace_id=trace_id,
            span_id=self._new_id("span"),
            parent_span_id=None,
            start_time=start,
            capture_content=self.capture_content,
            attributes=attrs,
        )
        trace = RunTrace(trace_id=trace_id, root=root, tracer=self)
        trace.metadata.update(
            {
                "tenant_id": tenant_id,
                "owner_id": owner_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "task_id": task_id,
                "session_id": session_id,
                "provider_request_id": provider_request_id,
            }
        )
        self._runs().append(trace)
        self._push_span(trace, root)
        try:
            yield trace
        except BaseException as exc:
            root.set_status(STATUS_ERROR)
            root.set_attribute("error.type", type(exc).__name__)
            raise
        finally:
            self._pop_span(trace, root)
            self._finish_run(trace)
            runs = self._runs()
            if runs and runs[-1] is trace:
                runs.pop()
            elif trace in runs:
                runs.remove(trace)

    @contextmanager
    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[Span]:
        trace = self.current_run()
        if trace is None:
            raise RuntimeError("Tracer.span() requires an active start_run()")
        with trace.span(name, attributes=attributes) as span:
            yield span

    def current_run(self) -> RunTrace | None:
        runs = self._runs()
        return runs[-1] if runs else None

    def record_evaluation(
        self,
        judged_span: Span,
        name: str,
        *,
        score: float | None = None,
        label: str | None = None,
        passed: bool | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> Evaluation:
        result = label
        if result is None and passed is not None:
            result = "pass" if passed else "fail"
        evaluation = Evaluation(
            name=str(name),
            score=score,
            label=label,
            passed=passed,
            attributes=dict(attributes or {}),
        )
        event_attributes = {
            "gen_ai.evaluation.name": str(name),
            "gen_ai.evaluation.result": result,
            "qubit.judged.span_id": judged_span.span_id,
            "qubit.judged.span_name": judged_span.name,
        }
        if score is not None:
            event_attributes["gen_ai.evaluation.score"] = score
        event_attributes.update(dict(attributes or {}))
        evaluation.attributes = event_attributes
        judged_span.evaluations.append(evaluation)
        judged_span.add_event(GEN_AI_EVAL_EVENT, event_attributes)
        run = self.current_run()
        if run is not None:
            run.evaluations.append(evaluation)
            run.events.append(
                {
                    "name": GEN_AI_EVAL_EVENT,
                    "time": self._now(),
                    "trace_id": run.trace_id,
                    "span_id": judged_span.span_id,
                    "attributes": event_attributes,
                }
            )
            if passed is False:
                run.summary["failed_eval"] = True
        return evaluation

    def record_metric(
        self,
        name: str,
        value: float | int = 1,
        *,
        attributes: Mapping[str, Any] | None = None,
        span: Span | None = None,
        unit: str = "",
        kind: str = "counter",
    ) -> None:
        run = self.current_run()
        row = {
            "name": str(name),
            "value": value,
            "unit": unit,
            "kind": kind,
            "time": self._now(),
            "trace_id": run.trace_id if run else None,
            "span_id": span.span_id if span is not None else (
                run.root.span_id if run is not None else None
            ),
            "attributes": dict(attributes or {}),
        }
        if self.exporter is not None:
            self.exporter.record_metric(row, redactor=self._redactor)

    # -- finalization --------------------------------------------------
    def _finish_run(self, trace: RunTrace) -> None:
        end = self._now()
        if trace.root.end_time is None:
            trace.root.end_time = end
            if trace.root.status == STATUS_UNSET:
                trace.root.status = STATUS_OK
            trace.root._closed = True
        if trace.root.status in _ERROR_STATUSES:
            trace.summary["error"] = True
            if trace.root.status == STATUS_TIMEOUT:
                trace.summary["timeout"] = True
        for span in trace.spans:
            if span.status == STATUS_TIMEOUT:
                trace.summary["timeout"] = True
            elif span.status == STATUS_ERROR:
                trace.summary["error"] = True
        trace.summary.setdefault("duration_ms", trace.root.duration_ms)
        trace.summary["tool_errors"] = sum(
            1 for span in trace.spans
            if span.name == SPAN_EXECUTE_TOOL and span.status == STATUS_ERROR
        )
        if trace.summary.get("unknown_outcome"):
            trace.summary.setdefault("unknown_outcome", True)
        keep, reason = self.sampling.decide(trace.trace_id, trace.summary)
        trace.sampled = keep
        trace.sampling_reason = reason
        self._record_run_metrics(trace)
        if self.exporter is None:
            return
        if keep:
            self.exporter.export_trace(trace, redactor=self._redactor)
        else:
            self.exporter.record_drop()

    def _record_run_metrics(self, trace: RunTrace) -> None:
        if self.exporter is None:
            return
        for span in trace.spans:
            self.exporter.record_metric(
                {
                    "name": "gen_ai.client.operation.duration",
                    "value": span.duration_ms / 1000.0,
                    "unit": "s",
                    "kind": "histogram",
                    "time": span.end_time or span.start_time,
                    "trace_id": trace.trace_id,
                    "span_id": span.span_id,
                    "attributes": {
                        "gen_ai.operation.name": span.name,
                        "gen_ai.span.status": span.status,
                    },
                },
                redactor=self._redactor,
            )
        input_tokens = _usage(trace, "gen_ai.usage.input_tokens")
        output_tokens = _usage(trace, "gen_ai.usage.output_tokens")
        cost = _usage(trace, "qubit.cost.usd")
        queue_age = _usage(trace, "qubit.queue.age_ms")
        if input_tokens is not None:
            self._metric(trace, "gen_ai.usage.input_tokens", input_tokens, "token")
        if output_tokens is not None:
            self._metric(trace, "gen_ai.usage.output_tokens", output_tokens, "token")
        if cost is not None:
            self._metric(trace, "qubit.cost.usd", cost, "usd")
        if queue_age is not None:
            self._metric(trace, "qubit.queue.age_ms", queue_age, "ms")
        tool_errors = int(trace.summary.get("tool_errors") or 0)
        if tool_errors:
            self._metric(trace, "qubit.tool.errors", tool_errors, "count")
        retries = _usage(trace, "qubit.retries")
        if retries:
            self._metric(trace, "qubit.retries", retries, "count")
        if trace.summary.get("unknown_outcome"):
            self._metric(trace, "qubit.unknown.outcomes", 1, "count")

    def _metric(self, trace: RunTrace, name: str, value: Any, unit: str) -> None:
        self.exporter.record_metric(
            {
                "name": name,
                "value": value,
                "unit": unit,
                "kind": "histogram",
                "time": trace.root.end_time or trace.root.start_time,
                "trace_id": trace.trace_id,
                "span_id": trace.root.span_id,
                "attributes": {},
            },
            redactor=self._redactor,
        )


def _usage(trace: RunTrace, key: str) -> float | None:
    root_value = _number(trace.root.attributes.get(key))
    if root_value is not None:
        return root_value
    total = 0.0
    seen = False
    for span in trace.spans:
        value = _number(span.attributes.get(key))
        if value is not None:
            total += value
            seen = True
    return total if seen else None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
