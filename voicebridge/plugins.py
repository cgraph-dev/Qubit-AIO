"""Qubit plugin installs behind the Qubit skill contract.

Qubit installs plugins from a Git URL, ``owner/repo`` shorthand, or a
community-index name. Qubit maps that surface onto the existing 7E
skill registry instead of fetching third-party code:

* a local, already-reviewed Qubit-schema manifest file installs exactly like
  ``voicebridge skills install``: validated, staged DISABLED, enabled only
  with a second explicit confirmation (installs stay disabled until approved);
* a remote identifier (URL, ``owner/repo``, bare index name) is refused with
  a typed ``BLOCKED_REMOTE_PLUGIN`` error: nothing is fetched, cloned, or
  executed until the source passes allowlist review with a pinned hash and
  explicit confirmation (reviewed-code policy);
* a missing local path is a typed ``PLUGIN_MANIFEST_IO`` error, never a
  traceback;
* manifests larger than :data:`MAX_MANIFEST_BYTES` are refused before reading
  (bounded I/O);
* every refusal echoes user input only through :func:`redact_text` (remote
  identifiers routinely carry ``user:token@host`` or ``?token=`` material);
* mutating operations refuse while the emergency stop is latched
  (:func:`estop_engaged`, same durable :class:`PowerControlStore` the
  dashboard, voice, and broker paths observe).

Execution of an installed plugin stays broker-governed: ``staged``/disabled
records grant no authority (proven in ``tests/test_plugins8tf.py`` against
the 7E store). Cancellation: these are fast bounded local registry writes;
there is no long-running work to cancel beyond the process-level
KeyboardInterrupt path every CLI command already has.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

MAX_MANIFEST_BYTES = 262144  # 256 KiB: manifests are metadata, never payloads.

REMOTE_CODE = "BLOCKED_REMOTE_PLUGIN"
ESTOP_CODE = "PLUGIN_ESTOP"
IO_CODE = "PLUGIN_MANIFEST_IO"
TOO_LARGE_CODE = "PLUGIN_MANIFEST_TOO_LARGE"
INVALID_CODE = "PLUGIN_MANIFEST_INVALID"

_QUBIT_EXPLANATION = (
    "Qubit Agent cannot do this: third-party plugins need allowlist review, "
    "hash pin, and explicit confirmation. "
    "Install a reviewed local manifest with "
    "`voicebridge skills install <manifest.json> --confirm` "
    "(staged disabled; enable separately with --confirm)."
)


class PluginInstallError(Exception):
    """Typed plugin-install failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def redact_spec(text: str) -> str:
    """Redact a user-supplied identifier/path before echoing it in output."""
    return _redact(text)


_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BARE_INDEX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")


def classify_install_source(spec: str) -> Dict[str, Any]:
    """Classify a ``plugins install`` argument without touching the network.

    Returns one of:
    * ``{"kind": "local-file", "path": Path}`` — an existing local file
      (existing files always win over remote-looking shapes);
    * ``{"kind": "missing-file", "path": str}`` — path-shaped but absent
      (absolute, ``./``/``~/``-rooted, ``*.json``, multi-segment);
    * ``{"kind": "remote", "identifier": str}`` — Git URL, ``owner/repo``
      shorthand, or bare community-index name (remote fetch is never
      attempted).
    """
    raw = (spec or "").strip()
    if not raw:
        return {"kind": "missing-file", "path": raw}
    lowered = raw.lower()
    if Path(raw).expanduser().is_file():
        return {"kind": "local-file", "path": Path(raw).expanduser()}
    if "://" in raw or lowered.startswith(("git@", "ssh://", "git://")):
        return {"kind": "remote", "identifier": raw}
    if raw.startswith((".", "/", "~")) or lowered.endswith(".json") \
            or raw.count("/") > 1:
        return {"kind": "missing-file", "path": raw}
    if _OWNER_REPO_RE.match(raw):
        return {"kind": "remote", "identifier": raw}
    if _BARE_INDEX_RE.match(raw) and not lowered.endswith(".json"):
        # Bare community-index name (resolved remotely on explicit request).
        return {"kind": "remote", "identifier": raw}
    return {"kind": "missing-file", "path": raw}


def refuse_remote(identifier: str) -> Dict[str, Any]:
    """Typed refusal for remote plugin identifiers (nothing fetched)."""
    return {"state": "BLOCKED", "code": REMOTE_CODE,
            "identifier": _redact(identifier),
            "explanation": _QUBIT_EXPLANATION,
            "owner": "qubit-policy",
            "rollback": "nothing installed or enabled; nothing to roll back"}


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": ESTOP_CODE, "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop "
                           "is latched; reset it before changing plugins.",
            "owner": "qubit-policy",
            "rollback": "nothing installed or enabled; nothing to roll back"}


def estop_engaged(cfg: Any) -> bool:
    """True when the durable emergency-stop/global-disable latch is set.

    Read-only probe of the same :class:`PowerControlStore` the dashboard,
    voice, broker, and runner paths observe. Fails closed: an unreadable
    store reports engaged (its ``load()`` already fails closed the same way).
    """
    try:
        from .power import power_control_store

        state = power_control_store(cfg).load()
    except Exception:
        return True
    try:
        return bool(state.get("emergency_stop") or state.get("global_disable"))
    except Exception:
        return True


def load_manifest_bytes(path: Path) -> bytes:
    """Read a manifest file with a hard size cap (bounded I/O)."""
    try:
        size = Path(path).stat().st_size
    except OSError as exc:
        raise PluginInstallError(
            IO_CODE, f"cannot read manifest {str(path)[:160]}: {exc}") from exc
    if size > MAX_MANIFEST_BYTES:
        raise PluginInstallError(
            TOO_LARGE_CODE,
            f"manifest is {size} bytes (limit {MAX_MANIFEST_BYTES}); "
            f"manifests are metadata, never payloads")
    try:
        with open(path, "rb") as fh:
            return fh.read(MAX_MANIFEST_BYTES + 1)
    except OSError as exc:
        raise PluginInstallError(
            IO_CODE, f"cannot read manifest {str(path)[:160]}: {exc}") from exc
