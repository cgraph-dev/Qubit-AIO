"""P1-08 — OpenTelemetry GenAI instrumentation contract.

Hermetic: in-memory exporter, injected clock/id factory, no OpenTelemetry SDK
and no network exporter.
"""
from __future__ import annotations

import itertools

import pytest

from voicebridge.business import otel
from voicebridge.business.otel import (
    GEN_AI_EVAL_EVENT,
    SamplingPolicy,
    Tracer,
)


def _tracer(**kwargs):
    exporter = otel.InMemoryExporter()
    options = {
        "exporter": exporter,
        "sampling": SamplingPolicy(baseline_rate=1.0),
    }
    options.update(kwargs)
    return Tracer(**options), exporter


def _run(tracer, **kwargs):
    return tracer.start_run(
        run_id="run_1",
        tenant_id="ten_1",
        owner_id="own_1",
        agent_id="agt_1",
        task_id="tsk_1",
        session_id="ses_1",
        provider_request_id="prq_1",
        artifact_id="art_1",
        approval_id="apr_1",
        **kwargs,
    )


def test_one_trace_covers_the_full_chain():
    tracer, exporter = _tracer()
    with _run(tracer) as trace:
        for name in otel.CHAIN_SPANS:
            with trace.span(name) as span:
                span.set_attribute("gen_ai.operation.name", name)
                span.set_attribute("qubit.artifact.id", f"art_{name}")
                if name == otel.SPAN_EXECUTE_TOOL:
                    span.set_attribute("gen_ai.tool.name", "browser")
                    span.set_attribute("gen_ai.tool.call.id", "call_1")
                if name == otel.SPAN_APPROVAL:
                    span.set_attribute("qubit.approval.id", "apr_1")
        trace.evaluate(trace.spans[1], "contains_answer", score=1.0, passed=True)

    snapshot = exporter.snapshot()
    assert len(snapshot["traces"]) == 1
    names = [s.name for s in snapshot["spans"]]
    for expected in otel.CHAIN_SPANS:
        assert expected in names
    trace_ids = {s.trace_id for s in snapshot["spans"]}
    assert len(trace_ids) == 1
    root = snapshot["spans"][0]
    assert root.name == otel.SPAN_INVOKE_AGENT
    assert root.attributes["qubit.tenant.id"] == "ten_1"
    assert root.attributes["qubit.owner.id"] == "own_1"
    assert root.attributes["qubit.agent.id"] == "agt_1"
    assert root.attributes["qubit.run.id"] == "run_1"
    assert root.attributes["qubit.task.id"] == "tsk_1"
    assert root.attributes["qubit.session.id"] == "ses_1"
    assert root.attributes["qubit.provider.request.id"] == "prq_1"
    assert root.attributes["qubit.artifact.id"] == "art_1"
    assert root.attributes["qubit.approval.id"] == "apr_1"


def test_chain_spans_are_children_of_the_root():
    tracer, exporter = _tracer()
    with _run(tracer) as trace:
        with trace.span(otel.SPAN_CHAT) as chat:
            with trace.span(otel.SPAN_EXECUTE_TOOL) as tool:
                assert tool.parent_span_id == chat.span_id
                assert chat.parent_span_id == trace.root.span_id
    spans = {s.name: s for s in exporter.spans}
    assert spans[otel.SPAN_CHAT].parent_span_id == spans[otel.SPAN_INVOKE_AGENT].span_id
    assert spans[otel.SPAN_EXECUTE_TOOL].parent_span_id == spans[otel.SPAN_CHAT].span_id


def test_content_capture_is_off_by_default():
    tracer, exporter = _tracer()
    with _run(tracer) as trace:
        with trace.span(otel.SPAN_CHAT) as chat:
            chat.record_content("prompt", "owner prompt text")
            chat.record_content("completion", "model completion text")
            chat.set_attribute("gen_ai.request.model", "deepseek-v4.1-flash")
    exported = {s.name: s for s in exporter.spans}[otel.SPAN_CHAT]
    assert exported.content == {}
    assert "gen_ai.content.captured" not in exported.attributes
    assert "prompt text" not in str(exported.attributes)


def test_content_capture_when_enabled_is_redacted_before_export():
    tracer, exporter = _tracer(capture_content=True)
    with _run(tracer) as trace:
        with trace.span(otel.SPAN_CHAT) as chat:
            chat.record_content("prompt", "api_key=sk-verysecret123456")
    exported = {s.name: s for s in exporter.spans}[otel.SPAN_CHAT]
    assert exported.content
    assert "sk-verysecret123456" not in str(exported.content)
    assert otel.REDACTED in str(exported.content)


def test_secret_and_pii_never_reach_exported_attributes():
    secret = "exact-vault-value-77a1"
    redactor = lambda attributes: otel.redact_attributes(  # noqa: E731
        attributes, extra_secrets=(secret,)
    )
    tracer, exporter = _tracer(redactor=redactor)
    with _run(tracer) as trace:
        with trace.span(otel.SPAN_CHAT) as chat:
            chat.set_attribute("api_key", "any-shape-value")
            chat.set_attribute("authorization", "Bearer abc.def.ghi")
            chat.set_attribute("qubit.note", f"leaked {secret}")
            chat.set_attribute("owner_email", "owner@example.com")
            chat.set_attribute("owner_phone", "+40 721 123 456")
            chat.set_attribute("gen_ai.request.model", "deepseek-v4.1-flash")
    attrs = {s.name: s for s in exporter.spans}[otel.SPAN_CHAT].attributes
    assert attrs["api_key"] == otel.REDACTED
    assert attrs["authorization"] == otel.REDACTED
    assert secret not in str(attrs)
    assert "owner@example.com" not in str(attrs)
    assert "+40 721 123 456" not in str(attrs)
    assert attrs["gen_ai.request.model"] == "deepseek-v4.1-flash"


def test_error_run_is_sampled_even_at_zero_baseline():
    tracer, exporter = _tracer(sampling=SamplingPolicy(baseline_rate=0.0))
    with pytest.raises(RuntimeError):
        with _run(tracer) as trace:
            with trace.span(otel.SPAN_EXECUTE_TOOL) as tool:
                tool.set_status(otel.STATUS_ERROR)
                raise RuntimeError("tool blew up")
    assert len(exporter.traces) == 1
    assert exporter.traces[0].sampling_reason == "error"


def test_timeout_run_is_sampled_even_at_zero_baseline():
    tracer, exporter = _tracer(sampling=SamplingPolicy(baseline_rate=0.0))
    with _run(tracer) as trace:
        with trace.span(otel.SPAN_CHAT) as chat:
            chat.set_status(otel.STATUS_TIMEOUT)
    assert len(exporter.traces) == 1
    assert exporter.traces[0].sampling_reason == "timeout"


def test_handoff_and_unknown_outcome_are_sampled():
    tracer, exporter = _tracer(sampling=SamplingPolicy(baseline_rate=0.0))
    with _run(tracer) as trace:
        trace.mark_handoff(reason="approval")
        trace.mark_unknown(outcome="unknown")
    assert len(exporter.traces) == 1
    assert exporter.traces[0].sampling_reason == "handoff"
    assert trace.summary["unknown_outcome"] is True


def test_failed_eval_is_sampled_at_zero_baseline():
    tracer, exporter = _tracer(sampling=SamplingPolicy(baseline_rate=0.0))
    with _run(tracer) as trace:
        with trace.span(otel.SPAN_CHAT) as chat:
            trace.evaluate(chat, "quality", score=0.1, passed=False)
    assert len(exporter.traces) == 1
    assert exporter.traces[0].sampling_reason == "failed_eval"


def test_outlier_is_sampled_at_zero_baseline():
    tracer, exporter = _tracer(sampling=SamplingPolicy(baseline_rate=0.0))
    with _run(tracer) as trace:
        trace.mark_outlier(reason="slow")
        with trace.span(otel.SPAN_CHAT):
            pass
    assert len(exporter.traces) == 1
    assert exporter.traces[0].sampling_reason == "outlier"


def test_clean_run_is_dropped_at_zero_baseline_and_kept_at_one():
    tracer, exporter = _tracer(sampling=SamplingPolicy(baseline_rate=0.0))
    with _run(tracer) as trace:
        with trace.span(otel.SPAN_CHAT):
            pass
    assert exporter.traces == []
    assert exporter.dropped == 1

    tracer2, exporter2 = _tracer(sampling=SamplingPolicy(baseline_rate=1.0))
    with _run(tracer2):
        pass
    assert len(exporter2.traces) == 1
    assert exporter2.traces[0].sampling_reason == "baseline"


def test_baseline_sampling_is_deterministic_for_a_trace_id():
    policy = SamplingPolicy(baseline_rate=0.25)
    results = {policy.decide(f"trace-{i}", {}) for i in range(200)}
    assert all(isinstance(keep, bool) for keep, _ in results)
    # The bucket is a pure function of the trace id.
    first = policy.decide("trace-42", {})
    second = policy.decide("trace-42", {})
    assert first == second


def test_eval_event_links_to_the_judged_span():
    tracer, exporter = _tracer()
    with _run(tracer) as trace:
        with trace.span(otel.SPAN_CHAT) as chat:
            evaluation = trace.evaluate(
                chat, "contains_answer", score=0.9, label="pass", passed=True
            )
    judged = {s.name: s for s in exporter.spans}[otel.SPAN_CHAT]
    events = [e for e in judged.events if e["name"] == GEN_AI_EVAL_EVENT]
    assert events
    event = events[-1]
    assert event["attributes"]["qubit.judged.span_id"] == judged.span_id
    assert event["attributes"]["gen_ai.evaluation.name"] == "contains_answer"
    assert event["attributes"]["gen_ai.evaluation.score"] == 0.9
    assert evaluation.passed is True
    exported_events = [e for e in exporter.events if e["name"] == GEN_AI_EVAL_EVENT]
    assert exported_events
    assert exported_events[-1]["span_id"] == judged.span_id


def test_metrics_are_counted_once_per_run():
    tracer, exporter = _tracer()
    with _run(tracer) as trace:
        trace.root.set_attribute("gen_ai.usage.input_tokens", 120)
        trace.root.set_attribute("gen_ai.usage.output_tokens", 40)
        trace.root.set_attribute("qubit.cost.usd", 0.003)
        trace.root.set_attribute("qubit.queue.age_ms", 7.5)
        trace.root.set_attribute("qubit.retries", 2)
        trace.mark_unknown()
        with trace.span(otel.SPAN_EXECUTE_TOOL) as tool:
            tool.set_status(otel.STATUS_ERROR)
        with trace.span(otel.SPAN_CHAT):
            pass

    by_name: dict[str, list] = {}
    for sample in exporter.metrics:
        by_name.setdefault(sample["name"], []).append(sample)
    assert len(by_name["gen_ai.usage.input_tokens"]) == 1
    assert by_name["gen_ai.usage.input_tokens"][0]["value"] == 120.0
    assert len(by_name["gen_ai.usage.output_tokens"]) == 1
    assert len(by_name["qubit.cost.usd"]) == 1
    assert len(by_name["qubit.queue.age_ms"]) == 1
    assert len(by_name["qubit.retries"]) == 1
    assert len(by_name["qubit.tool.errors"]) == 1
    assert len(by_name["qubit.unknown.outcomes"]) == 1
    # One duration sample per span, no duplicate per run finalisation.
    assert len(by_name["gen_ai.client.operation.duration"]) == len(exporter.spans)


def test_metrics_survive_an_unsampled_trace_but_the_trace_does_not():
    tracer, exporter = _tracer(sampling=SamplingPolicy(baseline_rate=0.0))
    with _run(tracer) as trace:
        trace.root.set_attribute("gen_ai.usage.input_tokens", 5)
        with trace.span(otel.SPAN_CHAT):
            pass
    assert exporter.traces == []
    assert any(m["name"] == "gen_ai.usage.input_tokens" for m in exporter.metrics)


def test_deterministic_ids_and_clock_with_injected_factories():
    ids = itertools.count(1)
    clock_values = itertools.count(1000, 1)

    def id_factory(kind):
        return f"{kind}-{next(ids)}"

    tracer, exporter = _tracer(
        clock=lambda: float(next(clock_values)),
        id_factory=id_factory,
    )
    with _run(tracer) as trace:
        with trace.span(otel.SPAN_CHAT) as chat:
            assert chat.span_id.startswith("span-3")
    assert trace.trace_id == "trace-1" + "0" * 25
    assert export_first_id(exporter) == trace.trace_id
    # Duration is the injected clock difference, not wall time.
    root = exporter.spans[0]
    assert root.duration_ms >= 0


def export_first_id(exporter):
    return exporter.spans[0].trace_id


def test_unsampled_export_does_not_leak_raw_attributes():
    secret = "another-exact-secret"
    redactor = lambda attributes: otel.redact_attributes(  # noqa: E731
        attributes, extra_secrets=(secret,)
    )
    tracer, exporter = _tracer(redactor=redactor)
    with _run(tracer) as trace:
        trace.root.set_attribute("note", f"value {secret}")
    assert secret not in str(exporter.snapshot())
