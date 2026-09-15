"""Hatchling build hook — package the frontend tree without dev-only files.

``pyproject.toml`` no longer maps ``frontend/`` as a raw ``force-include``
directory: hatchling walks such a mapping recursively and is deliberately
**not** filtered by ``exclude``, so a wheel built from a working tree that
contains ``frontend/hermes-web/node_modules`` would embed ~20k dev files — and
their placeholder user-home example paths — into the shipped artifact.

This hook adds ``frontend/`` as an explicit per-file ``force_include`` mapping
with the dev/build-only directories pruned. It is a no-op on a clean clone (no
``node_modules`` present) and keeps every runtime-served file
(``hermes-dist``, ``hermes-qubit``, the top-level HTML/CSS/JS pages).
"""
from __future__ import annotations

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# Directory names that are dev/build-only at ANY depth. ``node_modules`` is the
# big one; the dot-caches are editor/bundler scratch. Plugin bundles under
# ``public/plugins/<name>/dist/`` are runtime assets, so a bare ``dist`` name is
# NOT pruned — only the frontend package build outputs are (see _PRUNED_PREFIXES).
_PRUNED_ANY_DEPTH = frozenset({
    "node_modules", ".vite", ".turbo", ".cache", ".parcel-cache", ".nyc_output",
})

# Dev/build outputs at specific frontend-relative prefixes.
_PRUNED_PREFIXES = (
    "hermes-web/dist",
    "hermes-web/coverage",
    "hermes-shared/dist",
    "hermes-shared/coverage",
)


def _is_pruned(rel) -> bool:
    if any(part in _PRUNED_ANY_DEPTH for part in rel.parts):
        return True
    posix = rel.as_posix()
    return any(posix == p or posix.startswith(p + "/")
               for p in _PRUNED_PREFIXES)


class CustomBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        root = Path(self.root) / "frontend"
        if not root.is_dir():
            return
        force = build_data.setdefault("force_include", {})
        added = 0
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if _is_pruned(rel):
                continue
            force[f"frontend/{rel.as_posix()}"] = \
                f"voicebridge/_data/frontend/{rel.as_posix()}"
            added += 1
        if added:
            self.app.display_info(
                f"[qubit] frontend force-include: {added} file(s); "
                f"pruned any-depth {sorted(_PRUNED_ANY_DEPTH)} and prefixes "
                f"{list(_PRUNED_PREFIXES)}")
