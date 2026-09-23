"""``qubit plugins validate`` — admission checks for a plugin directory.

This is the command the plugin-catalog admission CI (and the
``.github/actions/plugin-validate`` composite action) runs against a
candidate plugin. It performs static manifest checks plus a
subprocess-isolated capability probe: the plugin is imported and its
``register(ctx)`` called against a minimal recording stub context in a
scratch child process (with a throwaway ``QUBIT_HOME``), so a crashing or
malicious plugin cannot take down the CLI, and the *actually registered*
tools/hooks/middleware are compared against the manifest's declared
``provides_*`` lists.

Ported from the read-only reference ``hermes_cli/plugin_validate.py`` onto the
vendored 0.21.1 model: the manifest contract, kinds, requires/dep handling and
the probe stub all come from :mod:`qubit_cli.plugins_manifest` /
:mod:`qubit_cli.plugins` — no catalog model is invented here.

Two deliberate CGraph-model adaptations, documented as such:

* ``requires_hermes`` is validated through the vendored manifest key
  (``requires_hermes`` is an accepted alias of the ``hermes`` manifest field)
  with the ``packaging`` version parser, instead of a reference-only
  ``hermes_cli.plugins_manifest._VERSION_COMPARATOR_RE`` that does not exist in
  0.21.1.
* ``--install-deps`` never installs: the vendored invariant is that
  ``python_dependencies`` are validated and surfaced ONLY, never
  auto-installed (``qubit_cli/plugins_manifest.py``: "VALIDATED AND SURFACED
  ONLY — Qubit never auto-installs these"). The flag surfaces missing declared
  deps with the exact manual ``pip install`` command.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from qubit_cli.plugin_validate_desktop import check_desktop_surface

_UPPER_SNAKE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
# The vendored manifest model owns the accepted config-schema types; import it lazily so a broken
# yaml/import never blocks the CLI from loading and reporting a clean error.
_PROBE_TIMEOUT = 30
# Sentinel printed by the probe subprocess; must be distinctive enough not to collide with plugin
# stdout. Renamed from the reference's ``HERMES_VALIDATE_JSON:`` along with the rebrand.
_PROBE_SENTINEL = "QUBIT_VALIDATE_JSON:"
# The distribution Qubit itself installs as; a plugin depending on it is always satisfied by the
# running checkout (installing a second copy from an index would clobber the runtime).
try:
    from importlib.metadata import distribution as _distribution

    _QUBIT_DIST_NAME = (_distribution("qubit-voicebridge").metadata["Name"] or "").strip()
except Exception:  # pragma: no cover - metadata always present in a real install
    _QUBIT_DIST_NAME = "qubit-voicebridge"


@dataclass
class ValidationReport:
    """Result of validating one plugin directory."""

    checks: List[Tuple[str, bool, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def failures(self) -> List[str]:
        return [detail or name for name, ok, detail in self.checks if not ok]

    @property
    def ok(self) -> bool:
        return all(ok for _name, ok, _detail in self.checks)

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [
                {"name": name, "ok": ok, "detail": detail}
                for name, ok, detail in self.checks
            ],
            "warnings": list(self.warnings),
        }


# ─── Static checks ───────────────────────────────────────────────────────────


def _manifest_requires_spec(manifest: dict) -> str:
    """The declared Qubit-version spec: ``requires_hermes`` (catalog/CI spelling) or the vendored
    manifest field ``hermes`` (``_KNOWN_MANIFEST_FIELDS``), whichever is present."""
    return str(manifest.get("requires_hermes") or manifest.get("hermes") or "").strip()


def _requires_spec_valid(spec: str) -> bool:
    """Strictly validate a ``requires_hermes``/``hermes`` spec.

    Unlike the loader (permissive at load time), validation REJECTS clauses whose
    version segment doesn't parse — a typo'd spec should fail admission, not
    silently gate nothing.
    """
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion, Version

    for clause in spec.split(","):
        clause = clause.strip()
        if not clause:
            continue
        try:
            SpecifierSet(clause)
            continue
        except InvalidSpecifier:
            pass
        # Not a valid specifier clause: still reject outright unless it is a bare version.
        try:
            Version(clause.lstrip("=<>!~ "))
        except InvalidVersion:
            return False
    return True


def _check_manifest_fields(report: ValidationReport, manifest: dict) -> None:
    missing = [
        f for f in ("name", "version", "description") if not manifest.get(f)
    ]
    if missing:
        report.add(
            "manifest fields",
            False,
            f"plugin.yaml missing required field(s): {', '.join(missing)}",
        )
    else:
        report.add("manifest fields", True, "name, version, description present")


def _check_manifest_version(report: ValidationReport, manifest: dict) -> None:
    """``manifest_version`` / ``api_version`` sanity, matching the vendored loader's numeric contract."""
    from qubit_cli.plugins_manifest import SUPPORTED_MANIFEST_VERSION

    raw = manifest.get("manifest_version")
    if raw is None:
        report.add("manifest version", True, "not declared (v1)")
        return
    try:
        mv = int(raw)
    except (TypeError, ValueError):
        report.add(
            "manifest version", False,
            f"manifest_version must be an integer (got {raw!r})",
        )
        return
    if mv > SUPPORTED_MANIFEST_VERSION:
        report.warn(
            f"manifest_version {mv} is newer than this Qubit supports "
            f"({SUPPORTED_MANIFEST_VERSION}); unknown fields are ignored at load"
        )
        report.add("manifest version", True, f"v{mv} (newer; forward-compatible)")
        return
    report.add("manifest version", True, f"v{mv}")


def _check_kind(report: ValidationReport, manifest: dict) -> None:
    """``kind`` must be one of the vendored ``_VALID_PLUGIN_KINDS`` (or absent = standalone)."""
    from qubit_cli.plugins_manifest import _VALID_PLUGIN_KINDS

    raw = manifest.get("kind")
    if raw is None:
        report.add("kind", True, "not declared (standalone)")
        return
    kind = str(raw).strip().lower()
    if kind not in _VALID_PLUGIN_KINDS:
        report.add(
            "kind",
            False,
            f"unknown kind {raw!r} (valid: {', '.join(sorted(_VALID_PLUGIN_KINDS))})",
        )
        return
    report.add("kind", True, kind)


def _check_requires_qubit(report: ValidationReport, manifest: dict) -> None:
    spec = _manifest_requires_spec(manifest)
    if not spec:
        report.add("requires_hermes", True, "not declared")
        return
    if _requires_spec_valid(spec):
        report.add("requires_hermes", True, f"spec {spec!r} parses")
    else:
        report.add(
            "requires_hermes",
            False,
            f"requires_hermes spec {spec!r} does not parse "
            "(expected e.g. \">=0.19\" or \">=0.19, <1.0\")",
        )


def _check_config_spec(report: ValidationReport, manifest: dict) -> None:
    """Validate the manifest ``config_schema`` mapping against the vendored accepted types."""
    from qubit_cli.plugins_manifest import _CONFIG_SCHEMA_TYPES

    raw = manifest.get("config_schema")
    if raw in (None, [], {}):
        report.add("config schema", True, "not declared")
        return
    problems: List[str] = []
    if not isinstance(raw, dict):
        problems.append("config_schema: must be a mapping of key -> spec")
    else:
        for skey, spec in raw.items():
            if not isinstance(spec, dict):
                problems.append(
                    f"config_schema.{skey}: must be a mapping (e.g. {{type: str}})"
                )
                continue
            typ = spec.get("type")
            if typ is not None and str(typ).lower() not in _CONFIG_SCHEMA_TYPES:
                problems.append(
                    f"config_schema.{skey}: type must be one of "
                    f"{'/'.join(sorted(_CONFIG_SCHEMA_TYPES))}"
                )
            required = spec.get("required")
            if required is not None and not isinstance(required, bool):
                problems.append(
                    f"config_schema.{skey}: required must be a boolean"
                )
    if problems:
        report.add("config schema", False, "; ".join(problems))
    else:
        report.add("config schema", True, "shape valid")


def _check_requires_env(report: ValidationReport, manifest: dict) -> None:
    raw = manifest.get("requires_env") or []
    problems: List[str] = []
    if not isinstance(raw, list):
        problems.append("requires_env: must be a list")
        raw = []
    for i, entry in enumerate(raw):
        if isinstance(entry, str):
            name = entry
        elif isinstance(entry, dict):
            name = str(entry.get("name") or "")
        else:
            problems.append(f"requires_env[{i}]: must be a string or mapping")
            continue
        if not _UPPER_SNAKE_RE.match(name):
            problems.append(
                f"requires_env[{i}]: {name!r} is not UPPER_SNAKE_CASE"
            )
    if problems:
        report.add("requires_env", False, "; ".join(problems))
    else:
        report.add("requires_env", True, "all entries UPPER_SNAKE")


def _check_requires_plugins(report: ValidationReport, manifest: dict) -> None:
    """Declared plugin dependencies must be plugin-id strings or ``{id, version_range}`` mappings —
    the shape ``plugins_manifest._dependency_entry`` accepts at load time."""
    raw = manifest.get("requires_plugins")
    if raw in (None, [], {}):
        report.add("requires_plugins", True, "not declared")
        return
    problems: List[str] = []
    if not isinstance(raw, list):
        report.add("requires_plugins", False, "requires_plugins: must be a list")
        return
    for i, entry in enumerate(raw):
        if isinstance(entry, str):
            if not entry.strip():
                problems.append(f"requires_plugins[{i}]: id must be non-empty")
            continue
        if isinstance(entry, dict):
            if not (isinstance(entry.get("id"), str) and entry["id"].strip()):
                problems.append(
                    f"requires_plugins[{i}]: mapping must carry a non-empty 'id'"
                )
            continue
        problems.append(
            f"requires_plugins[{i}]: must be a plugin id string or "
            "{id, version_range} mapping"
        )
    if problems:
        report.add("requires_plugins", False, "; ".join(problems))
    else:
        report.add("requires_plugins", True, "all entries valid")


# ─── Capability probe (subprocess-isolated) ──────────────────────────────────

# Self-contained harness run in a scratch child process. Imports the plugin
# module using the same file-location mechanics PluginManager uses, calls
# register() against a recording stub ctx, and prints a sentinel-prefixed
# JSON line of what was actually registered. Deliberately imports NOTHING
# from Qubit so a hostile plugin only sees a bare interpreter — the one
# exception is `providers` for `kind: model-provider`, whose contract IS
# calling providers.register_provider at import.
_PROBE_SCRIPT = r"""
import importlib.util
import json
import sys

plugin_dir = sys.argv[1]
sentinel = sys.argv[2]
options = json.loads(sys.argv[3])
# Public method names of the real PluginContext, computed by the parent so the
# stub's attribute surface cannot drift from the class plugins run against.
context_methods = set(options["context_methods"])
provider_kind = options["kind"] == "model-provider"

recorded = {"tools": [], "hooks": [], "middleware": [], "commands": [], "providers": []}


class RecordingContext:
    plugin_config = {}
    profile_name = "default"
    plugin_id = "qubit_validate_probe_plugin"

    def register_tool(self, name, *args, **kwargs):
        recorded["tools"].append(str(name))

    def register_hook(self, hook_name, callback):
        recorded["hooks"].append(str(hook_name))

    def register_middleware(self, kind, callback):
        recorded["middleware"].append(str(kind))

    def register_command(self, name, *args, **kwargs):
        recorded["commands"].append(str(name))

    def register_cli_command(self, name, *args, **kwargs):
        recorded["commands"].append(str(name))

    def get_config(self, key, default=None):
        # Mirrors PluginContext.get_config with no config on disk: the DEFAULT, never None —
        # plugins do `int(ctx.get_config("timeout", 180))` in register().
        return default

    def __getattr__(self, name):
        # Any other REAL registration surface (platforms, providers, skills,
        # context engines, ...) is accepted as a no-op — the probe only audits
        # the declared-capability categories. Names the real PluginContext does
        # not have raise AttributeError exactly like it would; handing back a
        # callable made `getattr(ctx, "profile_path", None)` truthy and crashed
        # register() in the probe alone.
        if name in context_methods:
            def _noop(*args, **kwargs):
                return None

            return _noop
        raise AttributeError(name)


def emit(payload):
    print(sentinel + json.dumps(payload))


if provider_kind:
    # `kind: model-provider` plugins register at import via
    # providers.register_provider(ProviderProfile) — the PluginManager never
    # calls a register(ctx) on them (plugins_discovery skips the kind), so the
    # probe records that call instead of demanding an entry point that would
    # be dead code.
    try:
        import providers as _providers
    except Exception as exc:
        emit({"error": "model-provider probe could not import providers: %s" % exc})
        sys.exit(0)
    _real_register_provider = _providers.register_provider

    def _recording_register_provider(profile):
        recorded["providers"].append(str(getattr(profile, "name", profile)))
        return _real_register_provider(profile)

    _providers.register_provider = _recording_register_provider

try:
    spec = importlib.util.spec_from_file_location(
        "qubit_validate_probe_plugin",
        plugin_dir + "/__init__.py",
        submodule_search_locations=[plugin_dir],
    )
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [plugin_dir]
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
except Exception as exc:
    emit({"error": "import failed: %s" % exc})
    sys.exit(0)

if provider_kind:
    if not recorded["providers"]:
        emit({"error": "model-provider plugin registered no ProviderProfile at import"})
    else:
        emit(recorded)
    sys.exit(0)

register = getattr(module, "register", None)
if register is None:
    emit({"error": "no register() function"})
    sys.exit(0)

try:
    register(RecordingContext())
except Exception as exc:
    emit({"error": "register() raised: %s" % exc})
    sys.exit(0)

emit(recorded)
"""


def _probe_options(manifest: dict) -> dict:
    from qubit_cli.plugins import PluginContext

    return {
        "kind": str(manifest.get("kind") or ""),
        "context_methods": sorted(
            n for n in dir(PluginContext)
            if not n.startswith("_") and callable(getattr(PluginContext, n))
        ),
    }


def _run_capability_probe(plugin_dir: Path, manifest: dict) -> Tuple[Optional[dict], str]:
    """Run the recording probe in a scratch subprocess.

    Returns ``(recorded, error)`` — exactly one is meaningful: *recorded*
    is the ``{tools, hooks, middleware, commands, providers}`` dict on
    success, and *error* is a human-readable failure description otherwise.
    """
    with tempfile.TemporaryDirectory(prefix="qubit-validate-") as scratch:
        env = dict(os.environ)
        env["QUBIT_HOME"] = scratch
        # The probe is a bare interpreter (`python -c`): it inherits no sys.path
        # from the running CLI. `kind: model-provider` plugins import the
        # top-level `providers` package (the vendored fork root), so put the fork
        # root on PYTHONPATH — the same root the launcher's `ensure_path` uses.
        fork_root = str(Path(__file__).resolve().parent.parent)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = fork_root + (os.pathsep + existing if existing else "")
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    _PROBE_SCRIPT,
                    str(plugin_dir),
                    _PROBE_SENTINEL,
                    json.dumps(_probe_options(manifest)),
                ],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return None, f"capability probe timed out after {_PROBE_TIMEOUT}s"

    payload: Optional[dict] = None
    for line in (result.stdout or "").splitlines():
        if line.startswith(_PROBE_SENTINEL):
            try:
                payload = json.loads(line[len(_PROBE_SENTINEL):])
            except json.JSONDecodeError:
                payload = None

    if payload is None:
        err = (result.stderr or "").strip()
        return None, (
            "capability probe produced no result "
            f"(exit {result.returncode})" + (f": {err}" if err else "")
        )
    if "error" in payload:
        return None, str(payload["error"])
    return payload, ""


def _declared_list(manifest: dict, key: str) -> List[str]:
    raw = manifest.get(key) or []
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str)]


def _check_capabilities(
    report: ValidationReport, manifest: dict, plugin_dir: Path
) -> Optional[dict]:
    """Probe actual registrations and diff against declared capabilities.

    Returns the recorded dict (for the built-in collision check) or None
    when the probe failed / was skipped.
    """
    if not (plugin_dir / "__init__.py").is_file():
        report.warn(
            "no __init__.py — capability probe skipped (manifest-only plugin)"
        )
        report.add("capability probe", True, "skipped (no __init__.py)")
        return None

    recorded, error = _run_capability_probe(plugin_dir, manifest)
    if recorded is None:
        report.add("capability probe", False, error)
        return None
    if recorded.get("providers"):
        report.add(
            "capability probe", True,
            "import registered provider(s): " + ", ".join(recorded["providers"]),
        )
    else:
        report.add("capability probe", True, "register() ran in isolation")

    for kind, manifest_key in (
        ("tools", "provides_tools"),
        ("hooks", "provides_hooks"),
        ("middleware", "provides_middleware"),
    ):
        declared = set(_declared_list(manifest, manifest_key))
        actual = set(recorded.get(kind) or [])
        undeclared = sorted(actual - declared)
        unregistered = sorted(declared - actual)
        if undeclared:
            report.add(
                f"declared {kind}",
                False,
                f"undeclared {kind} registered (not in {manifest_key}): "
                f"{', '.join(undeclared)}",
            )
        else:
            report.add(f"declared {kind}", True, "matches registrations")
        if unregistered:
            report.warn(
                f"{manifest_key} declares {', '.join(unregistered)} "
                f"but register() did not register them"
            )
    return recorded


def _builtin_tool_names() -> List[str]:
    """Return the built-in tool registry names (discovery-timing safe).

    ``tools.registry`` starts empty — built-in tool modules self-register on
    import, so we must run ``discover_builtin_tools()`` first (idempotent).
    """
    try:
        from tools.registry import discover_builtin_tools, registry

        discover_builtin_tools()
        return list(registry.get_all_tool_names())
    except Exception:
        return []


def _check_builtin_collisions(
    report: ValidationReport, manifest: dict, recorded: Optional[dict]
) -> None:
    candidate_tools = set(_declared_list(manifest, "provides_tools"))
    if recorded:
        candidate_tools.update(recorded.get("tools") or [])
    if not candidate_tools:
        report.add("built-in tool collisions", True, "no tools to check")
        return
    builtin = set(_builtin_tool_names())
    collisions = sorted(candidate_tools & builtin)
    if collisions:
        report.add(
            "built-in tool collisions",
            False,
            "tool name(s) collide with built-in tools: "
            f"{', '.join(collisions)}",
        )
    else:
        report.add("built-in tool collisions", True, "no collisions")


# ─── Entry point ─────────────────────────────────────────────────────────────


def validate_plugin_dir(plugin_dir: Path, *, install_deps: bool = False) -> ValidationReport:
    """Run every admission check against *plugin_dir* and return the report.

    ``install_deps`` is accepted for surface parity with the reference CLI; the
    vendored model never auto-installs plugin dependencies, so it only affects
    the ``python dependencies`` detail (missing deps are surfaced with the
    exact manual command). See the module docstring.
    """
    report = ValidationReport()
    plugin_dir = Path(plugin_dir)

    if not plugin_dir.is_dir():
        report.add(
            "plugin directory", False, f"{plugin_dir} is not a directory"
        )
        return report

    manifest_file = plugin_dir / "plugin.yaml"
    if not manifest_file.is_file():
        manifest_file = plugin_dir / "plugin.yml"
    if not manifest_file.is_file():
        # Portable Agent Plugins v1: a plugin.json-only package is admissible —
        # validated through the portable manifest reader. When a package carries
        # both manifests the native plugin.yaml always wins (this branch is only
        # reached when no native manifest exists).
        portable_file = plugin_dir / "plugin.json"
        if portable_file.is_file():
            return _validate_portable_plugin(report, plugin_dir)
        report.add(
            "manifest", False,
            "no plugin.yaml (or portable plugin.json) in the plugin directory",
        )
        return report

    import yaml

    try:
        manifest = yaml.safe_load(
            manifest_file.read_text(encoding="utf-8")
        )
    except Exception as exc:
        report.add("manifest", False, f"plugin.yaml failed to parse: {exc}")
        return report
    if not isinstance(manifest, dict):
        report.add("manifest", False, "plugin.yaml must be a mapping")
        return report
    report.add("manifest", True, "plugin.yaml parses")

    _check_manifest_fields(report, manifest)
    _check_manifest_version(report, manifest)
    _check_kind(report, manifest)
    _check_requires_qubit(report, manifest)
    _check_config_spec(report, manifest)
    _check_requires_env(report, manifest)
    _check_requires_plugins(report, manifest)
    _check_loadable(report, plugin_dir)
    _check_python_dependencies(report, plugin_dir, install_deps=install_deps)
    recorded = _check_capabilities(report, manifest, plugin_dir)
    _check_builtin_collisions(report, manifest, recorded)
    _check_security_scan(report, plugin_dir)
    check_desktop_surface(report, plugin_dir)
    return report


_LOADABLE_ENTRYPOINTS = ("__init__.py", "desktop/plugin.js", "plugin.json")


def _check_loadable(report: ValidationReport, plugin_dir: Path) -> None:
    """A plugin.yaml with nothing beside it that Qubit can load (no ``register()`` module, no
    desktop bundle, no portable manifest) installs "successfully" and does nothing — a pip-layout
    repo whose code lives under ``src/`` behind an entry point is the usual shape."""
    present = [rel for rel in _LOADABLE_ENTRYPOINTS if (plugin_dir / rel).is_file()]
    report.add(
        "loadable", bool(present),
        f"entry: {', '.join(present)}" if present else
        "nothing to load: no __init__.py, desktop/plugin.js or plugin.json beside plugin.yaml "
        "(pip-layout packages need a directory-plugin wrapper with a pyproject.toml declaring the deps)",
    )


def _declared_python_specs(plugin_dir: Path, manifest: dict) -> Tuple[List[str], str, bool]:
    """``(specs, source, external)`` from ``pyproject [project].dependencies`` (preferred) or the
    manifest ``python_dependencies`` / ``pip_dependencies`` — never installs anything."""
    specs: List[str] = []
    source = ""
    pyproject = plugin_dir / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib

            document = tomllib.loads(pyproject.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise ValueError(f"{pyproject}: could not parse pyproject.toml: {exc}") from exc
        raw = document.get("project", {}).get("dependencies", [])
        if not isinstance(raw, list) or any(not isinstance(s, str) for s in raw):
            raise ValueError(f"{pyproject}: [project].dependencies must be a list of strings")
        specs = [s.strip() for s in raw if s.strip()]
        source = "pyproject"
    if not specs:
        for key in ("python_dependencies", "pip_dependencies"):
            raw = manifest.get(key) or []
            if not isinstance(raw, list) or any(not isinstance(v, str) for v in raw):
                raise ValueError(f"{key} must be a list of requirement strings")
            specs.extend(v.strip() for v in raw if v.strip())
        if specs:
            source = "manifest"
    specs = list(dict.fromkeys(specs))
    external = str(manifest.get("python_runtime") or "").strip().lower() == "external"
    return specs, source, external


def _check_python_dependencies(
    report: ValidationReport, plugin_dir: Path, *, install_deps: bool = False
) -> None:
    """Declared deps must be well-formed PEP 508 specs the installer would accept. A plugin opting
    out with ``python_runtime: external`` declares none. Qubit never auto-installs them."""
    try:
        specs, source, external = _declared_python_specs(plugin_dir, _read_manifest_dict(plugin_dir))
    except ValueError as exc:
        report.add("python dependencies", False, f"declaration invalid: {exc}")
        return
    if external:
        report.add("python dependencies", True, "external runtime (plugin manages its own)")
        return
    if not specs:
        report.add("python dependencies", True, "none declared")
        return
    rejected = [s for s in specs if not _spec_is_safe(s)]
    detail = f"{len(specs)} declared from {source}"
    report.add(
        "python dependencies",
        not rejected,
        f"unsafe spec(s): {', '.join(rejected)}" if rejected else detail,
    )
    if install_deps:
        missing = _missing_requirements(specs)
        if missing:
            report.warn(
                "python dependencies not installed: " + ", ".join(missing)
                + " — Qubit never auto-installs plugin dependencies; install manually: "
                + " ".join(f"'{m}'" for m in missing)
            )
    else:
        report.warn(
            "python dependencies are declared only (never auto-installed); "
            f"review and install manually if needed: {' '.join(f'{s!r}' for s in specs)}"
        )


def _read_manifest_dict(plugin_dir: Path) -> dict:
    """Read the native manifest as a plain dict (the validator already parsed it, but dep checks run
    for the portable path too). Missing/broken → ``{}``."""
    for name in ("plugin.yaml", "plugin.yml"):
        path = plugin_dir / name
        if path.is_file():
            try:
                import yaml

                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
    return {}


def _spec_is_safe(spec: str) -> bool:
    """Reject pip specs that contain URLs, paths, or shell metacharacters (the install seam's own
    predicate, reused so admission and install agree)."""
    from tools.lazy_deps import _spec_is_safe as safe
    return safe(spec)


def _missing_requirements(specs: List[str]) -> List[str]:
    """Declared specs whose distribution is not importable by name here (best effort)."""
    from importlib.metadata import PackageNotFoundError, version
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name

    missing: List[str] = []
    for spec in specs:
        try:
            req = Requirement(spec)
        except InvalidRequirement:
            missing.append(spec)
            continue
        if req.url or canonicalize_name(req.name) == canonicalize_name(_QUBIT_DIST_NAME):
            continue
        try:
            have = version(req.name)
        except PackageNotFoundError:
            missing.append(spec)
            continue
        except Exception:
            continue
        if req.specifier and have not in req.specifier:
            missing.append(f"{spec} (installed {have})")
    return missing


def _check_security_scan(report: ValidationReport, plugin_dir: Path) -> None:
    """Run the install-time scanner at admission, so a pin a reviewer approves is one the
    installer will accept: ``dangerous`` fails the entry; ``caution`` findings surface as
    warnings for the reviewer (the installer trusts them once the pin is merged)."""
    from tools.plugin_guard import scan_plugin

    result = scan_plugin(plugin_dir)
    flagged = [f for f in result.findings if f.severity in ("critical", "high")]
    summary = ", ".join(sorted({f"{f.pattern_id} ({Path(f.file).name}:{f.line})" for f in flagged})) or "no findings"
    if result.verdict == "dangerous":
        report.add("security scan", False, f"dangerous: {summary}")
        return
    report.add("security scan", True, result.verdict)
    if result.verdict == "caution":
        report.warn(f"security scan caution: {summary}")


def _validate_portable_plugin(report: ValidationReport, plugin_dir: Path) -> ValidationReport:
    """Admission checks for a portable Agent Plugins v1 (plugin.json) package.

    Portable packages have no register() entry point, so the capability
    probe does not apply; validation is the manifest reader's own
    diagnostics (schema shape, name, supported subset).
    """
    try:
        from qubit_cli.agent_plugins import read_agent_plugin_manifest

        manifest, diagnostics = read_agent_plugin_manifest(plugin_dir)
    except Exception as exc:
        report.add("portable manifest", False, f"plugin.json failed validation: {exc}")
        return report

    for diag in diagnostics:
        scope = getattr(diag, "scope", "")
        message = getattr(diag, "message", str(diag))
        report.warnings.append(f"{scope}: {message}" if scope else message)

    report.add("portable manifest", True, "plugin.json parses (Agent Plugins v1)")
    name = str(manifest.get("name") or "").strip()
    report.add(
        "manifest fields",
        bool(name),
        "name present" if name else "plugin.json missing required 'name'",
    )
    _check_loadable(report, plugin_dir)
    _check_python_dependencies(report, plugin_dir)
    _check_security_scan(report, plugin_dir)
    check_desktop_surface(report, plugin_dir)
    return report
