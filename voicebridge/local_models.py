"""Qubit local-models surface (Qubit ``web_routers/local_models.py`` parity).

Upstream Qubit exposed a dashboard window into a managed llama.cpp
runtime: status/hardware/catalog/jobs reads plus install/download/sideload/
server/eject/activate/quickstart writes that pulled binaries and GGUF files
from the network.

Qubit has **no** managed local-runtime downloader. The Qubit-native local
model is the resident Ollama ``qubit-brain`` contract
(:mod:`voicebridge.local_llm`, :mod:`voicebridge.runner_contract`), and any
runtime/image provisioning is the pinned, confined, brokered workflow in
:mod:`voicebridge.runtime_images` (``qubit runtime ...``).

This module therefore:

* derives **reads** from real Qubit state only — the configured local-LLM
  section, the runner alias, the loopback Ollama model list, and the
  :mod:`voicebridge.runtime_inventory` / :mod:`voicebridge.vision` gauges.
  Unobservable facts are typed ``UNKNOWN``/``UNAVAILABLE`` — never invented;
* returns a **typed gate/refusal** for every write. Nothing is downloaded,
  started, ejected, activated, or written: direct local-model management is
  brokered through ``qubit runtime`` and the supervised opencode path.

No third-party dependencies, no egress (the only probe is loopback-only),
and no filesystem writes are performed anywhere in this module.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from . import runtime_inventory as _RIN
from .vram_guard import free_vram_mib

# --- typed codes -----------------------------------------------------------
UNKNOWN_CODE = "LOCAL_MODEL_UNKNOWN"
BAD_INPUT_CODE = "LOCAL_MODEL_BAD_INPUT"
BROKERED_CODE = "LOCAL_MODEL_BROKERED"
CONFIRM_CODE = "LOCAL_MODEL_CONFIRM_REQUIRED"
UNAVAILABLE_CODE = "LOCAL_MODEL_UNAVAILABLE"
ESTOP_CODE = "LOCAL_MODEL_ESTOP"
JOBS_EMPTY_CODE = "LOCAL_MODEL_JOBS_EMPTY"
TASK_UNAVAILABLE_CODE = "LOCAL_MODEL_TASK_UNAVAILABLE"
TASK_BAD_INPUT_CODE = "LOCAL_MODEL_TASK_BAD_INPUT"
TASK_SCHEMA = "qubit-local-model-task/1"

# --- schemas ---------------------------------------------------------------
STATUS_SCHEMA = "qubit-local-models-status/1"
HARDWARE_SCHEMA = "qubit-local-models-hardware/1"
CATALOG_SCHEMA = "qubit-local-models-catalog/1"
JOBS_SCHEMA = "qubit-local-models-jobs/1"
FALLBACK_SCHEMA = "qubit-local-models-fallback/1"

PROVIDER = "ollama"
BACKEND = "ollama-http"
DEFAULT_MODEL = "qubit-brain"
DEFAULT_URL = "http://127.0.0.1:11434"

# The probe is a bounded loopback read of the local runtime's model list.
# It never egresses: a non-loopback host is refused before any socket opens.
PROBE_TIMEOUT_S = 1.0
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

_BROKER_GUIDANCE = (
    "local-model runtime management is brokered in Qubit; use "
    "`qubit runtime status` and `qubit runtime install <image> --confirm` "
    "(pinned, confined, digest-verified), or the supervised opencode path. "
    "No download is performed or queued by this surface"
)


# --- small helpers ---------------------------------------------------------
def _get(cfg: Any):
    return (lambda *p, default=None: cfg.get(*p, default=default)) \
        if hasattr(cfg, "get") else (lambda *p, default=None: default)


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def redact_spec(text: str) -> str:
    """Redact user-supplied model ids/paths before echoing them."""
    return _redact(text)


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins/runtime surfaces (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def _refusal(op: str, code: str, detail: str, *,
             state: str = "REFUSED",
             guidance: str = _BROKER_GUIDANCE) -> Dict[str, Any]:
    return {"state": state, "code": code, "operation": op,
            "detail": _redact(detail), "owner": "qubit-policy",
            "guidance": guidance,
            "rollback": "nothing was downloaded, started, ejected, activated, "
                        "or written; nothing to roll back"}


# --- local state -----------------------------------------------------------
def _local_section(cfg: Any) -> Dict[str, Any]:
    get = _get(cfg)
    enabled = bool(get("intent", "local_llm", "enabled", default=True))
    model = str(get("intent", "local_llm", "model", default=DEFAULT_MODEL)
                or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    url = str(get("intent", "local_llm", "url", default=DEFAULT_URL)
              or DEFAULT_URL).strip() or DEFAULT_URL
    return {"enabled": enabled, "model": model, "url": url}


def _runner_alias(cfg: Any) -> str:
    get = _get(cfg)
    alias = str(get("intent", "runner", "alias", default="") or "").strip()
    return alias or _local_section(cfg)["model"]


def _loopback_base(url: str) -> Optional[str]:
    """scheme://host:port for a loopback HTTP url, else ``None`` (no egress)."""
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.hostname not in _LOOPBACK_HOSTS:
        return None
    if not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _probe_local(url: str, timeout_s: float = PROBE_TIMEOUT_S) -> Optional[Dict[str, Any]]:
    """Read the local runtime's model list over loopback.

    Returns ``{"state": "OK", "models": [...]}`` or ``None`` when the
    runtime is unreachable / not loopback. Never raises, never egresses, and
    never fabricates: a failed read is typed ``None``.
    """
    base = _loopback_base(url)
    if base is None:
        return None
    try:
        with urllib.request.urlopen(f"{base}/api/tags",
                                    timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None
    rows: List[Dict[str, Any]] = []
    raw = body.get("models", []) if isinstance(body, dict) else []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        if not name:
            continue
        size = item.get("size")
        try:
            size = int(size) if size is not None else None
        except (TypeError, ValueError):
            size = None
        rows.append({"id": name, "name": name, "size": size})
    return {"state": "OK", "models": rows}


def _observed_models(cfg: Any) -> List[Dict[str, Any]]:
    section = _local_section(cfg)
    if not section["enabled"]:
        return []
    probe = _probe_local(section["url"])
    if not probe:
        return []
    models = probe.get("models", [])
    return models if isinstance(models, list) else []


def _runtime_available(cfg: Any) -> bool:
    section = _local_section(cfg)
    if not section["enabled"]:
        return False
    return _probe_local(section["url"]) is not None


def _model_matches(names: List[str], model: str) -> bool:
    want = str(model or "").split(":")[0]
    for name in names:
        if name == model or str(name).split(":")[0] == want:
            return True
    return False


def _known_models(cfg: Any) -> List[Tuple[str, str]]:
    """Ordered, de-duplicated (id, source) pairs declared by real config."""
    get = _get(cfg)
    candidates = [
        (str(get("intent", "runner", "alias", default="") or "").strip(),
         "intent.runner"),
        (str(get("intent", "local_llm", "model", default="") or "").strip(),
         "intent.local_llm"),
        (str(get("intent", "ollama_model", default="") or "").strip(),
         "intent.ollama"),
        (str(get("agent", "model", default="") or "").strip(), "agent"),
        (str(get("summarizer", "ollama_model", default="") or "").strip(),
         "summarizer"),
        (str(get("intent", "vision", "model", default="") or "").strip(),
         "intent.vision"),
    ]
    out: List[Tuple[str, str]] = []
    seen: set = set()
    for model_id, source in candidates:
        if model_id and model_id not in seen:
            seen.add(model_id)
            out.append((model_id, source))
    if DEFAULT_MODEL not in seen:
        out.append((DEFAULT_MODEL, "intent.local_llm"))
    return out


def _known_model_ids(cfg: Any) -> set:
    return {model_id for model_id, _ in _known_models(cfg)}


def _is_known_model(cfg: Any, model: str) -> bool:
    if model in _known_model_ids(cfg):
        return True
    return any(row.get("id") == model for row in _observed_models(cfg))


# --- reads -----------------------------------------------------------------
def status(cfg: Any) -> Dict[str, Any]:
    """Local model runtime state, honestly derived from real Qubit state."""
    section = _local_section(cfg)
    alias = _runner_alias(cfg)
    probe = _probe_local(section["url"]) if section["enabled"] else None
    names = [row.get("id", "") for row in (probe or {}).get("models", [])]
    model_present = _model_matches(names, alias)
    if not section["enabled"]:
        state, detail = "UNAVAILABLE", "local models are disabled in config"
    elif probe is None:
        state = "UNAVAILABLE"
        detail = ("local runtime is not reachable on loopback; nothing was "
                  "started or loaded")
    elif not model_present:
        state = "DEGRADED"
        detail = (f"local runtime is up but the configured model {alias!r} "
                  "is not present")
    else:
        state, detail = "AVAILABLE", "resident local runtime is ready"
    return {"state": state, "schema": STATUS_SCHEMA,
            "available": state == "AVAILABLE", "provider": PROVIDER,
            "backend": BACKEND, "enabled": section["enabled"],
            "model": alias, "model_present": model_present,
            "fallback": resolve_fallback(cfg),
            "endpoint": _redact(section["url"]), "observed_models": len(names),
            "detail": _redact(detail),
            "rollback": "read-only; nothing to roll back"}


def _vision_gauge(cfg: Any) -> Dict[str, Any]:
    try:
        from .vision import status as _vision_status

        return _vision_status(cfg, timeout_s=2.0)
    except Exception as exc:
        return {"state": "UNAVAILABLE", "detail": str(exc)[:160]}


def hardware(cfg: Any) -> Dict[str, Any]:
    """GPU / VRAM / accelerator inventory from the real gauges.

    Reuses :func:`voicebridge.runtime_inventory.current_vram_mb` and
    :func:`voicebridge.vram_guard.free_vram_mib` and the vision gauge.
    Nothing is measured, so nothing is fabricated: no live metric means a
    typed ``UNKNOWN``.
    """
    try:
        vram_free_mb = int(_RIN.current_vram_mb())
    except Exception:
        vram_free_mb = -1
    try:
        free_mib = free_vram_mib()
    except Exception:
        free_mib = None
    vision = _vision_gauge(cfg)
    measured = vram_free_mb >= 0 or free_mib is not None
    state = "OK" if measured else "UNKNOWN"
    return {"state": state, "schema": HARDWARE_SCHEMA,
            "accelerator": "nvidia" if measured else "unknown",
            "vram_free_mb": vram_free_mb if vram_free_mb >= 0 else None,
            "free_vram_mib": free_mib,
            "vision": {"state": vision.get("state"),
                       "model": vision.get("model"),
                       "size_mib": vision.get("size_mib"),
                       "free_vram_mib": vision.get("free_vram_mib")},
            "detail": ("live GPU memory observed" if measured else
                       "no live GPU gauge available; no values fabricated"),
            "rollback": "read-only; nothing to roll back"}


def catalog(cfg: Any) -> Dict[str, Any]:
    """Known/installed local models from real config + runtime state."""
    probe = _probe_local(_local_section(cfg)["url"]) \
        if _local_section(cfg)["enabled"] else None
    observed = (probe or {}).get("models", []) if probe else []
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for item in observed:
        model_id = item.get("id", "")
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        rows.append({"id": model_id, "name": item.get("name", model_id),
                     "size": item.get("size"), "state": "installed",
                     "source": "runtime"})
    for model_id, source in _known_models(cfg):
        if model_id in seen:
            continue
        seen.add(model_id)
        rows.append({"id": model_id, "name": model_id, "size": None,
                     "state": "configured", "source": source})
    return {"state": "OK", "schema": CATALOG_SCHEMA, "models": rows,
            "fallback": fallback_chain(cfg)["chain"],
            "runtime_observed": probe is not None,
            "detail": ("runtime model list observed" if probe is not None
                       else "runtime not observed; only configured models "
                            "are listed"),
            "rollback": "read-only; nothing to roll back"}


def jobs(cfg: Any) -> Dict[str, Any]:
    """Install/download job history — typed EMPTY (Qubit has no such store)."""
    return {"state": "EMPTY", "code": JOBS_EMPTY_CODE, "schema": JOBS_SCHEMA,
            "jobs": [],
            "detail": "Qubit keeps no local-model install/download job store; "
                      "runtime provisioning is brokered through `qubit "
                      "runtime` and never queued here",
            "rollback": "read-only; nothing to roll back"}


# --- writes: typed gates only, never an action -----------------------------
def _provision_gate(cfg: Any, op: str, *, confirmed: bool) -> Dict[str, Any]:
    if estop_engaged(cfg):
        return _refusal(op, ESTOP_CODE,
                        "the emergency stop is latched; reset it before "
                        "changing local models", state="ESTOP")
    if not confirmed:
        return _refusal(op, CONFIRM_CODE,
                        f"{op} requires explicit confirmation; nothing was "
                        "downloaded or written")
    return _refusal(op, BROKERED_CODE,
                    "Qubit does not download or install local models/runtimes "
                    "directly; provisioning is brokered (pinned, confirmed, "
                    "audited)")


def _runtime_gate(cfg: Any, op: str, *, confirmed: bool) -> Dict[str, Any]:
    if estop_engaged(cfg):
        return _refusal(op, ESTOP_CODE,
                        "the emergency stop is latched; reset it before "
                        "changing local models", state="ESTOP")
    if not _runtime_available(cfg):
        return _refusal(op, UNAVAILABLE_CODE,
                        "no local model runtime is reachable on loopback; "
                        "nothing was started or changed")
    if not confirmed:
        return _refusal(op, CONFIRM_CODE,
                        f"{op} requires explicit confirmation; nothing was "
                        "changed")
    return _refusal(op, BROKERED_CODE,
                    "Qubit manages the local runtime through the brokered "
                    "pinned-runtime workflow, not this surface")


def runtime_install(cfg: Any, *, backend: Optional[str] = None,
                    requested_by: str = "cli:user",
                    confirmed: bool = False) -> Dict[str, Any]:
    """Pinned-runtime install is brokered; no network, no download."""
    return _provision_gate(cfg, "runtime_install", confirmed=confirmed)


def download(cfg: Any, *, model: str, requested_by: str = "cli:user",
             confirmed: bool = False) -> Dict[str, Any]:
    """Model download is brokered; nothing is fetched or queued."""
    clean = str(model or "").strip()
    if not clean:
        return _refusal("download", BAD_INPUT_CODE, "model is required")
    if not _is_known_model(cfg, clean):
        return _refusal("download", UNKNOWN_CODE,
                        f"unknown local model {clean!r}; known models: "
                        f"{', '.join(sorted(_known_model_ids(cfg))) or 'none'}")
    return _provision_gate(cfg, "download", confirmed=confirmed)


def download_pause(cfg: Any, *, job_id: str, requested_by: str = "cli:user",
                   confirmed: bool = False) -> Dict[str, Any]:
    """Pausing a download is brokered; Qubit keeps no download-job store.

    Mirrors the vendored ``web_routers/local_models.py:local_models_download_pause``
    request contract (``{"job_id": ...}``) on Qubit's authority path. Qubit runs
    no managed local-runtime downloader, so every job is unknown here; the verb
    still passes through the sibling e-stop -> confirmation -> broker gate and
    never touches a job/queue.
    """
    clean = str(job_id or "").strip()
    if not clean:
        return _refusal("download.pause", BAD_INPUT_CODE, "job_id is required")
    return _provision_gate(cfg, "download.pause", confirmed=confirmed)


def download_resume(cfg: Any, *, job_id: str, requested_by: str = "cli:user",
                    confirmed: bool = False) -> Dict[str, Any]:
    """Resuming a download is brokered; Qubit keeps no download-job store.

    Mirrors the vendored ``web_routers/local_models.py:local_models_download_resume``
    request contract (``{"job_id": ...}``) on Qubit's authority path. No job is
    restarted or resumed by this surface.
    """
    clean = str(job_id or "").strip()
    if not clean:
        return _refusal("download.resume", BAD_INPUT_CODE, "job_id is required")
    return _provision_gate(cfg, "download.resume", confirmed=confirmed)


def sideload(cfg: Any, *, path: str, requested_by: str = "cli:user",
             confirmed: bool = False) -> Dict[str, Any]:
    """Registering an on-disk GGUF is brokered; no filesystem access here."""
    clean = str(path or "").strip()
    if not clean or not clean.lower().endswith(".gguf"):
        return _refusal("sideload", BAD_INPUT_CODE,
                        "path must be an absolute path to a .gguf file")
    return _provision_gate(cfg, "sideload", confirmed=confirmed)


def server(cfg: Any, action: str = "start", *,
           requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Local server start/stop is brokered; no process is touched."""
    clean = str(action or "").strip().lower()
    if clean not in ("start", "stop"):
        return _refusal("server", BAD_INPUT_CODE,
                        "action must be 'start' or 'stop'")
    return _runtime_gate(cfg, f"server.{clean}", confirmed=confirmed)


def eject(cfg: Any, *, model: str, requested_by: str = "cli:user",
          confirmed: bool = False) -> Dict[str, Any]:
    """Ejecting a resident model is brokered; nothing is unloaded here."""
    clean = str(model or "").strip()
    if not clean:
        return _refusal("eject", BAD_INPUT_CODE, "model is required")
    if not _is_known_model(cfg, clean):
        return _refusal("eject", UNKNOWN_CODE,
                        f"unknown local model {clean!r}")
    return _runtime_gate(cfg, "eject", confirmed=confirmed)


def activate(cfg: Any, *, model: str, requested_by: str = "cli:user",
             confirmed: bool = False) -> Dict[str, Any]:
    """Making a model the default is a brokered config write; nothing here."""
    clean = str(model or "").strip()
    if not clean:
        return _refusal("activate", BAD_INPUT_CODE, "model is required")
    if not _is_known_model(cfg, clean):
        return _refusal("activate", UNKNOWN_CODE,
                        f"unknown local model {clean!r}")
    return _runtime_gate(cfg, "activate", confirmed=confirmed)


def quickstart(cfg: Any, *, model: Optional[str] = None,
               requested_by: str = "cli:user",
               confirmed: bool = False) -> Dict[str, Any]:
    """One-click runtime+model setup is brokered; nothing is downloaded."""
    if model is not None:
        clean = str(model or "").strip()
        if not clean:
            return _refusal("quickstart", BAD_INPUT_CODE, "bad model id")
        if not _is_known_model(cfg, clean):
            return _refusal("quickstart", UNKNOWN_CODE,
                            f"unknown local model {clean!r}")
    return _provision_gate(cfg, "quickstart", confirmed=confirmed)


def fallback_chain(cfg: Any) -> Dict[str, Any]:
    """Ordered model fallback chain derived from real config + runtime state.

    Read-only and loopback-only: each configured candidate is annotated with
    whether the resident runtime actually reports it. Nothing is downloaded,
    loaded, or probed off loopback, and no provider credentials are required.
    """
    observed = [row.get("id", "") for row in _observed_models(cfg)]
    observed = [name for name in observed if name]
    chain: List[Dict[str, Any]] = []
    for model_id, source in _known_models(cfg):
        available = bool(observed) and _model_matches(observed, model_id)
        chain.append({"id": model_id, "source": source,
                      "available": available,
                      "state": "available" if available else "configured"})
    runtime_observed = bool(observed)
    return {"state": "OK", "schema": FALLBACK_SCHEMA, "chain": chain,
            "runtime_observed": runtime_observed,
            "detail": ("ordered from configured models; availability is "
                       "observed only from the loopback runtime"
                       if runtime_observed else
                       "runtime not observed; no model is marked available"),
            "rollback": "read-only; nothing to roll back"}


def resolve_fallback(cfg: Any, model: str = "") -> Dict[str, Any]:
    """Pick the requested model, or automatically fall back to one available.

    No credentials and no egress: selection is over the configured candidates
    observed from the loopback runtime. When the requested model is not
    present (or absent entirely) the first available candidate is selected;
    with no observed candidate the result is the typed
    ``LOCAL_MODEL_UNAVAILABLE`` — never a fabricated pick.
    """
    wanted = str(model or "").strip()
    chain = fallback_chain(cfg)["chain"]
    available = [row for row in chain if row.get("available")]
    base = {"schema": FALLBACK_SCHEMA, "wanted": _redact(wanted)[:120],
            "chain": chain}

    def _picked(selected: str, *, fallback: bool) -> Dict[str, Any]:
        return {**base, "state": "OK", "selected": selected,
                "fallback": bool(fallback),
                "detail": ("requested model available" if not fallback else
                           "requested model unavailable; fell back to the "
                           "first available configured model"),
                "rollback": "read-only; nothing to roll back"}

    if wanted:
        for row in chain:
            if row["id"] == wanted or \
                    row["id"].split(":")[0] == wanted.split(":")[0]:
                if row["available"]:
                    return _picked(row["id"], fallback=False)
                break
        if available:
            return _picked(available[0]["id"], fallback=True)
    elif available:
        return _picked(available[0]["id"], fallback=False)
    return {**base, "state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
            "selected": None, "fallback": False,
            "detail": "no configured local model is available on the loopback "
                      "runtime; nothing was started or loaded",
            "owner": "qubit-policy",
            "rollback": "read-only; nothing to roll back"}


def public_error(code: str, detail: str) -> Dict[str, Any]:
    """Public JSON envelope for a typed local-model failure (redacted)."""
    return {"state": "FAILED", "code": code, "detail": _redact(detail)[:300],
            "owner": "qubit-policy",
            "rollback": "nothing was changed; nothing to roll back"}


def structured_task(cfg: Any, *, prompt: Any, schema: Any = None,
                    runner: Any = None) -> Dict[str, Any]:
    """JSON-only structured task over the local model boundary.

    Qubit-owned equivalent of the Qubit ``llm-task`` tool. The prompt is
    bounded and validated locally; the model call is only ever made through a
    caller-supplied ``runner`` (an injected boundary). With no runner the
    result is a typed ``LOCAL_MODEL_TASK_UNAVAILABLE`` and a non-JSON reply
    from the runner is a typed failure — Qubit never fabricates a structure.
    """
    text = str(prompt or "").strip()
    if not text or len(text) > 8000:
        return public_error(TASK_BAD_INPUT_CODE,
                            "a prompt of 1..8000 characters is required")
    if runner is None:
        return {**_refusal("structured-task", TASK_UNAVAILABLE_CODE,
                           "no model runner is configured; use the resident "
                           "local runtime or the supervised agent path",
                           state="UNAVAILABLE"),
                "schema": TASK_SCHEMA}
    try:
        raw = runner({"prompt": text, "schema": schema})
    except Exception as exc:  # noqa: BLE001 - injected boundary
        return public_error(TASK_UNAVAILABLE_CODE, str(exc))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return public_error(TASK_BAD_INPUT_CODE,
                                "runner reply was not valid JSON")
    if not isinstance(raw, dict):
        return public_error(TASK_BAD_INPUT_CODE,
                            "runner reply was not a JSON object")
    if schema is not None and isinstance(schema, dict):
        required = [k for k in schema.get("required", []) if isinstance(k, str)]
        absent = [k for k in required if k not in raw]
        if absent:
            return public_error(TASK_BAD_INPUT_CODE,
                                "runner reply missing required keys: "
                                + ", ".join(sorted(absent)))
    return {"state": "OK", "schema": TASK_SCHEMA, "result": raw,
            "detail": "structured result returned by the injected runner",
            "rollback": "read-only; nothing to roll back"}
