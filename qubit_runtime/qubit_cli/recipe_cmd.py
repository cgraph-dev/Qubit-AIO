"""``qubit recipe`` handler: list and run recipes."""
from __future__ import annotations

import json
import sys
from typing import Any

from agent.runtime_port import UnknownRuntimeError
from recipes.registry import UnknownRecipeError, build_recipe_registry
from recipes.runner import run_recipe


def _recipe_registry(registry: Any | None) -> Any:
    return registry if registry is not None else build_recipe_registry()


def _backend_registry(registry: Any | None) -> Any:
    if registry is not None:
        return registry
    from agent.runtime_registry import build_registry
    from qubit_cli.runtime_cmd import _native_factory

    return build_registry(_native_factory, native_stream=True)


def _print_listing(registry: Any, as_json: bool) -> int:
    specs = registry.specs()
    if as_json:
        print(json.dumps([spec.as_dict() for spec in specs], ensure_ascii=False))
        return 0
    for spec in specs:
        print(f"{spec.id}\t{spec.title}\t{spec.default_schedule}")
    return 0


def _print_run(run, as_json: bool) -> None:
    if as_json:
        print(json.dumps(run.as_dict(), ensure_ascii=False, default=str))
        return
    if run.brief:
        text = run.brief if run.brief.endswith("\n") else run.brief + "\n"
        sys.stdout.write(text)


def cmd_recipe(
    args,
    *,
    registry: Any | None = None,
    backend_registry: Any | None = None,
) -> int:
    recipes = _recipe_registry(registry)

    if getattr(args, "recipe_action", "") == "list":
        return _print_listing(recipes, bool(getattr(args, "json", False)))

    try:
        spec = recipes.get(args.recipe_id)
    except UnknownRecipeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    engine = str(getattr(args, "engine", "") or "native").strip() or "native"
    try:
        run = run_recipe(
            spec, registry=_backend_registry(backend_registry), engine=engine,
            dry_run=bool(getattr(args, "dry_run", False)))
    except UnknownRuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("recipe run: interrupted", file=sys.stderr)
        return 130

    as_json = bool(getattr(args, "json", False))
    if run.cancelled:
        if as_json:
            _print_run(run, True)
        print("recipe run: interrupted", file=sys.stderr)
        return 130
    if run.refusal:
        if as_json:
            _print_run(run, True)
        else:
            prefix = f"[{run.refusal}] " if run.refusal else ""
            print(f"recipe run: {prefix}{run.hint or run.refusal}", file=sys.stderr)
        return 2
    if run.failed:
        if as_json:
            _print_run(run, True)
        else:
            print("recipe run: the turn failed to produce a brief", file=sys.stderr)
        return 1

    _print_run(run, as_json)
    return 0
