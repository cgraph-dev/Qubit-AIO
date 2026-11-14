#!/usr/bin/env python3
"""P8-04 — independent verification of observability/evals + QA matrices.

What this instrument checks, each with a real input:

* one composed GenAI run has the canonical
  ``request -> run -> model -> tool -> approval -> receipt -> reconciliation``
  stages, every required id, the named metrics and an eval event linked to the
  judged span;
* content capture is off and no prompt/secret/PII marker survives export;
* the evaluation gate blocks release on a regression or a missing required eval;
* sampling keeps errors/timeouts/handoffs/failed evals/outliers and a
  deterministic baseline;
* the accessibility matrix consumes the real deterministic P6-05 audit and
  refuses to upgrade ``axe: not_available`` or an unperformed manual review;
* the performance matrix consumes the real P3-04 renderer report (the shipped
  100-floor projection), keeps GPU frame/heap ``not_measured`` unless a real
  sample was supplied, and never accepts a fabricated value;
* the device tiers and the context-loss/recovery state machine are exercised by
  executing the shipped TypeScript renderer contract in node (no browser, no
  WebGL context is lost).

Exit codes: ``0`` all required checks pass, ``1`` a check failed, ``2``
usage/IO (a missing input or a malformed instrument result).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TASK_ID = "P8-04"
SCHEMA = "qubit-office-observability-verification/1"
PLUGIN_ROOT = ROOT / "frontend" / "qubit-app" / "plugins" / "qubit-office"
RENDERER_SCRIPT = ROOT / "scripts" / "verify_renderer_budget.py"
A11Y_SCRIPT = ROOT / "scripts" / "accessibility_audit.py"
FLOOR_TS = (
    PLUGIN_ROOT / "game" / "app" / "renderer-floor.ts"
)
BUDGET_TS = (
    PLUGIN_ROOT / "game" / "app" / "renderer-budget.ts"
)

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


class UsageError(Exception):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


# ---------------------------------------------------------------- utilities
def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)


def _check(name: str, ok: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": str(detail)}


def _load_script(name: str, path: Path) -> Any:
    import importlib.util

    if not path.is_file():
        raise UsageError("QOFFICE_OBSERVABILITY_INPUT_MISSING", str(path))
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise UsageError("QOFFICE_OBSERVABILITY_INPUT_INVALID", str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------- real renderer report
def renderer_report(timeout: int = 300) -> Dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env.setdefault("TMPDIR", "/tmp")
    proc = subprocess.run(
        [sys.executable, str(RENDERER_SCRIPT), "--json"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise UsageError(
            "QOFFICE_OBSERVABILITY_RENDERER_FAILED",
            (proc.stdout or proc.stderr or "renderer verifier failed")[-1200:],
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise UsageError(
            "QOFFICE_OBSERVABILITY_RENDERER_INVALID",
            f"renderer report is not JSON: {exc}",
        ) from exc


# ----------------------------------------- shipped TypeScript contract probe
_PROBE_JS = r"""
import {
  contextRenderMode,
  resolveDeviceTier,
  transitionContextLoss,
} from './renderer-floor.ts';

function sequence(name, start, steps) {
  let state = start;
  const states = [state];
  const modes = [contextRenderMode(state)];
  const changed = [];
  for (const step of steps) {
    const event = step[0];
    const options = step[1] || {};
    const result = transitionContextLoss(state, event, options);
    state = result.state;
    changed.push(result.changed);
    states.push(state);
    modes.push(contextRenderMode(state));
  }
  return { name, states, modes, changed };
}

const sequences = [
  sequence('recover', 'ready', [
    ['context_lost'],
    ['restore_started'],
    ['restore_succeeded'],
  ]),
  sequence('fallback', 'ready', [
    ['context_lost'],
    ['restore_started'],
    ['restore_failed'],
  ]),
  sequence('no_fallback', 'ready', [
    ['context_lost'],
    ['restore_started'],
    ['restore_failed', { fallbackAvailable: false }],
  ]),
];

const devices = [
  { name: 'default', ...resolveDeviceTier({}) },
  { name: 'save_data', ...resolveDeviceTier({ saveData: true }) },
  { name: 'low_memory', ...resolveDeviceTier({ deviceMemoryGb: 2 }) },
  { name: 'low_cores', ...resolveDeviceTier({ hardwareConcurrency: 2 }) },
  {
    name: 'capable',
    ...resolveDeviceTier({ deviceMemoryGb: 8, hardwareConcurrency: 8 }),
  },
];

const invalid = transitionContextLoss('ready', 'restore_succeeded');

console.log(JSON.stringify({ ok: true, sequences, devices, invalid }));
"""


def renderer_contract_probe(timeout: int = 60) -> Dict[str, Any]:
    """Execute the shipped TypeScript state machine and tier resolver.

    The two TS modules are copied to a private temp directory (the import
    specifier is made explicit for node's type stripping). Nothing in the repo
    is modified and no browser is opened.
    """
    node = shutil.which("node")
    if not node:
        return {
            "ok": False,
            "reason": "node is not available; the TypeScript renderer contract"
            " was not executed",
            "devices": [],
            "sequences": [],
            "invalid": {},
        }
    if not FLOOR_TS.is_file() or not BUDGET_TS.is_file():
        return {
            "ok": False,
            "reason": "the renderer contract sources are missing",
            "devices": [],
            "sequences": [],
            "invalid": {},
        }
    workdir = Path(tempfile.mkdtemp(prefix="qoffice-p8-", dir=os.environ.get("TMPDIR", "/tmp")))
    try:
        floor_text = FLOOR_TS.read_text(encoding="utf-8")
        floor_text = floor_text.replace(
            'from "./renderer-budget"', 'from "./renderer-budget.ts"'
        )
        (workdir / "renderer-floor.ts").write_text(floor_text, encoding="utf-8")
        shutil.copyfile(BUDGET_TS, workdir / "renderer-budget.ts")
        (workdir / "probe.mjs").write_text(_PROBE_JS, encoding="utf-8")
        proc = subprocess.run(
            [node, "--experimental-strip-types", "--no-warnings", "probe.mjs"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            return {
                "ok": False,
                "reason": f"node exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout)[-600:]}",
                "devices": [],
                "sequences": [],
                "invalid": {},
            }
        try:
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return {
                "ok": False,
                "reason": "node probe did not return JSON",
                "devices": [],
                "sequences": [],
                "invalid": {},
            }
        payload["node"] = proc.version if hasattr(proc, "version") else node
        return payload
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "reason": "node probe timed out",
            "devices": [],
            "sequences": [],
            "invalid": {},
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ------------------------------------------------------- observability probe
def _counter(start: int):
    value = start - 1

    def next_value() -> float:
        nonlocal value
        value += 1
        return float(value)

    return next_value


def _id_factory():
    counters: dict[str, int] = {}

    def make(kind: str) -> str:
        counters[kind] = counters.get(kind, 0) + 1
        return f"{kind}-{counters[kind]}"

    return make


_PROBE_IDS = {
    "tenant": "ten_probe",
    "owner": "own_probe",
    "agent": "agt_probe",
    "run": "run_probe",
    "task": "tsk_probe",
    "session": "ses_probe",
    "provider_request": "prq_probe",
    "artifact": "art_probe",
    "approval": "apr_probe",
}
_SECRET = "exact-vault-value-77a1"
_PROMPT_MARKER = "PROMPT_SENTINEL_NEVER_EXPORTED"


def _observability():
    from voicebridge.business import observability as OBS
    from voicebridge.business import otel

    redactor = lambda attrs: otel.redact_attributes(  # noqa: E731
        attrs, extra_secrets=(_SECRET,)
    )
    return OBS, otel, redactor


def compose_probe_run() -> Dict[str, Any]:
    """Compose one full run and return the exported snapshot + gate report."""
    OBS, otel, redactor = _observability()
    baselines = [
        OBS.EvalBaseline("contains_answer", min_pass_rate=1.0, min_score=0.9),
    ]
    obs = OBS.Observability(
        sampling=otel.SamplingPolicy(baseline_rate=1.0),
        clock=_counter(1000),
        id_factory=_id_factory(),
        redactor=redactor,
        eval_baselines=baselines,
    )
    with obs.start(ids=_PROBE_IDS) as run:
        with run.run(attributes={"qubit.run.kind": "business_turn"}):
            with run.model() as model_span:
                model_span.set_attribute(
                    "gen_ai.request.model", "probe-model"
                )
                model_span.record_content("prompt", _PROMPT_MARKER)
                model_span.record_content("completion", "probe completion")
                model_span.set_attribute("api_key", "sk-verysecret123456")
                model_span.set_attribute("note", f"vault={_SECRET}")
                model_span.set_attribute("owner_email", "owner@example.com")
                model_span.set_attribute("owner_phone", "+40 721 123 456")
            with run.tool() as tool_span:
                tool_span.set_attribute("gen_ai.tool.name", "browser")
                tool_span.set_status(otel.STATUS_ERROR)
            with run.approval() as approval_span:
                approval_span.set_attribute("qubit.approval.id", "apr_probe")
                approval_span.set_attribute("qubit.approval.decision", "allow")
            with run.receipt() as receipt_span:
                receipt_span.set_attribute(
                    "qubit.receipt.id", "rcpt_probe"
                )
                receipt_span.set_attribute(
                    "qubit.provider.request.id", "prq_probe"
                )
            with run.reconciliation():
                pass
            run.record_usage(
                input_tokens=120,
                output_tokens=40,
                cost_usd=0.003,
                retries=1,
                queue_age_ms=7.5,
            )
            run.mark_unknown(outcome="unknown")
            run.evaluate(
                model_span,
                "contains_answer",
                score=1.0,
                label="pass",
                passed=True,
            )
    snapshot = obs.snapshot()
    trace_id = (
        snapshot["spans"][0].trace_id if snapshot.get("spans") else None
    )
    return {
        "snapshot": snapshot,
        "gate": obs.eval_gate.report(),
        "trace_id": trace_id,
    }


def _run_sampling_cases() -> Dict[str, Any]:
    OBS, otel, redactor = _observability()

    def make(baseline: float = 0.0):
        return OBS.Observability(
            sampling=otel.SamplingPolicy(baseline_rate=baseline),
            clock=_counter(2000),
            id_factory=_id_factory(),
            redactor=redactor,
        )

    clean = make()
    with clean.start(ids=_PROBE_IDS) as run:
        with run.run():
            pass
    error = make()
    error_raised = False
    try:
        with error.start(ids=_PROBE_IDS) as run:
            with run.run():
                with run.tool() as span:
                    span.set_status(otel.STATUS_ERROR)
                    raise RuntimeError("probe tool failure")
    except RuntimeError:
        error_raised = True
    timeout = make()
    with timeout.start(ids=_PROBE_IDS) as run:
        with run.model() as span:
            span.set_status(otel.STATUS_TIMEOUT)
    failed_eval = make()
    with failed_eval.start(ids=_PROBE_IDS) as run:
        with run.model() as model_span:
            run.evaluate(
                model_span, "contains_answer", score=0.1, passed=False
            )
    outlier = make()
    with outlier.start(ids=_PROBE_IDS) as run:
        run.mark_outlier(reason="probe")
    baseline = make(1.0)
    with baseline.start(ids=_PROBE_IDS) as run:
        with run.run():
            pass

    def reason(obs):
        traces = obs.snapshot()["traces"]
        return traces[0].sampling_reason if traces else None

    reasons = {
        "error": reason(error),
        "timeout": reason(timeout),
        "failed_eval": reason(failed_eval),
        "outlier": reason(outlier),
        "baseline": reason(baseline),
    }
    return {
        "clean_dropped": clean.snapshot()["traces"] == []
        and clean.snapshot()["dropped"] == 1,
        "error_raised": error_raised,
        "reasons": reasons,
        "ok": (
            clean.snapshot()["traces"] == []
            and clean.snapshot()["dropped"] == 1
            and error_raised
            and reasons["error"] == "error"
            and reasons["timeout"] == "timeout"
            and reasons["failed_eval"] == "failed_eval"
            and reasons["outlier"] == "outlier"
            and reasons["baseline"] == "baseline"
        ),
    }


def _run_eval_regression() -> Dict[str, Any]:
    OBS, otel, redactor = _observability()

    def gate_run(passed: bool, score: float):
        obs = OBS.Observability(
            sampling=otel.SamplingPolicy(baseline_rate=1.0),
            clock=_counter(3000),
            id_factory=_id_factory(),
            redactor=redactor,
            eval_baselines=[
                OBS.EvalBaseline(
                    "contains_answer", min_pass_rate=1.0, min_score=0.9
                )
            ],
        )
        with obs.start(ids=_PROBE_IDS) as run:
            with run.model() as model_span:
                run.evaluate(
                    model_span,
                    "contains_answer",
                    score=score,
                    passed=passed,
                )
        return obs.eval_gate.report()

    good = gate_run(True, 1.0)
    bad = gate_run(False, 0.2)
    missing_obs = OBS.Observability(
        sampling=otel.SamplingPolicy(baseline_rate=1.0),
        eval_baselines=[
            OBS.EvalBaseline("contains_answer", min_pass_rate=1.0)
        ],
    )
    with missing_obs.start(ids=_PROBE_IDS) as run:
        with run.run():
            pass
    missing = missing_obs.eval_gate.report()
    return {
        "good_blocked": bool(good["blocked"]),
        "bad_blocked": bool(bad["blocked"]),
        "bad_reasons": list(bad["reasons"]),
        "missing_blocked": bool(missing["blocked"]),
        "ok": (
            good["blocked"] is False
            and bad["blocked"] is True
            and bool(bad["reasons"])
            and missing["blocked"] is True
        ),
    }


# --------------------------------------------------------------- main audit
def build_report(*, node_timeout: int = 60, renderer_timeout: int = 300) -> Dict[str, Any]:
    from voicebridge.business import observability as OBS

    accessibility_audit = _load_script("p8_accessibility_audit", A11Y_SCRIPT)
    a11y_raw = accessibility_audit.build_report(PLUGIN_ROOT)
    a11y = OBS.accessibility_matrix(a11y_raw)

    budget_report = renderer_report(timeout=renderer_timeout)
    device_contract = renderer_contract_probe(timeout=node_timeout)
    performance = OBS.performance_matrix(
        budget_report, device_contract=device_contract
    )
    context_loss = OBS.context_loss_matrix(device_contract)
    matrices = OBS.matrices_report(
        accessibility=a11y,
        performance=performance,
        context_loss=context_loss,
    )

    probe = compose_probe_run()
    snapshot = probe["snapshot"]
    spans = list(snapshot.get("spans") or [])
    metrics = OBS.metrics_by_name(snapshot)
    stages = OBS.trace_stages(spans)
    trace_ids = {span.trace_id for span in spans}
    by_stage = {
        stage: [
            span for span in spans
            if span.attributes.get("qubit.observability.stage") == stage
        ]
        for stage in OBS.STAGES
    }
    run_span = (by_stage[OBS.STAGE_RUN] or [None])[0]
    root_span = spans[0] if spans else None
    child_stages = [
        stage for stage in OBS.STAGES if stage not in (OBS.STAGE_REQUEST, OBS.STAGE_RUN)
    ]
    child_parent_ok = bool(run_span) and all(
        by_stage[stage]
        and by_stage[stage][0].parent_span_id == run_span.span_id
        for stage in child_stages
    )
    run_parent_ok = bool(run_span and root_span) and (
        run_span.parent_span_id == root_span.span_id
    )

    from voicebridge.business import otel

    requirements = [
        _check(
            "one_trace",
            len(snapshot.get("traces") or []) == 1 and len(trace_ids) == 1,
            f"traces={len(snapshot.get('traces') or [])} "
            f"trace_ids={len(trace_ids)}",
        ),
        _check(
            "canonical_stage_order",
            stages == list(OBS.STAGES),
            f"stages={stages}",
        ),
        _check(
            "stage_span_names",
            all(
                by_stage[stage]
                and by_stage[stage][0].name == OBS.SPAN_FOR_STAGE[stage]
                for stage in OBS.STAGES
            ),
            f"spans={[span.name for span in spans]}",
        ),
        _check(
            "run_nests_model_tool_approval_receipt_reconciliation",
            run_parent_ok and child_parent_ok,
            f"run_parent_ok={run_parent_ok} child_parent_ok={child_parent_ok}",
        ),
        _check(
            "all_required_ids",
            root_span is not None
            and all(
                root_span.attributes.get(attribute) not in (None, "")
                for attribute in OBS.ID_ATTRIBUTES.values()
            ),
            f"ids={ {name: (root_span.attributes.get(attr) if root_span else None) for name, attr in OBS.ID_ATTRIBUTES.items()} }",
        ),
        _check(
            "required_metrics",
            all(name in metrics for name in OBS.REQUIRED_METRICS),
            f"metrics={sorted(metrics)}",
        ),
        _check(
            "token_cost_tool_error_retry_queue_metrics",
            metrics.get(OBS.METRIC_INPUT_TOKENS, [{}])[0].get("value") == 120
            and metrics.get(OBS.METRIC_OUTPUT_TOKENS, [{}])[0].get("value") == 40
            and metrics.get(OBS.METRIC_COST, [{}])[0].get("value") == 0.003
            and metrics.get(OBS.METRIC_TOOL_ERRORS, [{}])[0].get("value") == 1
            and metrics.get(OBS.METRIC_RETRIES, [{}])[0].get("value") == 1
            and metrics.get(OBS.METRIC_QUEUE_AGE, [{}])[0].get("value") == 7.5
            and metrics.get(OBS.METRIC_UNKNOWN, [{}])[0].get("value") == 1,
            "usage/cost/errors/retries/queue-age/unknown metrics carry the"
            " values the run recorded",
        ),
        _check(
            "content_capture_off",
            all(not span.content for span in spans)
            and not any(
                span.attributes.get("gen_ai.content.captured") for span in spans
            ),
            "no span carries captured prompt/completion content",
        ),
        _check(
            "no_prompt_or_pii_leakage",
            OBS.leak_markers_found(snapshot) == []
            and _PROMPT_MARKER not in OBS.serialized_export(snapshot),
            "secret/PII/prompt markers are absent from the export",
        ),
        _check(
            "eval_event_linked_to_judged_span",
            any(
                event.get("name") == otel.GEN_AI_EVAL_EVENT
                and event.get("span_id") == by_stage[OBS.STAGE_MODEL][0].span_id
                for event in snapshot.get("events") or []
            )
            and bool(by_stage[OBS.STAGE_MODEL]),
            "the eval event points at the judged model span",
        ),
        _check(
            "clean_eval_gate_passes",
            probe["gate"]["blocked"] is False,
            f"gate={probe['gate']['rows']}",
        ),
    ]

    sampling = _run_sampling_cases()
    eval_regression = _run_eval_regression()

    # Honesty negatives: a fabricated axe claim and a fabricated GPU number
    # must be rejected rather than counted as evidence.
    fake_a11y = dict(a11y_raw)
    fake_a11y["axe"] = {"status": "ran", "claimed_run": True}
    a11y_negative = OBS.accessibility_matrix(fake_a11y)["ok"] is False
    fake_budget = json.loads(json.dumps(budget_report))
    for row in fake_budget.get("budgets") or []:
        if row.get("metric") == "frame_p95_ms":
            row["status"] = "pass"
            row["value"] = 15.0
            row["ok"] = True
    fake_budget.setdefault("budget_sample", {})["supplied"] = False
    perf_negative = (
        OBS.performance_matrix(fake_budget, device_contract=None)["ok"] is False
    )

    checks = list(requirements)
    checks.extend(
        [
            _check(
                "sampling_policy",
                bool(sampling["ok"]),
                f"reasons={sampling['reasons']} clean_dropped="
                f"{sampling['clean_dropped']} error_raised="
                f"{sampling['error_raised']}",
            ),
            _check(
                "eval_regression_blocks_release",
                bool(eval_regression["ok"]),
                f"bad_blocked={eval_regression['bad_blocked']} "
                f"missing_blocked={eval_regression['missing_blocked']}",
            ),
            _check(
                "accessibility_matrix_honest",
                bool(a11y["ok"]) and a11y["axe"]["status"] == "not_available"
                and a11y["axe"]["claimed_run"] is False
                and all(
                    review["status"] in ("not_performed", "unit_proven")
                    for review in a11y["reviews"]
                ),
                f"axe={a11y['axe']} static={a11y['static']}",
            ),
            _check(
                "accessibility_fake_axe_claim_rejected",
                a11y_negative,
                "an audit claiming a run it did not perform is rejected",
            ),
            _check(
                "performance_matrix_real_data_path",
                bool(performance["ok"])
                and performance["data_path"]["measured"] is True,
                f"data_path={performance['data_path']}",
            ),
            _check(
                "gpu_frame_and_heap_not_fabricated",
                performance["sample_supplied"] is True
                or all(
                    row["status"] == "not_measured" and row["value"] is None
                    for row in performance["budgets"]
                    if row["metric"] in ("frame_p95_ms", "heap_mb")
                ),
                f"gpu_not_measured={performance['gpu_not_measured']} "
                f"sample_supplied={performance['sample_supplied']}",
            ),
            _check(
                "performance_fake_gpu_claim_rejected",
                perf_negative,
                "a GPU frame claimed without a supplied sample is rejected",
            ),
            _check(
                "device_tiers_exercised",
                bool(device_contract.get("ok"))
                and len(performance["device_tiers"]) >= 2,
                f"contract_ok={device_contract.get('ok')} "
                f"tiers={[row.get('tier') for row in performance['device_tiers']]}",
            ),
            _check(
                "context_loss_recovery_exercised",
                bool(context_loss["ok"]),
                f"sequences={context_loss['sequences']}",
            ),
        ]
    )
    status = "PASS" if all(item["ok"] for item in checks) else "FAIL"

    return {
        "schema": SCHEMA,
        "task_id": TASK_ID,
        "status": status,
        "ok": status == "PASS",
        "generated_at": _utc_now(),
        "checks": checks,
        "checks_total": len(checks),
        "checks_failed": sum(1 for item in checks if not item["ok"]),
        "observability": {
            "trace_id": probe["trace_id"],
            "stages": stages,
            "span_count": len(spans),
            "metric_names": sorted(metrics),
            "eval_gate": probe["gate"],
            "sampling": sampling,
            "eval_regression": eval_regression,
            "leak_markers": OBS.leak_markers_found(snapshot),
            "content_captured": any(span.content for span in spans),
        },
        "accessibility": a11y,
        "performance": performance,
        "context_loss": context_loss,
        "matrices": matrices,
        "inputs": [
            {"path": str(A11Y_SCRIPT.relative_to(ROOT)), "sha256": _sha256(A11Y_SCRIPT)},
            {
                "path": str(RENDERER_SCRIPT.relative_to(ROOT)),
                "sha256": _sha256(RENDERER_SCRIPT),
            },
            {"path": str(FLOOR_TS.relative_to(ROOT)), "sha256": _sha256(FLOOR_TS)},
            {"path": str(BUDGET_TS.relative_to(ROOT)), "sha256": _sha256(BUDGET_TS)},
        ],
        "limitations": [
            "the accessibility matrix consumes a static source/CSS audit; axe,"
            " a screen reader, high-contrast, 200% zoom, touch and muted-audio"
            " reviews were NOT performed",
            "GPU frame time and browser heap are not measured here; they stay"
            " not_measured unless a real browser sample is supplied",
            "the measured floor-projection timing is a Python data-path number,"
            " not a rendered frame time",
            "device tiers and context-loss/recovery are exercised by executing"
            " the shipped TypeScript renderer contract in node; no real WebGL"
            " context was lost and no GPU was used",
            "no live service, credential, network or external side effect is"
            " touched; no full matrix was run",
        ],
    }


# --------------------------------------------------------------------- CLI
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify P8-04 observability/evals and the QA matrices."
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--out", default=None, help="write the JSON report")
    parser.add_argument(
        "--node-timeout", type=int, default=60, help="node probe timeout (s)"
    )
    parser.add_argument(
        "--renderer-timeout",
        type=int,
        default=300,
        help="renderer verifier timeout (s)",
    )
    args = parser.parse_args(argv)
    try:
        report = build_report(
            node_timeout=args.node_timeout,
            renderer_timeout=args.renderer_timeout,
        )
    except UsageError as exc:
        payload = {
            "schema": SCHEMA,
            "task_id": TASK_ID,
            "status": "FAIL",
            "ok": False,
            "code": exc.code,
            "detail": exc.detail,
        }
        print(json.dumps(payload, indent=2, sort_keys=True, default=_json_default))
        return EXIT_USAGE

    payload = json.dumps(report, indent=2, sort_keys=True, default=_json_default)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(payload)
    else:
        print(
            f"{TASK_ID}: {report['status']} — "
            f"{report['checks_failed']} failed of {report['checks_total']}"
        )
        for item in report["checks"]:
            mark = "ok" if item["ok"] else "FAIL"
            print(f"  [{mark}] {item['name']}: {item['detail']}")
        print(f"accessibility axe: {report['accessibility']['axe']['status']}")
        print(
            "performance GPU not measured: "
            f"{report['performance']['gpu_not_measured']}"
        )
    return EXIT_OK if report["ok"] else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
