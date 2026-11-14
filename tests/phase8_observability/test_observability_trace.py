"""P8-04 — one composed trace, metrics, eval linkage and no leakage."""
from __future__ import annotations

import json

from _p8_helpers import IDS, PROMPT, SECRET, make_observability


def test_full_chain_has_every_stage_and_id(tensor):
    OBS, otel, _ = tensor
    obs = make_observability(tensor)
    with obs.start(ids=IDS) as run:
        with run.run():
            with run.model():
                pass
            with run.tool():
                pass
            with run.approval():
                pass
            with run.receipt():
                pass
            with run.reconciliation():
                pass
    snapshot = obs.snapshot()
    spans = snapshot["spans"]
    assert len(snapshot["traces"]) == 1
    assert len({span.trace_id for span in spans}) == 1
    assert OBS.trace_stages(spans) == list(OBS.STAGES)
    by_stage = {
        stage: [
            span
            for span in spans
            if span.attributes.get("qubit.observability.stage") == stage
        ]
        for stage in OBS.STAGES
    }
    for stage in OBS.STAGES:
        assert len(by_stage[stage]) == 1
        assert by_stage[stage][0].name == OBS.SPAN_FOR_STAGE[stage]
    request = by_stage[OBS.STAGE_REQUEST][0]
    run_span = by_stage[OBS.STAGE_RUN][0]
    assert run_span.parent_span_id == request.span_id
    for stage in (
        OBS.STAGE_MODEL,
        OBS.STAGE_TOOL,
        OBS.STAGE_APPROVAL,
        OBS.STAGE_RECEIPT,
        OBS.STAGE_RECONCILIATION,
    ):
        assert by_stage[stage][0].parent_span_id == run_span.span_id
    for attribute in OBS.ID_ATTRIBUTES.values():
        assert request.attributes.get(attribute) not in (None, "")


def test_metrics_carry_usage_tool_error_retry_queue_and_unknown(tensor):
    OBS, otel, _ = tensor
    obs = make_observability(tensor)
    with obs.start(ids=IDS) as run:
        with run.run():
            with run.model() as model_span:
                run.evaluate(
                    model_span, "contains_answer", score=1.0, passed=True
                )
            with run.tool() as tool_span:
                tool_span.set_status(otel.STATUS_ERROR)
            run.record_usage(
                input_tokens=120,
                output_tokens=40,
                cost_usd=0.003,
                retries=1,
                queue_age_ms=7.5,
            )
            run.mark_unknown()
    metrics = OBS.metrics_by_name(obs.snapshot())
    for name in OBS.REQUIRED_METRICS:
        assert name in metrics, name
    assert metrics[OBS.METRIC_INPUT_TOKENS][0]["value"] == 120
    assert metrics[OBS.METRIC_OUTPUT_TOKENS][0]["value"] == 40
    assert metrics[OBS.METRIC_COST][0]["value"] == 0.003
    assert metrics[OBS.METRIC_TOOL_ERRORS][0]["value"] == 1
    assert metrics[OBS.METRIC_RETRIES][0]["value"] == 1
    assert metrics[OBS.METRIC_QUEUE_AGE][0]["value"] == 7.5
    assert metrics[OBS.METRIC_UNKNOWN][0]["value"] == 1
    # One duration sample per span; the trace itself is not double counted.
    assert len(metrics[OBS.METRIC_DURATION]) == len(obs.snapshot()["spans"])


def test_eval_event_links_to_the_judged_span(tensor):
    OBS, otel, _ = tensor
    obs = make_observability(tensor)
    with obs.start(ids=IDS) as run:
        with run.run():
            with run.model() as model_span:
                evaluation = run.evaluate(
                    model_span,
                    "contains_answer",
                    score=0.9,
                    label="pass",
                    passed=True,
                )
    snapshot = obs.snapshot()
    model = [
        span
        for span in snapshot["spans"]
        if span.attributes.get("qubit.observability.stage") == OBS.STAGE_MODEL
    ][0]
    events = [
        event
        for event in snapshot["events"]
        if event["name"] == otel.GEN_AI_EVAL_EVENT
    ]
    assert events
    assert events[-1]["span_id"] == model.span_id
    assert (
        events[-1]["attributes"]["qubit.judged.span_id"] == model.span_id
    )
    assert evaluation.passed is True
    assert obs.eval_gate.report()["blocked"] is False


def test_content_capture_is_off_and_never_exports_prompt_or_pii(tensor):
    OBS, otel, _ = tensor
    obs = make_observability(tensor)
    with obs.start(ids=IDS) as run:
        with run.run():
            with run.model() as span:
                span.record_content("prompt", PROMPT)
                span.record_content("completion", "completion text")
                span.set_attribute("api_key", "sk-verysecret123456")
                span.set_attribute("authorization", "Bearer aaa.bbb.ccc")
                span.set_attribute("note", f"exact {SECRET}")
                span.set_attribute("owner_email", "owner@example.com")
                span.set_attribute("owner_phone", "+40 721 123 456")
    snapshot = obs.snapshot()
    spans = snapshot["spans"]
    assert all(span.content == {} for span in spans)
    assert not any(
        span.attributes.get("gen_ai.content.captured") for span in spans
    )
    model = [
        span
        for span in spans
        if span.attributes.get("qubit.observability.stage") == OBS.STAGE_MODEL
    ][0]
    assert model.attributes["api_key"] == otel.REDACTED
    assert model.attributes["authorization"] == otel.REDACTED
    assert SECRET not in json.dumps(model.attributes)
    assert "owner@example.com" not in json.dumps(model.attributes)
    assert "+40 721 123 456" not in json.dumps(model.attributes)
    assert OBS.leak_markers_found(snapshot) == []
    assert PROMPT not in OBS.serialized_export(snapshot)


def test_explicit_content_capture_is_redacted_before_export(tensor):
    OBS, otel, _ = tensor
    obs = make_observability(tensor, capture_content=True)
    with obs.start(ids=IDS) as run:
        with run.run():
            with run.model() as span:
                span.record_content("prompt", f"secret {SECRET}")
    model = [
        span
        for span in obs.snapshot()["spans"]
        if span.attributes.get("qubit.observability.stage") == OBS.STAGE_MODEL
    ][0]
    assert model.content
    assert SECRET not in json.dumps(model.content)
    assert otel.REDACTED in json.dumps(model.content)


def test_clean_run_is_sampled_by_the_baseline_and_errors_are_kept(tensor):
    OBS, otel, _ = tensor

    def build(baseline: float):
        return make_observability(
            tensor,
            sampling=otel.SamplingPolicy(baseline_rate=baseline),
        )

    clean = build(0.0)
    with clean.start(ids=IDS) as run:
        with run.run():
            pass
    assert clean.snapshot()["traces"] == []
    assert clean.snapshot()["dropped"] == 1

    error = build(0.0)
    try:
        with error.start(ids=IDS) as run:
            with run.run():
                raise RuntimeError("probe")
    except RuntimeError:
        pass
    assert error.snapshot()["traces"][0].sampling_reason == "error"

    timeout = build(0.0)
    with timeout.start(ids=IDS) as run:
        with run.model() as span:
            span.set_status(otel.STATUS_TIMEOUT)
    assert timeout.snapshot()["traces"][0].sampling_reason == "timeout"

    handoff = build(0.0)
    with handoff.start(ids=IDS) as run:
        run.mark_handoff(reason="probe")
    assert handoff.snapshot()["traces"][0].sampling_reason == "handoff"

    failed_eval = build(0.0)
    with failed_eval.start(ids=IDS) as run:
        with run.model() as span:
            run.evaluate(span, "quality", score=0.1, passed=False)
    assert (
        failed_eval.snapshot()["traces"][0].sampling_reason
        == "failed_eval"
    )

    outlier = build(0.0)
    with outlier.start(ids=IDS) as run:
        run.mark_outlier(reason="slow")
    assert outlier.snapshot()["traces"][0].sampling_reason == "outlier"

    baseline = build(1.0)
    with baseline.start(ids=IDS) as run:
        with run.run():
            pass
    assert baseline.snapshot()["traces"][0].sampling_reason == "baseline"
