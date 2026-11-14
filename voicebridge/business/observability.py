"""P8-04 — one composable GenAI run trace, evaluation gate and QA matrices.

This module is a **composition layer** over :mod:`voicebridge.business.otel`.
It adds the pieces P8-04 asks for that the P1-08 instrumentation does not own:

* one canonical run chain — ``request -> run (invoke_agent) -> model -> tool ->
  approval -> receipt -> reconciliation`` — with every id the checklist names;
* a metrics view (duration, tokens, cost, tool errors, retries, queue age,
  unknown outcomes) computed from the same trace, never from a second store;
* eval results linked to the judged span plus a baseline gate whose regression
  **blocks release**;
* accessibility, performance/device and context-loss/recovery matrices that
  consume the deterministic instruments other lanes already shipped.

Honesty rules, stated as code:

* content capture stays **off** by default; the underlying tracer never records
  prompt/completion text unless the caller explicitly enables it;
* every exported attribute goes through the existing OTel redactor, so a secret
  or PII value cannot leave the process;
* a matrix never upgrades ``not_measured`` to a pass. The accessibility matrix
  requires ``axe: not_available`` and manual reviews reported as
  ``not_performed``/``unit_proven``; the performance matrix keeps GPU frame and
  heap numbers ``not_measured`` unless a real sample was supplied;
* no network, no live service, no browser is started here.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, Sequence

from . import otel

SCHEMA = "qubit-office-observability/1"
MATRIX_SCHEMA = "qubit-office-observability-matrix/1"

#: Stage labels, in causal order.
STAGE_REQUEST = "request"
STAGE_RUN = "run"
STAGE_MODEL = "model"
STAGE_TOOL = "tool"
STAGE_APPROVAL = "approval"
STAGE_RECEIPT = "receipt"
STAGE_RECONCILIATION = "reconciliation"
STAGES = (
    STAGE_REQUEST,
    STAGE_RUN,
    STAGE_MODEL,
    STAGE_TOOL,
    STAGE_APPROVAL,
    STAGE_RECEIPT,
    STAGE_RECONCILIATION,
)

#: Stage label -> OTel span name. ``run`` is the GenAI ``invoke_agent`` span;
#: ``model`` is ``chat``. The label is what the checklist names, the span name
#: is what a GenAI backend understands.
SPAN_FOR_STAGE: Mapping[str, str] = {
    STAGE_REQUEST: STAGE_REQUEST,
    STAGE_RUN: otel.SPAN_INVOKE_AGENT,
    STAGE_MODEL: otel.SPAN_CHAT,
    STAGE_TOOL: otel.SPAN_EXECUTE_TOOL,
    STAGE_APPROVAL: otel.SPAN_APPROVAL,
    STAGE_RECEIPT: otel.SPAN_EXTERNAL_RECEIPT,
    STAGE_RECONCILIATION: otel.SPAN_RECONCILIATION,
}

#: Every id the mandatory checklist names. ``trace`` is carried by the trace
#: itself; the rest are root-span attributes.
ID_ATTRIBUTES: Mapping[str, str] = {
    "tenant": "qubit.tenant.id",
    "owner": "qubit.owner.id",
    "agent": "qubit.agent.id",
    "run": "qubit.run.id",
    "task": "qubit.task.id",
    "session": "qubit.session.id",
    "provider_request": "qubit.provider.request.id",
    "artifact": "qubit.artifact.id",
    "approval": "qubit.approval.id",
}
REQUIRED_IDS = tuple(ID_ATTRIBUTES)

#: Metric names this module reports. Token/cost metrics are only emitted when
#: the run actually carried a value; an absent metric is absent, never zero.
METRIC_DURATION = "gen_ai.client.operation.duration"
METRIC_INPUT_TOKENS = "gen_ai.usage.input_tokens"
METRIC_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
METRIC_COST = "qubit.cost.usd"
METRIC_TOOL_ERRORS = "qubit.tool.errors"
METRIC_RETRIES = "qubit.retries"
METRIC_QUEUE_AGE = "qubit.queue.age_ms"
METRIC_UNKNOWN = "qubit.unknown.outcomes"
REQUIRED_METRICS = (
    METRIC_DURATION,
    METRIC_INPUT_TOKENS,
    METRIC_OUTPUT_TOKENS,
    METRIC_COST,
    METRIC_TOOL_ERRORS,
    METRIC_RETRIES,
    METRIC_QUEUE_AGE,
    METRIC_UNKNOWN,
)

#: PII and secret shapes used by the independent leakage check.
_LEAK_MARKERS = (
    "owner@example.com",
    "+40 721 123 456",
    "sk-verysecret123456",
    "exact-vault-value-77a1",
)

CONTENT_CAPTURE_DISABLED = otel.CONTENT_NOT_CAPTURED


class ObservabilityError(Exception):
    """A typed refusal from the composition layer."""

    code = "QOFFICE_OBSERVABILITY_INVALID"

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


# --------------------------------------------------------------------------
# evaluation gate
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class EvalBaseline:
    """The release baseline for one evaluation.

    ``min_pass_rate`` is the fraction of observations that must pass;
    ``min_score`` is the floor for the mean score. A baseline with neither still
    requires the evaluation to have run, so a missing eval cannot pass silently.
    """

    name: str
    min_pass_rate: float = 1.0
    min_score: float | None = None
    required: bool = True

    def __post_init__(self) -> None:
        rate = float(self.min_pass_rate)
        if not 0.0 <= rate <= 1.0:
            raise ObservabilityError("min_pass_rate must be between 0 and 1")
        object.__setattr__(self, "min_pass_rate", rate)
        if self.min_score is not None:
            object.__setattr__(self, "min_score", float(self.min_score))


@dataclass
class EvalObservation:
    name: str
    score: float | None
    passed: bool | None
    label: str | None
    span_id: str
    span_name: str


class EvaluationGate:
    """Collect eval observations and decide whether a release may proceed."""

    def __init__(self, baselines: Iterable[EvalBaseline] = ()) -> None:
        self.baselines: dict[str, EvalBaseline] = {}
        for baseline in baselines:
            self.baselines[baseline.name] = baseline
        self.observations: list[EvalObservation] = []

    def record(self, evaluation: otel.Evaluation, *, span: otel.Span) -> None:
        self.observations.append(
            EvalObservation(
                name=evaluation.name,
                score=evaluation.score,
                passed=evaluation.passed,
                label=evaluation.label,
                span_id=span.span_id,
                span_name=span.name,
            )
        )

    def report(self) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        blocked_reasons: list[str] = []
        for name in sorted(self.baselines):
            baseline = self.baselines[name]
            observed = [item for item in self.observations if item.name == name]
            row: dict[str, Any] = {
                "name": name,
                "observations": len(observed),
                "required": baseline.required,
                "min_pass_rate": baseline.min_pass_rate,
                "min_score": baseline.min_score,
                "status": "pass",
                "reasons": [],
            }
            if not observed:
                if baseline.required:
                    row["status"] = "fail"
                    row["reasons"].append("required evaluation did not run")
                else:
                    row["status"] = "missing"
                rows.append(row)
                if row["status"] == "fail":
                    blocked_reasons.append(
                        f"{name}: required evaluation did not run"
                    )
                continue
            scored = [item for item in observed if item.passed is not None]
            pass_rate = (
                sum(1 for item in scored if item.passed) / len(scored)
                if scored
                else None
            )
            scores = [
                float(item.score)
                for item in observed
                if isinstance(item.score, (int, float))
            ]
            mean_score = sum(scores) / len(scores) if scores else None
            row["pass_rate"] = pass_rate
            row["mean_score"] = mean_score
            if pass_rate is None:
                row["reasons"].append("no boolean verdict was recorded")
            elif pass_rate < baseline.min_pass_rate:
                row["reasons"].append(
                    f"pass rate {pass_rate:.3f} < {baseline.min_pass_rate:.3f}"
                )
            if baseline.min_score is not None:
                if mean_score is None:
                    row["reasons"].append("baseline needs a score but none was recorded")
                elif mean_score < baseline.min_score:
                    row["reasons"].append(
                        f"mean score {mean_score:.3f} < {baseline.min_score:.3f}"
                    )
            if row["reasons"]:
                row["status"] = "fail"
                blocked_reasons.extend(f"{name}: {reason}" for reason in row["reasons"])
            rows.append(row)
        return {
            "schema": MATRIX_SCHEMA,
            "rows": rows,
            "blocked": bool(blocked_reasons),
            "reasons": blocked_reasons,
        }

    def release_blocked(self) -> bool:
        return bool(self.report()["blocked"])

    def release_reasons(self) -> list[str]:
        return list(self.report()["reasons"])


# --------------------------------------------------------------------------
# run composition
# --------------------------------------------------------------------------
class RunObservation:
    """The live view of one run trace."""

    def __init__(self, trace: otel.RunTrace, gate: EvaluationGate) -> None:
        self.trace = trace
        self.gate = gate
        self._stage_index = 0

    @property
    def request_span(self) -> otel.Span:
        return self.trace.root

    @property
    def ids(self) -> dict[str, Any]:
        identifiers: dict[str, Any] = {"trace": self.trace.trace_id}
        for name, attribute in ID_ATTRIBUTES.items():
            identifiers[name] = self.trace.root.attributes.get(attribute)
        return identifiers

    def missing_ids(self) -> list[str]:
        return [
            name
            for name, attribute in ID_ATTRIBUTES.items()
            if self.trace.root.attributes.get(attribute) in (None, "")
        ]

    @contextmanager
    def stage(
        self,
        name: str,
        *,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[otel.Span]:
        """Open one canonical stage beneath the current stage."""
        if name not in SPAN_FOR_STAGE:
            raise ObservabilityError(
                f"unknown stage {name!r}; expected one of {', '.join(STAGES)}"
            )
        attrs: dict[str, Any] = {
            "qubit.observability.stage": name,
            "qubit.stage.sequence": self._stage_index,
        }
        attrs.update(attributes or {})
        self._stage_index += 1
        with self.trace.span(SPAN_FOR_STAGE[name], attributes=attrs) as span:
            span.set_attribute("gen_ai.operation.name", SPAN_FOR_STAGE[name])
            yield span

    def _convenience(
        self,
        name: str,
        attributes: Mapping[str, Any] | None,
        extra: Mapping[str, Any],
    ) -> Any:
        merged = dict(attributes or {})
        merged.update(extra)
        return self.stage(name, attributes=merged)

    def request(
        self,
        *,
        attributes: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> Any:
        return self._convenience(STAGE_REQUEST, attributes, extra)

    def run(
        self,
        *,
        attributes: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> Any:
        return self._convenience(STAGE_RUN, attributes, extra)

    def model(
        self,
        *,
        attributes: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> Any:
        return self._convenience(STAGE_MODEL, attributes, extra)

    def tool(
        self,
        *,
        attributes: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> Any:
        return self._convenience(STAGE_TOOL, attributes, extra)

    def approval(
        self,
        *,
        attributes: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> Any:
        return self._convenience(STAGE_APPROVAL, attributes, extra)

    def receipt(
        self,
        *,
        attributes: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> Any:
        return self._convenience(STAGE_RECEIPT, attributes, extra)

    def reconciliation(
        self,
        *,
        attributes: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> Any:
        return self._convenience(STAGE_RECONCILIATION, attributes, extra)

    def record_usage(
        self,
        *,
        input_tokens: int | float | None = None,
        output_tokens: int | float | None = None,
        cost_usd: float | None = None,
        retries: int | None = None,
        queue_age_ms: float | None = None,
    ) -> None:
        """Attach the run's measured usage to the root span.

        Only supplied values are written; an absent usage is absent.
        """
        if input_tokens is not None:
            self.trace.root.set_attribute(
                "gen_ai.usage.input_tokens", float(input_tokens)
            )
        if output_tokens is not None:
            self.trace.root.set_attribute(
                "gen_ai.usage.output_tokens", float(output_tokens)
            )
        if cost_usd is not None:
            self.trace.root.set_attribute("qubit.cost.usd", float(cost_usd))
        if retries is not None:
            self.trace.root.set_attribute("qubit.retries", int(retries))
        if queue_age_ms is not None:
            self.trace.root.set_attribute("qubit.queue.age_ms", float(queue_age_ms))

    def mark_handoff(self, *, reason: str = "") -> None:
        self.trace.mark_handoff(reason=reason)

    def mark_unknown(self, *, outcome: str = "unknown") -> None:
        self.trace.mark_unknown(outcome=outcome)

    def mark_outlier(self, *, reason: str = "") -> None:
        self.trace.mark_outlier(reason=reason)

    def evaluate(
        self,
        judged_span: otel.Span | None,
        name: str,
        *,
        score: float | None = None,
        label: str | None = None,
        passed: bool | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> otel.Evaluation:
        evaluation = self.trace.evaluate(
            judged_span,
            name,
            score=score,
            label=label,
            passed=passed,
            attributes=attributes,
        )
        span = judged_span or self.trace.root
        self.gate.record(evaluation, span=span)
        return evaluation


class Observability:
    """Compose run traces and hold the evaluation gate for the process."""

    def __init__(
        self,
        *,
        exporter: Any | None = None,
        capture_content: bool = False,
        sampling: otel.SamplingPolicy | None = None,
        clock: Any | None = None,
        id_factory: Any | None = None,
        redactor: Any | None = None,
        eval_baselines: Iterable[EvalBaseline] = (),
    ) -> None:
        self.exporter = exporter if exporter is not None else otel.InMemoryExporter()
        self.capture_content = bool(capture_content)
        self.tracer = otel.Tracer(
            exporter=self.exporter,
            capture_content=self.capture_content,
            sampling=sampling,
            clock=clock,
            id_factory=id_factory,
            redactor=redactor,
        )
        self.eval_gate = EvaluationGate(eval_baselines)

    @contextmanager
    def start(
        self,
        *,
        ids: Mapping[str, Any] | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[RunObservation]:
        """Start one trace. The root span is the ``request``; ``run`` is its
        ``invoke_agent`` child, followed by the remaining canonical stages."""
        supplied = dict(ids or {})
        unknown = sorted(set(supplied) - set(REQUIRED_IDS))
        if unknown:
            raise ObservabilityError(
                "unknown id(s): " + ", ".join(unknown)
            )
        attrs = dict(attributes or {})
        attrs.setdefault("gen_ai.operation.name", otel.SPAN_INVOKE_AGENT)
        attrs.setdefault("qubit.observability.chain", list(STAGES))
        attrs.setdefault("qubit.observability.stage", STAGE_REQUEST)
        attrs.setdefault("qubit.stage.sequence", 0)
        with self.tracer.start_run(
            name=STAGE_REQUEST,
            attributes=attrs,
            tenant_id=supplied.get("tenant"),
            owner_id=supplied.get("owner"),
            agent_id=supplied.get("agent"),
            run_id=supplied.get("run"),
            task_id=supplied.get("task"),
            session_id=supplied.get("session"),
            provider_request_id=supplied.get("provider_request"),
            artifact_id=supplied.get("artifact"),
            approval_id=supplied.get("approval"),
        ) as trace:
            yield RunObservation(trace, self.eval_gate)

    def snapshot(self) -> dict[str, Any]:
        exporter = self.exporter
        if hasattr(exporter, "snapshot"):
            return exporter.snapshot()
        return {
            "traces": [],
            "spans": [],
            "metrics": [],
            "events": [],
            "dropped": 0,
        }


# --------------------------------------------------------------------------
# trace inspection
# --------------------------------------------------------------------------
def trace_stages(spans: Sequence[otel.Span]) -> list[str]:
    """Stage labels present in an exported span list, in span order."""
    return [
        str(span.attributes.get("qubit.observability.stage"))
        for span in spans
        if span.attributes.get("qubit.observability.stage") in STAGES
    ]


def metrics_by_name(
    snapshot: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {}
    for sample in snapshot.get("metrics") or []:
        rows.setdefault(str(sample.get("name")), []).append(sample)
    return rows


def _span_export(span: otel.Span) -> dict[str, Any]:
    return {
        "name": span.name,
        "trace_id": span.trace_id,
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "status": span.status,
        "attributes": dict(span.attributes),
        "events": list(span.events),
        "content": dict(span.content),
        "evaluations": [
            {
                "name": item.name,
                "score": item.score,
                "label": item.label,
                "passed": item.passed,
                "attributes": dict(item.attributes),
            }
            for item in span.evaluations
        ],
    }


def export_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """A cycle-free view of an export, safe to serialise.

    ``RunTrace`` holds a back-reference to its tracer (and thus the exporter);
    a naive ``json.dumps(snapshot)`` would recurse. This view carries only the
    fields a leakage check or an evidence record needs.
    """
    traces = []
    for trace in snapshot.get("traces") or []:
        traces.append(
            {
                "trace_id": getattr(trace, "trace_id", None),
                "sampling_reason": getattr(trace, "sampling_reason", None),
                "sampled": getattr(trace, "sampled", None),
                "summary": getattr(trace, "summary", {}),
                "metadata": getattr(trace, "metadata", {}),
            }
        )
    return {
        "traces": traces,
        "spans": [
            _span_export(span) for span in snapshot.get("spans") or []
        ],
        "metrics": list(snapshot.get("metrics") or []),
        "events": list(snapshot.get("events") or []),
        "dropped": snapshot.get("dropped", 0),
    }


def serialized_export(snapshot: Mapping[str, Any]) -> str:
    """A deterministic serialisation of an export, used by leakage checks."""
    return json.dumps(export_payload(snapshot), sort_keys=True, default=str)


def leak_markers_found(snapshot: Mapping[str, Any]) -> list[str]:
    """Return the PII/secret shapes that survived into the export."""
    blob = serialized_export(snapshot)
    return [marker for marker in _LEAK_MARKERS if marker in blob]


# --------------------------------------------------------------------------
# accessibility matrix
# --------------------------------------------------------------------------
_MANUAL_ITEMS = (
    "axe-core automated scan",
    "keyboard-only traversal",
    "NVDA / VoiceOver / TalkBack",
    "high-contrast mode",
    "200% zoom / reflow",
    "touch",
    "muted audio",
    "reduced motion",
    "screen-reader loading/error states",
)
_ALLOWED_MANUAL = frozenset({"not_performed", "unit_proven"})


def accessibility_matrix(report: Mapping[str, Any]) -> dict[str, Any]:
    """Consume the deterministic P6-05 audit report without inflating it.

    The audit is a static source/CSS check. This matrix passes only when that
    check is clean **and** every claim that was not actually performed is still
    labelled as such.
    """
    axe = dict(report.get("axe") or {})
    summary = dict(report.get("summary") or {})
    findings = list(report.get("findings") or [])
    manual = [
        dict(item) for item in (report.get("manual_review") or [])
    ]
    errors = int(summary.get("errors") or 0)

    axe_honest = (
        axe.get("status") == "not_available"
        and axe.get("claimed_run") is False
    )
    static_ok = bool(report.get("ok") is True) and errors == 0
    named_manual = {str(item.get("item")): str(item.get("status")) for item in manual}
    manual_honest = all(
        status in _ALLOWED_MANUAL for status in named_manual.values()
    )
    missing_manual = [item for item in _MANUAL_ITEMS if item not in named_manual]

    checks: list[dict[str, Any]] = []
    checks.append(
        _check(
            "static_source_audit",
            static_ok,
            f"{errors} error(s), {len(findings)} finding(s) from the deterministic audit",
            measured=True,
        )
    )
    checks.append(
        _check(
            "axe_run_honesty",
            axe_honest,
            "axe is not installed and no axe run was performed"
            if axe_honest
            else f"audit claimed axe status={axe.get('status')!r} "
            f"claimed_run={axe.get('claimed_run')!r}",
            measured=True,
        )
    )
    checks.append(
        _check(
            "manual_review_honesty",
            not missing_manual and manual_honest,
            "manual reviews are labelled not_performed/unit_proven"
            if not missing_manual and manual_honest
            else "missing or over-claimed manual review "
            + ", ".join(missing_manual or [
                name for name, status in named_manual.items()
                if status not in _ALLOWED_MANUAL
            ]),
            measured=True,
        )
    )

    status_by_name = {
        name: status for name, status in named_manual.items()
    }
    reviewed = [
        {
            "item": name,
            "status": status_by_name.get(name, "not_performed"),
            "measured": status_by_name.get(name) == "unit_proven",
        }
        for name in _MANUAL_ITEMS
    ]
    return {
        "schema": MATRIX_SCHEMA,
        "kind": "accessibility",
        "ok": all(item["ok"] for item in checks),
        "source": "scripts/accessibility_audit.py (P6-05 static audit)",
        "static_scope": [
            "interactive_name",
            "shell_structure",
            "semantic_mirror",
            "outline_replacement",
            "reduced_motion",
            "target_size",
            "non_color_status",
            "focus_visible",
            "skip_link_style",
        ],
        "axe": {
            "status": axe.get("status"),
            "claimed_run": axe.get("claimed_run"),
            "honest": axe_honest,
        },
        "static": {
            "status": report.get("status"),
            "errors": errors,
            "warnings": int(summary.get("warnings") or 0),
            "checks": list(summary.get("checks") or []),
        },
        "reviews": reviewed,
        "checks": checks,
        "limitations": list(report.get("limitations") or []) + [
            "this matrix consumes the static audit; it never converts a"
            " not_performed browser/screen-reader review into a pass",
        ],
    }


# --------------------------------------------------------------------------
# performance / device / context-loss matrices
# --------------------------------------------------------------------------
def performance_matrix(
    budget_report: Mapping[str, Any],
    *,
    device_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Consume the real renderer data-path report, keep GPU/heap honest."""
    source = "scripts/verify_renderer_budget.py"
    data_path = dict(budget_report.get("data_path") or {})
    projection = dict(data_path.get("floor_projection") or {})
    budgets = [dict(row) for row in (budget_report.get("budgets") or [])]
    sample = dict(budget_report.get("budget_sample") or {})
    supplied = sample.get("supplied") is True
    rows: list[dict[str, Any]] = []
    fabricated: list[str] = []
    for row in budgets:
        status = row.get("status")
        value = row.get("value")
        metric = str(row.get("metric"))
        if status == "not_measured":
            if value is not None:
                fabricated.append(f"{metric}: not_measured with a value")
        else:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                fabricated.append(f"{metric}: measured without a numeric value")
        if not supplied and metric in ("frame_p95_ms", "heap_mb") and status != "not_measured":
            fabricated.append(f"{metric}: claimed without a supplied sample")
        rows.append(
            {
                "metric": metric,
                "value": value,
                "limit": row.get("limit"),
                "status": status,
                "ok": row.get("ok"),
                "source": row.get("source"),
            }
        )
    data_path_measured = (
        isinstance(projection.get("p95_ms"), (int, float))
        and projection.get("iterations", 0) >= 1
        and not isinstance(projection.get("p95_ms"), bool)
    )
    gpu_rows = [
        row for row in rows if row["metric"] in ("frame_p95_ms", "heap_mb")
    ]
    gpu_not_measured = [
        row["metric"] for row in gpu_rows if row["status"] == "not_measured"
    ]
    missing_gpu = [
        row["metric"] for row in gpu_rows if row["status"] != "not_measured"
    ] if not supplied else []
    tiers = _device_rows(device_contract)
    checks = [
        _check(
            "real_data_path_measured",
            bool(budget_report.get("ok") is True) and data_path_measured,
            f"floor projection p95={projection.get('p95_ms')} ms over "
            f"{projection.get('iterations')} run(s) from {source}",
            measured=True,
        ),
        _check(
            "gpu_and_heap_honest",
            not fabricated and not missing_gpu,
            "frame/heap are not_measured with null values"
            if not supplied
            else "a supplied sample carries the frame/heap measurements",
            measured=True,
        ),
        _check(
            "no_fabricated_measurement",
            not fabricated,
            "; ".join(fabricated) or "no measured null or unmeasured value",
            measured=True,
        ),
        _check(
            "device_tiers",
            bool(tiers["ok"]),
            tiers["detail"],
            measured=bool(device_contract),
        ),
    ]
    return {
        "schema": MATRIX_SCHEMA,
        "kind": "performance",
        "ok": all(item["ok"] for item in checks),
        "source": source,
        "tier": budget_report.get("tier"),
        "data_path": {
            "p95_ms": projection.get("p95_ms"),
            "median_ms": projection.get("median_ms"),
            "iterations": projection.get("iterations"),
            "measured": data_path_measured,
        },
        "budgets": rows,
        "sample_supplied": supplied,
        "gpu_not_measured": gpu_not_measured,
        "device_tiers": tiers["rows"],
        "checks": checks,
        "limitations": list(budget_report.get("limitations") or []),
    }


def _device_rows(contract: Mapping[str, Any] | None) -> dict[str, Any]:
    if not contract or not contract.get("ok"):
        return {
            "ok": False,
            "detail": "device tiers were not exercised by the renderer contract",
            "rows": [],
        }
    rows = [dict(row) for row in (contract.get("devices") or [])]
    expected = {"desktop", "low"}
    observed = {row.get("tier") for row in rows}
    ok = expected <= observed and bool(contract.get("ok"))
    return {
        "ok": ok,
        "detail": (
            "desktop and low tiers resolved by the shipped TypeScript contract"
            if ok
            else f"tiers observed: {sorted(item for item in observed if item)}"
        ),
        "rows": rows,
    }


_CONTEXT_EXPECTATIONS: Mapping[str, Sequence[str]] = {
    "recover": ("ready", "lost", "recovering", "ready"),
    "fallback": ("ready", "lost", "recovering", "fallback"),
    "no_fallback": ("ready", "lost", "recovering", "lost"),
}


def context_loss_matrix(contract: Mapping[str, Any] | None) -> dict[str, Any]:
    """Check the states the shipped renderer contract actually produced."""
    if not contract or not contract.get("ok"):
        return {
            "schema": MATRIX_SCHEMA,
            "kind": "context_loss",
            "ok": False,
            "checks": [
                _check(
                    "context_loss_exercised",
                    False,
                    str(
                        (contract or {}).get("reason")
                        or "the renderer contract probe did not run"
                    ),
                )
            ],
            "sequences": [],
            "limitations": [
                "context-loss/recovery was not exercised; no state was invented"
            ],
        }
    sequences = [dict(item) for item in (contract.get("sequences") or [])]
    by_name = {str(item.get("name")): item for item in sequences}
    checks: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for name, expected in _CONTEXT_EXPECTATIONS.items():
        item = by_name.get(name) or {}
        observed = tuple(item.get("states") or ())
        ok = observed == tuple(expected)
        rows.append(
            {
                "name": name,
                "expected": list(expected),
                "observed": list(observed),
                "modes": list(item.get("modes") or []),
                "changed": list(item.get("changed") or []),
                "ok": ok,
            }
        )
        checks.append(
            _check(
                f"context_loss_{name}",
                ok,
                f"observed {list(observed)}",
            )
        )
    invalid = dict(contract.get("invalid") or {})
    invalid_ok = invalid.get("changed") is False and bool(invalid.get("ignored"))
    rows.append(
        {
            "name": "invalid_event",
            "expected": ["unchanged", "ignored"],
            "observed": [
                "unchanged" if invalid.get("changed") is False else "changed",
                "ignored" if invalid.get("ignored") else "applied",
            ],
            "modes": [],
            "changed": [invalid.get("changed")],
            "ok": invalid_ok,
        }
    )
    checks.append(
        _check(
            "context_loss_invalid_event_ignored",
            invalid_ok,
            f"ignored={invalid.get('ignored')!r} state={invalid.get('state')!r}",
        )
    )
    return {
        "schema": MATRIX_SCHEMA,
        "kind": "context_loss",
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
        "sequences": rows,
        "limitations": [
            "context-loss is exercised through the shipped TypeScript renderer"
            " contract by node; no real WebGL context was lost",
        ],
    }


def _check(name: str, ok: bool, detail: str, *, measured: bool = True) -> dict[str, Any]:
    return {
        "name": name,
        "ok": bool(ok),
        "detail": str(detail),
        "measured": bool(measured),
    }


def matrices_report(
    *,
    accessibility: Mapping[str, Any],
    performance: Mapping[str, Any],
    context_loss: Mapping[str, Any],
) -> dict[str, Any]:
    """One combined matrix result for the operator/evidence surface."""
    checks = (
        list(accessibility.get("checks") or [])
        + list(performance.get("checks") or [])
        + list(context_loss.get("checks") or [])
    )
    return {
        "schema": MATRIX_SCHEMA,
        "ok": all(item.get("ok") for item in checks),
        "checks": checks,
        "accessibility": dict(accessibility),
        "performance": dict(performance),
        "context_loss": dict(context_loss),
    }
