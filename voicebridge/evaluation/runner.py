"""Packet 6B: offline evaluation runner.

Runs every Packet 6B case through deterministic, network-free probes:
  - classifier probes  (in-process Classifier, llm_classifier=False)
  - lesson/memory probes (real Packet 6A ledger + Packet 5B memory store in a
    throwaway directory — never the user's private state)
  - structural resource probes (canonical runner contract constants / guard
    presence — real residency/VRAM sampling belongs to the opt-in live mode)
  - tool probes (CapabilityRegistry arg validators)

Poisoning categories go through the full poisoning runner. Everything is
offline and side-effect free; no model is loaded and no network is touched.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..benchmarking.corpus import Case
from ..capabilities import (CapabilityError, CapabilityRegistry,
                            CapabilitySpec, Tier)
from ..intent.classifier import Classifier
from ..intent.state import ConfirmationState, ConversationState
from ..learning import LearningLedger
from ..memory import MemoryStore
from .. import vram_guard as VG
from .. import runner_contract as RC
from . import poisoning as POI
from .scoring import CATEGORY_FAMILY

OFFLINE_CFG: Dict[str, Any] = {"llm_classifier": False}


class EvalComponents:
    """Throwaway, hermetic components for an offline evaluation run."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="vb-eval-")
        root = Path(self._tmp.name)
        self.ledger = LearningLedger(root)
        self.memory = MemoryStore(root / "mem.db")
        self.classifier = Classifier(
            dict(OFFLINE_CFG), ConfirmationState(), ConversationState(),
            busy_fn=lambda: False)
        self.tools = _build_tool_registry()

    def close(self) -> None:
        try:
            self.memory.close()
        except Exception:
            pass
        self._tmp.cleanup()


def _build_tool_registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()

    def _valid_note(args: Dict[str, Any]) -> None:
        text = args.get("text")
        if not isinstance(text, str) or not text.strip():
            raise CapabilityError("note.add: text is required and non-empty")

    def _valid_path(args: Dict[str, Any]) -> None:
        p = args.get("path")
        if not isinstance(p, str) or not p.strip():
            raise CapabilityError("file.read: path is required")
        if ".." in p or p.startswith("/"):
            raise CapabilityError("file.read: unsafe path")

    reg.register(CapabilitySpec(name="note.add", version="1.0.0",
                                tier=Tier.REVERSIBLE,
                                summary="append a local note",
                                scopes=("notes",), arg_validator=_valid_note))
    reg.register(CapabilitySpec(name="file.read", version="1.0.0",
                                tier=Tier.READ_ONLY,
                                summary="read a repository-relative file",
                                scopes=("files",), arg_validator=_valid_path))
    return reg


def _classifier_probe(case: Case) -> Dict[str, Any]:
    conf = ConfirmationState()
    if case.expected.get("pending_required"):
        conf.request("eval demo action", {}, "eval-fixture")
    cls = Classifier(dict(OFFLINE_CFG), confirmations=conf,
                     conversation=ConversationState(),
                     busy_fn=lambda: bool(case.expected.get("busy_required")))
    t0 = time.perf_counter()
    kind, err = None, None
    try:
        kind = cls.classify(case.text).kind
    except Exception as exc:  # pragma: no cover - defensive
        err = f"{type(exc).__name__}: {exc}"[:160]
    lat_ms = round((time.perf_counter() - t0) * 1000.0, 4)
    acceptable = tuple(case.expected.get("expected_kinds") or ())
    passed = err is None and kind in acceptable
    return {"latency_ms": lat_ms, "observed_kind": kind, "error": err,
            "passed": bool(passed),
            "failed_reason": None if passed
            else f"expected kind in {acceptable}, got {kind!r}"}


def _poison_probe(case: Case, comp: EvalComponents) -> Dict[str, Any]:
    res = POI.run_poisoning(cases=[case], classifier=comp.classifier,
                            ledger=comp.ledger, memory=comp.memory)[0]
    return {"observed_kind": res.get("observed_kind"),
            "passed": bool(res["passed"]),
            "failed_reason": res.get("failed_reason"),
            "error": None}


def _correction_probe(case: Case, comp: EvalComponents) -> Dict[str, Any]:
    """Run a concrete correction scenario against the ledger.

    Base case uses canned defaults; PRIVATE cases carry the user's real
    question/wrong/corrected text, so the correction machinery is exercised on
    genuine accepted-corrected content.
    """
    question = case.expected.get("question") or "what is my favorite color"
    wrong = case.expected.get("wrong") or "blue"
    corrected = case.expected.get("corrected") or "teal"
    if not (corrected and question):
        return {"passed": False, "error": None,
                "failed_reason": "correction case missing question/corrected"}
    led = comp.ledger
    led.record(question, wrong, feedback="good")
    led.record(question, wrong, feedback="good")
    led.record(question, wrong, feedback="correction", corrected=corrected)
    prompt = led.as_prompt()
    passed = corrected in prompt and wrong not in prompt
    return {"passed": passed,
            "failed_reason": None if passed
            else "correction did not supersede the stale answer"}


def _memory_probe(case: Case, comp: EvalComponents) -> Dict[str, Any]:
    mem = comp.memory
    old_fact = case.expected.get("fact_old") or "my favorite city is London-demo"
    new_fact = case.expected.get("fact_new") or "my favorite city is Berlin-demo"
    mem.remember(old_fact)
    mem.correct(old_fact, new_fact)
    hits = mem.search(" ".join(old_fact.split()[:4]))
    texts = [h["text"] for h in hits]
    passed = bool(hits) and any(new_fact in t for t in texts) \
        and not any(old_fact in t for t in texts)
    return {"passed": passed,
            "failed_reason": None if passed
            else "corrected fact did not outrank stale fact"}


def _resource_probe(case: Case) -> Dict[str, Any]:
    """Structural offline checks; live residency/VRAM is the opt-in live mode."""
    if case.category == "model_residency":
        from dataclasses import fields as _fields
        contract_fields = {f.name for f in _fields(RC.RunnerContract)}
        checks = {
            "canonical_keep_alive_pinned": RC.KEEP_ALIVE == -1,
            "contract_has_num_ctx": "num_ctx" in contract_fields,
        }
    elif case.category == "runner_options":
        checks = {
            "single_contract_type": hasattr(RC, "RunnerContract"),
            "resolve_available": callable(RC.resolve),
        }
    else:  # vram_guard
        checks = {
            "guard_callable": callable(VG.guard_load),
            "headroom_positive": getattr(VG, "DEFAULT_HEADROOM_MIB", 0) > 0,
            "typed_error": hasattr(VG, "VRAMGuardError"),
        }
    passed = all(checks.values())
    return {"resource": checks, "passed": passed,
            "failed_reason": None if passed else str(
                {k: v for k, v in checks.items() if not v})}


def _tool_probe(case: Case, comp: EvalComponents) -> Dict[str, Any]:
    spec = comp.tools.get(case.expected.get("tool") or "")
    if spec is None:
        return {"passed": False, "error": None,
                "failed_reason": "unknown tool in eval case"}
    args = dict(case.expected.get("args") or {})
    valid = bool(case.expected.get("valid", True))
    try:
        spec.arg_validator(args)
        accepted = True
    except CapabilityError:
        accepted = False
    # tool_selection expects valid args accepted; invalid_arguments expects the
    # validator to REJECT; tool_execution additionally simulates a run.
    if case.category == "invalid_arguments":
        passed = accepted is False and valid is False
    elif case.category == "tool_execution":
        runs_ok = bool(case.expected.get("runs_ok", True))
        passed = accepted is True and runs_ok
    else:
        passed = accepted is True
    return {"tool": spec.name, "denied": not accepted, "passed": passed,
            "error": None,
            "failed_reason": None if passed else "tool argument gate mismatch"}


def evaluate_cases(cases: List[Case], comp: Optional[EvalComponents] = None,
                   ) -> List[Dict[str, Any]]:
    """Evaluate a list of cases offline. Pass a shared EvalComponents to keep
    lesson/memory state across cases (e.g. poisoning), else create one."""
    own = comp is None
    comp = comp or EvalComponents()
    rows: List[Dict[str, Any]] = []
    try:
        for case in cases:
            base = {"case_id": case.case_id, "category": case.category,
                    "split": case.split}
            fam = CATEGORY_FAMILY.get(case.category, "quality")
            if case.category in ("prompt_injection", "secret_handling"):
                rows.append({**base, **_poison_probe(case, comp)})
            elif fam == "safety" or case.category in (
                    "short_chat", "language_policy", "deterministic_routing",
                    "ambiguous_routing", "malformed_speech"):
                rows.append({**base, **_classifier_probe(case)})
            elif case.category == "correction_priority":
                rows.append({**base, **_correction_probe(case, comp)})
            elif case.category in ("personal_fact_conflict",
                                   "memory_retrieval"):
                rows.append({**base, **_memory_probe(case, comp)})
            elif fam == "resource":
                rows.append({**base, **_resource_probe(case)})
            elif fam == "tool_success":
                rows.append({**base, **_tool_probe(case, comp)})
            else:  # pragma: no cover - defensive
                rows.append({**base, "passed": False, "error": None,
                             "failed_reason": "no offline probe"})
    finally:
        if own:
            comp.close()
    return rows