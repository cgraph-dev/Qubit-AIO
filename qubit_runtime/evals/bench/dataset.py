"""Offline dataset loading for the eval bench.

Accepted input is a JSONL file or the local trace rows. Every record is already
a finished sample (prompt + response + optional label); the bench never calls a
model and never opens a network connection.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

_RESPONSE_KEYS = ("response", "response_excerpt", "answer", "output", "model_answer")
_REFERENCE_KEYS = ("reference", "expected", "label", "reference_text")
_ID_KEYS = ("id", "record_id", "trace_id")
_PROMPT_KEYS = ("prompt", "prompt_excerpt", "query")
_VARIANT_KEYS = ("variant", "variant_name", "prompt_variant")


class EvalInputError(ValueError):
    """Bad dataset input; ``code`` is stable for the CLI."""

    code = "eval_invalid_input"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        if code:
            self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class EvalSample:
    record_id: str
    response: str = ""
    reference: str | None = None
    correct: bool | None = None
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    energy_joules: float | None = None
    flops: float | None = None
    error: str | None = None
    prompt: str = ""
    variant: str | None = None

    @property
    def is_labeled(self) -> bool:
        return self.correct is not None or self.reference is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "response": self.response,
            "reference": self.reference,
            "correct": self.correct,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": self.cost_usd,
            "energy_joules": self.energy_joules,
            "flops": self.flops,
            "error": self.error,
            "prompt": self.prompt,
            "variant": self.variant,
        }


def _pick(obj: Mapping[str, Any], keys: Iterable[str]) -> tuple[bool, Any]:
    for key in keys:
        if key in obj:
            return True, obj[key]
    return False, None


def _number(value: Any, name: str, *, integer: bool = False) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise EvalInputError(f"{name} must be a number, not a boolean")
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise EvalInputError(f"{name} must be a number") from exc
    if integer:
        if number < 0:
            raise EvalInputError(f"{name} must not be negative")
        return number
    if not math.isfinite(number) or number < 0:
        raise EvalInputError(f"{name} must be a finite non-negative number")
    return number


def _text(value: Any, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise EvalInputError(f"{name} must be a string")
    return value


def sample_from_mapping(obj: Mapping[str, Any], *, fallback_id: str) -> EvalSample:
    if not isinstance(obj, Mapping):
        raise EvalInputError("each dataset line must be a JSON object")

    found_id, raw_id = _pick(obj, _ID_KEYS)
    record_id = _text(raw_id, "id") if found_id else fallback_id
    if not record_id:
        record_id = fallback_id

    found_response, raw_response = _pick(obj, _RESPONSE_KEYS)
    response = _text(raw_response, "response") if found_response else ""

    found_prompt, raw_prompt = _pick(obj, _PROMPT_KEYS)
    prompt = _text(raw_prompt, "prompt") if found_prompt else ""

    found_variant, raw_variant = _pick(obj, _VARIANT_KEYS)
    variant = _text(raw_variant, "variant").strip() or None if found_variant else None

    found_reference, raw_reference = _pick(obj, _REFERENCE_KEYS)
    reference = _text(raw_reference, "reference") if found_reference and raw_reference is not None else None

    found_correct, raw_correct = _pick(obj, ("correct", "is_correct"))
    if found_correct and raw_correct is not None and not isinstance(raw_correct, bool):
        raise EvalInputError("correct must be a boolean")
    correct = raw_correct if found_correct else None

    usage = obj.get("usage") if isinstance(obj.get("usage"), Mapping) else {}
    prompt_tokens = _number(
        obj.get("prompt_tokens", usage.get("prompt_tokens")), "prompt_tokens", integer=True)
    completion_tokens = _number(
        obj.get("completion_tokens", usage.get("completion_tokens")),
        "completion_tokens", integer=True)

    found_latency_ms, latency_ms = _pick(obj, ("latency_ms",))
    if found_latency_ms:
        latency = _number(latency_ms, "latency_ms")
    else:
        found_latency_s, latency_s = _pick(obj, ("latency_seconds",))
        seconds = _number(latency_s, "latency_seconds") if found_latency_s else None
        latency = None if seconds is None else seconds * 1000.0

    found_cost, raw_cost = _pick(obj, ("cost_usd", "cost"))
    cost = _number(raw_cost, "cost_usd") if found_cost else None

    found_energy, raw_energy = _pick(obj, ("energy_joules", "energy_j"))
    energy = _number(raw_energy, "energy_joules") if found_energy else None

    found_flops, raw_flops = _pick(obj, ("flops", "estimated_flops"))
    flops = _number(raw_flops, "flops") if found_flops else None

    found_error, raw_error = _pick(obj, ("error", "error_code"))
    error = _text(raw_error, "error") if found_error and raw_error is not None else None

    return EvalSample(
        record_id=record_id,
        response=response,
        reference=reference,
        correct=correct,
        latency_ms=latency,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost,
        energy_joules=energy,
        flops=flops,
        error=error,
        prompt=prompt,
        variant=variant,
    )


def load_jsonl(path: str | Path) -> list[EvalSample]:
    """Load and validate a JSONL dataset. Raises a typed ``EvalInputError``."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise EvalInputError(f"dataset not found: {source}", code="dataset_missing")
    try:
        text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise EvalInputError(f"cannot read dataset {source}: {exc}", code="dataset_unreadable") from exc
    samples: list[EvalSample] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise EvalInputError(
                f"dataset line {line_number} is not valid JSON: {exc}",
                code="dataset_invalid_json",
            ) from exc
        try:
            samples.append(sample_from_mapping(obj, fallback_id=f"line-{line_number}"))
        except EvalInputError as exc:
            raise EvalInputError(
                f"dataset line {line_number}: {exc.message}", code=exc.code) from exc
    return samples


def samples_from_traces(rows: Iterable[Any]) -> list[EvalSample]:
    """Adapt stored trace rows; no reference/correct label is invented."""
    samples: list[EvalSample] = []
    for row in rows:
        samples.append(EvalSample(
            record_id=str(getattr(row, "id", "") or ""),
            response=str(getattr(row, "response_excerpt", "") or ""),
            reference=None,
            correct=None,
            latency_ms=getattr(row, "latency_ms", None),
            prompt_tokens=getattr(row, "prompt_tokens", None),
            completion_tokens=getattr(row, "completion_tokens", None),
            cost_usd=getattr(row, "cost_usd", None),
            energy_joules=getattr(row, "energy_joules", None),
            flops=getattr(row, "flops", None),
            error=getattr(row, "error_code", None),
            prompt=str(getattr(row, "prompt_excerpt", "") or ""),
        ))
    return samples


__all__ = [
    "EvalInputError",
    "EvalSample",
    "load_jsonl",
    "sample_from_mapping",
    "samples_from_traces",
]
