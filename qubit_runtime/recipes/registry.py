"""Recipe registry: named recipes are looked up, never guessed."""
from __future__ import annotations

from recipes.spec import RecipeSpec


class UnknownRecipeError(ValueError):
    """No recipe is registered under this id."""

    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = available
        super().__init__(
            f"Unknown recipe {name!r}; available: {', '.join(available) or '(none)'}")


class RecipeRegistry:
    def __init__(self) -> None:
        self._recipes: dict[str, RecipeSpec] = {}

    def register(self, spec: RecipeSpec) -> None:
        if spec.id in self._recipes:
            raise ValueError(f"recipe {spec.id!r} is already registered")
        self._recipes[spec.id] = spec

    def get(self, name: str) -> RecipeSpec:
        key = str(name or "").strip()
        if key not in self._recipes:
            raise UnknownRecipeError(key, self.available())
        return self._recipes[key]

    def available(self) -> list[str]:
        return sorted(self._recipes)

    def specs(self) -> tuple[RecipeSpec, ...]:
        return tuple(self._recipes[name] for name in self.available())


def build_recipe_registry() -> RecipeRegistry:
    from recipes.morning_digest import MORNING_DIGEST

    registry = RecipeRegistry()
    registry.register(MORNING_DIGEST)
    return registry
