"""Optional label sidecars for trace rows.

The trace store deliberately stores no ground truth. Labels stay in a separate
JSONL file keyed by trace id, so selecting variants over traces never requires a
schema change or an invented label.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from evals.bench.dataset import EvalSample
from evals.optimize.errors import OptimizeError

MAX_LABELS = 20_000
MAX_REFERENCE_CHARS = 4000
_ID_KEYS = ("id", "trace_id", "record_id")
_REFERENCE_KEYS = ("reference", "expected", "reference_text")
_CORRECT_KEYS = ("correct", "is_correct")
_KNOWN = set(_ID_KEYS) | set(_REFERENCE_KEYS) | set(_CORRECT_KEYS)
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class TraceLabel:
    reference: str | None = None
    correct: bool | None = None

    def __post_init__(self) -> None:
        if self.reference is None and self.correct is None:
            raise OptimizeError(
                "a label needs a reference text or a correct boolean",
                code="optimize_label_invalid")


def _pick(obj: Mapping[str, Any], keys: Iterable[str]) -> tuple[bool, Any]:
    for key in keys:
        if key in obj:
            return True, obj[key]
    return False, None


def _label_from_mapping(obj: Mapping[str, Any], line_number: int) -> tuple[str, TraceLabel]:
    if not isinstance(obj, Mapping):
        raise OptimizeError(f"labels line {line_number} must be a JSON object",
                            code="optimize_label_invalid")
    unknown = sorted(set(obj) - _KNOWN)
    if unknown:
        raise OptimizeError(
            f"labels line {line_number} has unknown field(s): "
            f"{', '.join(str(k) for k in unknown)}",
            code="optimize_label_invalid")
    found_id, raw_id = _pick(obj, _ID_KEYS)
    if not found_id or not isinstance(raw_id, str) or not raw_id.strip():
        raise OptimizeError(f"labels line {line_number} needs a non-empty id",
                            code="optimize_label_invalid")
    record_id = raw_id.strip()

    found_reference, raw_reference = _pick(obj, _REFERENCE_KEYS)
    reference: str | None = None
    if found_reference and raw_reference is not None:
        if not isinstance(raw_reference, str):
            raise OptimizeError(f"labels line {line_number}: reference must be a string",
                                code="optimize_label_invalid")
        reference = _WHITESPACE.sub(" ", raw_reference.strip()) or None
        if reference is not None and len(reference) > MAX_REFERENCE_CHARS:
            raise OptimizeError(
                f"labels line {line_number}: reference is longer than "
                f"{MAX_REFERENCE_CHARS} characters",
                code="optimize_label_invalid")

    found_correct, raw_correct = _pick(obj, _CORRECT_KEYS)
    correct: bool | None = None
    if found_correct and raw_correct is not None:
        if not isinstance(raw_correct, bool):
            raise OptimizeError(
                f"labels line {line_number}: correct must be a boolean",
                code="optimize_label_invalid")
        correct = raw_correct

    try:
        label = TraceLabel(reference=reference, correct=correct)
    except OptimizeError as exc:
        raise OptimizeError(f"labels line {line_number}: {exc.message}",
                            code=exc.code) from exc
    return record_id, label


def load_labels(path: str | Path) -> dict[str, TraceLabel]:
    """Load ``{id, reference|correct}`` JSONL records; duplicate ids are refused."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise OptimizeError(f"labels file not found: {source}",
                            code="optimize_labels_missing")
    try:
        text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise OptimizeError(f"cannot read labels file {source}: {exc}",
                            code="optimize_labels_unreadable") from exc
    labels: dict[str, TraceLabel] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise OptimizeError(
                f"labels line {line_number} is not valid JSON: {exc}",
                code="optimize_labels_invalid_json") from exc
        record_id, label = _label_from_mapping(obj, line_number)
        if record_id in labels:
            raise OptimizeError(f"duplicate label for id {record_id!r}",
                                code="optimize_duplicate_label")
        labels[record_id] = label
        if len(labels) > MAX_LABELS:
            raise OptimizeError(
                f"labels file exceeds the {MAX_LABELS}-record bound",
                code="optimize_too_many_samples")
    if not labels:
        raise OptimizeError("labels file has no records",
                            code="optimize_labels_missing")
    return labels


def apply_labels(
    samples: Iterable[EvalSample],
    labels: Mapping[str, TraceLabel],
) -> tuple[list[EvalSample], int, int]:
    """Overlay sidecar labels by record id; return samples and match counts."""
    updated: list[EvalSample] = []
    matched = 0
    for sample in samples:
        label = labels.get(sample.record_id)
        if label is None:
            updated.append(sample)
            continue
        matched += 1
        updated.append(replace(
            sample,
            reference=sample.reference if label.reference is None else label.reference,
            correct=sample.correct if label.correct is None else label.correct,
        ))
    return updated, matched, len(labels) - matched


__all__ = [
    "MAX_LABELS",
    "TraceLabel",
    "apply_labels",
    "load_labels",
]
