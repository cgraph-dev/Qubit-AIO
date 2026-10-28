"""Canonical resident Ollama runner contract (Packet 4B).

ONE source for every call to the resident generative model:
model alias, context size, keep-alive, placement/parallelism policy.
Per rule 6.3-27, no caller may set its own num_ctx/keep_alive/placement.

The reload key under pinned Ollama semantics (docs/archive/2026-09-21/UPSTREAM_REUSE.md R5) is
the full runner options struct — here, num_ctx — plus model identity.
keep_alive is expiry policy only and is pinned to -1 (always resident).
Placement/parallelism are recorded as policy fields; the bridge deliberately
does NOT send per-request GPU options (server default observed = 100% GPU
offload); changing them happens ONLY here, never in a caller.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from .logging_setup import log_event

DEFAULT_ALIAS = "qubit-brain"
DEFAULT_URL = "http://127.0.0.1:11434"
# Measured choice (Packet 4B, docs/archive/2026-09-21/BENCHMARKS.md): smallest context that fits
# the compiled-context budget plus output reserve with >=25% headroom.
DEFAULT_NUM_CTX = 4096
KEEP_ALIVE = -1


@dataclass(frozen=True)
class RunnerContract:
    alias: str
    url: str
    num_ctx: int
    keep_alive: int = KEEP_ALIVE
    placement_policy: str = "ollama-default-full-offload"
    parallelism: int = 1
    digest: Optional[str] = None

    def as_log_fields(self) -> dict:
        return {"alias": self.alias, "url": self.url, "num_ctx": self.num_ctx,
                "keep_alive": self.keep_alive,
                "placement": self.placement_policy,
                "parallelism": self.parallelism, "digest": self.digest}


def _fetch_digest(url: str, alias: str, timeout_s: float = 1.0) -> Optional[str]:
    """Best-effort digest lookup; never blocks startup (typed None).

    Tries the /api/show ``X-Ollama-Digest`` header first, then falls back to
    the per-model digest in /api/tags (Ollama versions that omit the header).
    Never fabricates a value: None means the digest could not be obtained and
    callers must fail closed."""
    base = url.rstrip("/")
    try:
        req = urllib.request.Request(
            f"{base}/api/show",
            data=json.dumps({"model": alias}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            d = resp.headers.get("X-Ollama-Digest")
            if d:
                return d or None
    except Exception:
        pass
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=timeout_s) as resp:
            tags = json.loads(resp.read().decode())
        for m in tags.get("models", []):
            name = str(m.get("name") or "")
            want = alias.split(":")[0]
            if name.split(":")[0] == want:
                return m.get("digest") or None
    except Exception:
        pass
    return None


_KNOWN_DIGESTS: dict = {}
_LOGGED_KEYS: set = set()


def reset_cache() -> None:
    """Test/diagnostic hook: forget cached contracts.

    Clears both the per-key resolution log and the stored digests, so a
    settings PUT that changes the model/URL (wired through the settings
    sync) takes effect on the next resolve with no restart.
    """
    _LOGGED_KEYS.clear()
    _KNOWN_DIGESTS.clear()


def _walk(node: Any, *path: str) -> Any:
    cur = node
    for p in path:
        if isinstance(cur, dict):
            if p not in cur:
                return None
            cur = cur[p]
        else:
            getter = getattr(cur, "get", None)
            if getter is None:
                return None
            try:
                cur = getter(p, default=None)
            except TypeError:
                cur = getter(p)
            if cur is None:
                return None
    return cur


def resolve(cfg: Any = None) -> RunnerContract:
    """Resolve the single contract from config precedence:

    intent.runner.* > legacy flat keys > measured defaults.
    Legacy keys keep working so no live-config edit is required.
    ``cfg`` may be the FULL config (paths prefixed ``intent.``) or the
    ``intent`` section itself (Classifier-style); both are supported.

    Resolutions are cached per key so hot-path callers never pay an HTTP
    round-trip; the digest lookup happens once per distinct contract.
    """
    def get(*path: str, default: Any = None) -> Any:
        if cfg is None:
            return default
        value = _walk(cfg, *path)
        if value is None and path[0] == "intent":
            value = _walk(cfg, *path[1:])   # cfg given as intent section
        return default if value is None else value

    alias = str(get("intent", "runner", "alias", default=None)
                or get("intent", "ollama_model", default=None)
                or get("intent", "local_llm", "model", default=None)
                or DEFAULT_ALIAS)
    url = str(get("intent", "runner", "url", default=None)
              or get("intent", "local_llm", "url", default=None)
              or get("intent", "ollama_url", default=None)
              or DEFAULT_URL)
    num_ctx = int(get("intent", "runner", "num_ctx", default=None)
                  or get("intent", "num_ctx", default=None)
                  or DEFAULT_NUM_CTX)
    key = (alias, url, num_ctx)
    if key in _LOGGED_KEYS:
        return RunnerContract(alias=alias, url=url, num_ctx=num_ctx,
                              digest=_KNOWN_DIGESTS.get(key))
    digest = _fetch_digest(url, alias)
    if digest:
        _KNOWN_DIGESTS[key] = digest
    _LOGGED_KEYS.add(key)
    contract = RunnerContract(alias=alias, url=url, num_ctx=num_ctx,
                              digest=digest)
    log_event("runner.contract", **contract.as_log_fields())
    return contract


def material_options(payload: dict) -> dict:
    """The reload-relevant projection of an outgoing request body (R5)."""
    opts = payload.get("options") or {}
    return {"num_ctx": opts.get("num_ctx"),
            "keep_alive_present": "keep_alive" in payload}
