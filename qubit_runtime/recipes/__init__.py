"""Qubit recipes: bounded, typed briefs executed through the RuntimePort."""
from __future__ import annotations

from recipes.registry import RecipeRegistry, UnknownRecipeError, build_recipe_registry
from recipes.runner import RecipeInputError, RecipeRun, build_prompt, normalize_inputs, run_recipe
from recipes.sections import (
    AVAILABLE,
    UNAVAILABLE,
    SectionResult,
    available,
    unavailable,
)
from recipes.spec import RecipeSpec

__all__ = [
    "AVAILABLE",
    "UNAVAILABLE",
    "RecipeInputError",
    "RecipeRegistry",
    "RecipeRun",
    "RecipeSpec",
    "SectionResult",
    "UnknownRecipeError",
    "available",
    "build_prompt",
    "build_recipe_registry",
    "normalize_inputs",
    "run_recipe",
    "unavailable",
]
