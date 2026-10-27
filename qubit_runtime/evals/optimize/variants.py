"""Bounded definitions for candidate prompt variants.

A variant is an id, the prompt text it stands for, and an optional description.
The set is deliberately small (2–4) and each prompt is length-bounded: this is
an offline selector over recorded labeled outputs, not a prompt generator.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from evals.optimize.errors import OptimizeError

MIN_VARIANTS = 2
MAX_VARIANTS = 4
MAX_NAME_CHARS = 64
MAX_PROMPT_CHARS = 4000
MAX_TARGET_CHARS = 128
MAX_DESCRIPTION_CHARS = 500

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_STOPWORDS = {"name", "prompt", "description"}


def _text(value: Any, field: str, *, limit: int, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise OptimizeError(f"variant {field} must be a string",
                            code="optimize_variant_invalid")
    text = value.strip()
    if required and not text:
        raise OptimizeError(f"variant {field} must not be empty",
                            code="optimize_variant_invalid")
    if len(text) > limit:
        raise OptimizeError(
            f"variant {field} is {len(text)} characters; the limit is {limit}",
            code="optimize_variant_invalid")
    return text


def _name(value: Any) -> str:
    name = _text(value, "name", limit=MAX_NAME_CHARS, required=True)
    if not _NAME_RE.fullmatch(name):
        raise OptimizeError(
            "variant name must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}",
            code="optimize_variant_invalid")
    return name


@dataclass(frozen=True)
class PromptVariant:
    """One candidate prompt. The prompt text is never sent anywhere."""

    name: str
    prompt: str
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "prompt_chars": len(self.prompt),
            "prompt_sha256": hashlib.sha256(self.prompt.encode("utf-8")).hexdigest(),
        }


@dataclass(frozen=True)
class VariantSet:
    """2–4 named candidates plus the recipe/schema they target."""

    variants: tuple[PromptVariant, ...]
    target: str = ""
    source: str = ""

    def by_name(self) -> dict[str, PromptVariant]:
        return {variant.name: variant for variant in self.variants}

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "source": self.source,
            "variants": [variant.as_dict() for variant in self.variants],
        }


def build_variant_set(
    variants: Iterable[PromptVariant],
    *,
    target: str = "",
    source: str = "",
) -> VariantSet:
    """Validate count/uniqueness/bounds and return an immutable set."""
    items = tuple(variants)
    if not (MIN_VARIANTS <= len(items) <= MAX_VARIANTS):
        raise OptimizeError(
            f"exactly {MIN_VARIANTS}–{MAX_VARIANTS} candidate variants are required "
            f"(got {len(items)})",
            code="optimize_variant_count")
    seen: set[str] = set()
    for variant in items:
        key = variant.name.casefold()
        if key in seen:
            raise OptimizeError(f"duplicate variant name {variant.name!r}",
                                code="optimize_variant_duplicate")
        seen.add(key)
    return VariantSet(
        variants=items,
        target=_text(target, "target", limit=MAX_TARGET_CHARS),
        source=source,
    )


def variant_from_mapping(obj: Mapping[str, Any]) -> PromptVariant:
    if not isinstance(obj, Mapping):
        raise OptimizeError("each variant must be a JSON object",
                            code="optimize_variant_invalid")
    unknown = sorted(set(obj) - _STOPWORDS)
    if unknown:
        raise OptimizeError(
            f"unknown variant field(s): {', '.join(str(k) for k in unknown)}",
            code="optimize_variant_invalid")
    return PromptVariant(
        name=_name(obj.get("name")),
        prompt=_text(obj.get("prompt"), "prompt",
                     limit=MAX_PROMPT_CHARS, required=True),
        description=_text(obj.get("description"), "description",
                          limit=MAX_DESCRIPTION_CHARS),
    )


def load_variant_file(path: str | Path) -> VariantSet:
    """Load a JSON variant spec: ``{"target", "variants": [...]}`` or a list."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise OptimizeError(f"variants file not found: {source}",
                            code="optimize_variant_file_missing")
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError) as exc:
        raise OptimizeError(f"cannot read variants file {source}: {exc}",
                            code="optimize_variant_file_unreadable") from exc
    except (TypeError, ValueError) as exc:
        raise OptimizeError(f"variants file is not valid JSON: {exc}",
                            code="optimize_variant_file_invalid_json") from exc

    target = ""
    if isinstance(payload, Mapping):
        allowed = {"target", "variants"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise OptimizeError(
                f"unknown variants file field(s): {', '.join(str(k) for k in unknown)}",
                code="optimize_variant_invalid")
        target = payload.get("target") or ""
        raw_variants = payload.get("variants")
        if not isinstance(raw_variants, list):
            raise OptimizeError("variants file needs a 'variants' list",
                                code="optimize_variant_invalid")
    elif isinstance(payload, list):
        raw_variants = payload
    else:
        raise OptimizeError("variants file must be an object or a list",
                            code="optimize_variant_invalid")

    variants = [variant_from_mapping(item) for item in raw_variants]
    return build_variant_set(variants, target=target, source=str(source))


def variant_from_arg(spec: str) -> PromptVariant:
    """Parse ``NAME=PATH`` where PATH is a UTF-8 prompt file."""
    if not isinstance(spec, str) or "=" not in spec:
        raise OptimizeError(
            "--variant expects NAME=PATH (a prompt file)",
            code="optimize_variant_invalid")
    name, _, raw_path = spec.partition("=")
    name = name.strip()
    raw_path = raw_path.strip()
    if not raw_path:
        raise OptimizeError("--variant PATH must not be empty",
                            code="optimize_variant_invalid")
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise OptimizeError(f"variant prompt file not found: {path}",
                            code="optimize_variant_file_missing")
    try:
        prompt = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise OptimizeError(f"cannot read variant prompt {path}: {exc}",
                            code="optimize_variant_file_unreadable") from exc
    return PromptVariant(
        name=_name(name),
        prompt=_text(prompt, "prompt", limit=MAX_PROMPT_CHARS, required=True),
    )


def load_variants(
    specs: Iterable[str] | None = None,
    *,
    variants_file: str | Path | None = None,
    target: str = "",
) -> VariantSet:
    """Combine ``--variant`` specs and an optional spec file; validate the result."""
    variants: list[PromptVariant] = []
    file_target = ""
    source = "arguments"
    if variants_file:
        loaded = load_variant_file(variants_file)
        variants.extend(loaded.variants)
        file_target = loaded.target
        source = loaded.source
    for spec in specs or ():
        variants.append(variant_from_arg(spec))
    if file_target and target and file_target != target:
        raise OptimizeError(
            "target differs between --variants-file and --target",
            code="optimize_variant_invalid")
    return build_variant_set(
        variants,
        target=target or file_target,
        source=source,
    )


__all__ = [
    "MAX_DESCRIPTION_CHARS",
    "MAX_NAME_CHARS",
    "MAX_PROMPT_CHARS",
    "MAX_TARGET_CHARS",
    "MAX_VARIANTS",
    "MIN_VARIANTS",
    "PromptVariant",
    "VariantSet",
    "build_variant_set",
    "load_variant_file",
    "load_variants",
    "variant_from_arg",
    "variant_from_mapping",
]
