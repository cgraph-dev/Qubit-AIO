"""Hatchling build hook — package the frontend/runtime trees without dev files.

``pyproject.toml`` maps several source directories as ``force-include`` entries
(e.g. the vendored ``qubit_runtime/ui-tui`` runtime and the frontend tree).
Hatchling's force-include recursion walks a directory mapping and only prunes
the names in ``hatchling.builders.constants.EXCLUDED_DIRECTORIES``; that set
does **not** include ``node_modules``. A working tree therefore sweeps its
git-ignored dev dependencies into the wheel/sdist (the clean-install gate
catches this as "wheel swept N node_modules members").

This hook:

1. stages ``frontend/`` as explicit per-file ``force_include`` entries under
   ``voicebridge/_data/frontend`` (the wheel reparents that tree; a raw
   directory mapping would also carry `node_modules`), and
2. extends hatchling's directory-exclusion set for the build so the vendored
   runtime directory mappings (e.g. ``qubit_runtime/ui-tui``) do not recurse
   into ``node_modules`` or bundler/editor scratch trees.

It is a no-op on a clean clone (no dev trees present) and keeps every
runtime-served file. It touches no static ``pyproject.toml`` mapping, so the
packaging contract tests that read the mapping table are unaffected.

A public release candidate exported by ``scripts/export_public_tree.py``
intentionally omits some internal docs/provenance sources that the full wheel
force-includes.  In that tree only (detected by the exported manifest) the
hook drops those known sources so the snapshot can build; any other missing
source still raises.  See ``scripts/public_export_packaging.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

sys.path.insert(0, str(Path(__file__).resolve().parent))
from public_export_packaging import drop_missing_optional_sources  # noqa: E402

# Directory names that are dev/build-only at ANY depth. ``node_modules`` is the
# big one; the dot-caches are editor/bundler scratch. Plugin bundles under
# ``public/plugins/<name>/dist/`` are runtime assets, so a bare ``dist`` name is
# NOT pruned — only the frontend package build outputs are (see _PRUNED_PREFIXES).
_PRUNED_ANY_DEPTH = frozenset({
    "node_modules", ".vite", ".turbo", ".cache", ".parcel-cache", ".nyc_output",
})

# Dev/build outputs at specific frontend-relative prefixes.
_PRUNED_PREFIXES = (
    "qubit-web/dist",
    "qubit-web/coverage",
    "qubit-shared/dist",
    "qubit-shared/coverage",
)


def _is_pruned(rel) -> bool:
    if any(part in _PRUNED_ANY_DEPTH for part in rel.parts):
        return True
    posix = rel.as_posix()
    return any(posix == p or posix.startswith(p + "/")
               for p in _PRUNED_PREFIXES)


def _extend_hatchling_excluded_directories() -> None:
    """Teach hatchling's force-include recursion to skip dev-only trees.

    ``recurse_forced_files`` reads the module-level ``EXCLUDED_DIRECTORIES``
    name at call time, so rebinding it here (before the build) makes every
    directory ``force-include`` entry prune ``node_modules`` and scratch
    caches without editing the static mapping table.
    """
    import hatchling.builders.plugin.interface as _iface

    _iface.EXCLUDED_DIRECTORIES = frozenset(
        set(_iface.EXCLUDED_DIRECTORIES) | _PRUNED_ANY_DEPTH)


class CustomBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        _extend_hatchling_excluded_directories()
        root = Path(self.root)
        force = build_data.setdefault("force_include", {})
        added = 0

        # Static ``pyproject.toml`` force-include entries live in the builder
        # config, not in ``build_data`` (which can only add/override), so wrap
        # ``get_force_include`` to drop the public export's known omissions.
        config = self.build_config
        if not getattr(config, "_qubit_public_export_filtered", False):
            original = config.get_force_include

            def _filtered_force_include():
                mapping = original()
                dropped = drop_missing_optional_sources(root, mapping)
                if dropped:
                    self.app.display_info(
                        "[qubit] public export omits intentionally-internal "
                        f"force-include source(s): {dropped}")
                return mapping

            config.get_force_include = _filtered_force_include
            config._qubit_public_export_filtered = True

        if self.target_name == "wheel":
            # The wheel reparents the frontend tree under ``_data``; stage it
            # per-file because it has no static directory mapping to prune.
            frontend = root / "frontend"
            if frontend.is_dir():
                for path in sorted(frontend.rglob("*")):
                    if not path.is_file():
                        continue
                    rel = path.relative_to(frontend)
                    if _is_pruned(rel):
                        continue
                    force[f"frontend/{rel.as_posix()}"] = \
                        f"voicebridge/_data/frontend/{rel.as_posix()}"
                    added += 1

        if added:
            self.app.display_info(
                f"[qubit] force-include staged {added} file(s); "
                f"pruned any-depth {sorted(_PRUNED_ANY_DEPTH)} and prefixes "
                f"{list(_PRUNED_PREFIXES)}")
