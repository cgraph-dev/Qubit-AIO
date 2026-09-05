"""Packet 8M: optional GENOME-compatible semantic memory adapter.

Adapts the pinned GENOME contract (docs/UPSTREAM_REUSE.md R23) as a narrow,
disabled-by-default wrapper over the existing 5B ``MemoryStore``:

- temporal/bi-temporal belief facts (``valid_from`` inclusive,
  ``valid_until`` exclusive, NULL = open window), believer-aware invalidation,
  point-in-time ``belief_at``/``current_facts``, ``belief_conflicts``, and
  stored-data-only ``explain``;
- deterministic hash-chained journal + replay/verify (voicebridge/memory/journal.py);
- optional semantic recall via an embedding provider: ``none`` (default, FTS5
  baseline only), ``hash`` (offline deterministic stdlib projection,
  approximate), or ``ollama`` (existing ``nomic-embed-text`` endpoint, CPU-only
  placement). Ingest is ALWAYS LLM-free; embeddings are opt-in, never contacted
  by default status, and reported with typed missing/degraded states.

Guarantees preserved from the 5B store: scope isolation, provenance/trust,
secret rejection, confidence, expiration, correction, deletion, retention,
redaction, crash recovery, and "memory never trains or self-modifies the
model". Web/tool/model-origin facts carry a lower source-trust and therefore
cannot close a higher-trust user fact (origin-bound authority), and no
low-trust actor can correct, forget, purge, promote, or invalidate a higher-
trust user fact. Unknown or forged sources fail closed.

External-content boundary (Packet 8M repair, documented in R23):

  Sources ``web``, ``tool``, ``model`` and ``observed`` are EXTERNAL-origin.
  Their RAW text is NEVER stored, journaled, FTS-indexed, or returned. It is
  replaced by a deterministic bounded reference ``[external:<source>:<sha256
  of the normalized text, 20 hex>:<optional <=60-char caller label>]`` that
  cannot expose the original private text. Every output surface (journal,
  history, recall, explain, conflicts, CLI, export) therefore only ever carries
  the reference for external-origin records, while legitimate user-owned
  (source="user") memory is stored raw. This keeps deterministic replay honest:
  replay reproduces the stored reference exactly, and integrity is never
  claimed for raw content that was intentionally not stored.

State-config boundary (Packet 8M repair): ``load_semantic_state`` /
``write_semantic_state`` validate EVERY value (boolean ``enabled``, known
provider, finite ``0 < timeout_s <= 60``, integer ``0 < top_k <= 50``, a valid
non-empty model identifier, a valid http(s) ``ollama_url``, no unsupported
fields, and a supported ``schema_version``). Malformed values return a typed
``degraded``/``config-invalid`` health — they are NEVER silently coerced into
valid-looking state. ``write_semantic_state`` validates before persisting and
surfaces any write/flush/file-fsync/rename/directory-fsync/permission failure
as a typed ``SemanticStateError`` (no swallowed directory-fsync).

No MCP server, no second store/database/server/listener, no model downloads.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .journal import (Journal, snapshot_hash, verify_chain, replay as _replay,
                      verify_journal as _verify_journal)
from .store import (MemoryStore, MemoryStoreError, SecretRejectedError,
                    normalize_text, norm_hash, _SECRET_SHAPES,
                    redact_secrets)

PROVIDERS = ("none", "hash", "ollama")
SOURCES = ("user", "tool", "web", "model", "observed")
EXTERNAL_SOURCES = ("tool", "web", "model", "observed")

# Origin-bound authority: a lower-trust source may not close a higher-trust
# fact. User-explicit facts (source="user") always outrank web/tool/model
# observations; ties coexist and surface as belief_conflicts.
_SOURCE_TRUST = {"user": 3, "tool": 2, "web": 1, "model": 1, "observed": 1}

# State file schema version (memory/semantic.json). Bump on incompatible
# change; a NEWER version is a typed DEGRADED state, never silently reset.
SEMANTIC_STATE_SCHEMA = 1
# Documented configuration bounds for the persisted runtime state.
STATE_TIMEOUT_MAX = 60.0
STATE_TOP_K_MAX = 50
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\-+]{0,127}$")
_STATE_FIELDS = frozenset({"enabled", "provider", "embed_model", "ollama_url",
                           "timeout_s", "top_k", "schema_version"})
# A stored external-origin reference: [external:<source>:<20 hex>:<label?>]
_EXTERNAL_REF_RE = re.compile(
    r"^\[external:(tool|web|model|observed):[0-9a-f]{20}"
    r"(:[^\]\n]{0,60})?\]$")


class SemanticMemoryError(Exception):
    """Typed adapter failure."""


class SemanticProviderUnavailable(SemanticMemoryError):
    """The configured embedding provider could not produce a vector."""


class BeliefFormatError(SemanticMemoryError):
    """A belief fact's entity/fact_type/value could not be parsed."""


class SemanticStateError(SemanticMemoryError):
    """The persisted semantic-memory runtime state is corrupt, invalid, or
    could not be written durably."""


def _trust(source: Optional[str]) -> int:
    if source not in _SOURCE_TRUST:
        raise SemanticMemoryError(f"unknown/forged source {source!r} (fail closed)")
    return _SOURCE_TRUST[source]


def _require_source(source: Optional[str]) -> str:
    if source not in SOURCES:
        raise SemanticMemoryError(f"unknown/forged source {source!r} (fail closed)")
    return source


def _validate_finite(name: str, value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise SemanticMemoryError(f"{name} must be a finite number") from exc
    if not math.isfinite(v):
        raise SemanticMemoryError(f"{name} must be a finite number")
    return v


# ---------------------------------------------- external-content boundary
def external_ref(text: str, source: str, label: str = "") -> str:
    """Deterministic, bounded, non-recoverable reference for external-origin
    content. The raw text is reduced to a truncated SHA-256 digest of its
    normalized form plus an optional caller-supplied short label; the original
    private text cannot be recovered from it."""
    digest = hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()[:20]
    label = re.sub(r"[\]\n]", "", (label or "").strip())[:60]
    base = f"[external:{source}:{digest}"
    return f"{base}:{label}]" if label else f"{base}]"


def looks_like_external_ref(text: Optional[str]) -> bool:
    return bool(text) and bool(_EXTERNAL_REF_RE.match((text or "").strip()))


def _store_text(text: str, source: str, label: str = "") -> str:
    """The text actually persisted for a source. User-owned content is kept raw
    (legitimate private memory); external-origin content becomes the bounded
    non-recoverable reference. Idempotent for already-stored references so
    journal replay and import reproduce them exactly."""
    if source == "user":
        return text
    if looks_like_external_ref(text):
        return text
    return external_ref(text, source, label)


def _valid_url(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    return (u.scheme in ("http", "https")
            and bool(u.netloc) and bool(u.hostname))


def _validate_state_values(state: Dict[str, Any]) -> Optional[str]:
    """Return an error string for the first invalid field, or None."""
    for k in state:
        if k not in _STATE_FIELDS:
            return f"unsupported state field {k!r}"
    v = state.get("enabled")
    if not isinstance(v, bool):
        return f"enabled must be a boolean, got {v!r}"
    provider = state.get("provider")
    if provider not in PROVIDERS:
        return f"invalid provider {provider!r}"
    t = state.get("timeout_s")
    if not isinstance(t, (int, float)) or isinstance(t, bool):
        return f"timeout_s must be a number, got {t!r}"
    tf = float(t)
    if not math.isfinite(tf) or tf <= 0:
        return f"timeout_s must be > 0, got {t!r}"
    if tf > STATE_TIMEOUT_MAX:
        return f"timeout_s above maximum {STATE_TIMEOUT_MAX}"
    tk = state.get("top_k")
    if not isinstance(tk, int) or isinstance(tk, bool):
        return f"top_k must be an integer, got {tk!r}"
    if tk <= 0:
        return f"top_k must be > 0, got {tk!r}"
    if tk > STATE_TOP_K_MAX:
        return f"top_k above maximum {STATE_TOP_K_MAX}"
    m = state.get("embed_model")
    if not isinstance(m, str) or not _MODEL_RE.match(m):
        return f"invalid embed_model {m!r}"
    u = state.get("ollama_url")
    if not isinstance(u, str) or not _valid_url(u):
        return f"invalid ollama_url {u!r}"
    ver = state.get("schema_version")
    if not isinstance(ver, int) or isinstance(ver, bool) \
            or ver > SEMANTIC_STATE_SCHEMA:
        return f"unsupported schema_version {ver!r}"
    return None


# ------------------------------------------------------ state persistence
def _default_state(enabled: Any = False, provider: Any = "none",
                   embed_model: Any = "nomic-embed-text",
                   ollama_url: Any = "http://127.0.0.1:11434",
                   timeout_s: Any = 10.0, top_k: Any = 5) -> Dict[str, Any]:
    return {"enabled": enabled, "provider": provider,
            "embed_model": embed_model, "ollama_url": ollama_url,
            "timeout_s": timeout_s, "top_k": top_k,
            "schema_version": SEMANTIC_STATE_SCHEMA}


def load_semantic_state(path: Path | str, *,
                        enabled_default: Any = False,
                        provider_default: Any = "none",
                        embed_model_default: Any = "nomic-embed-text",
                        ollama_url_default: Any = "http://127.0.0.1:11434",
                        timeout_s_default: Any = 10.0,
                        top_k_default: Any = 5,
                        ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Read and VALIDATE the runtime semantic-memory state.

    Returns ``(state, health)`` where ``health["state"]`` is ``ok``,
    ``degraded`` (corrupt/unreadable/config-invalid), or ``newer`` (schema the
    current build cannot safely apply). Every value is validated; malformed
    values are NEVER silently coerced. The returned ``state`` is the validated
    defaults when health is not ``ok``, so callers surface the typed state and
    refuse unconfirmed mutations.
    """
    defaults = _default_state(enabled_default, provider_default,
                              embed_model_default, ollama_url_default,
                              timeout_s_default, top_k_default)
    dflt_err = _validate_state_values(defaults)
    if dflt_err is not None:
        return dict(defaults), {"state": "degraded",
                                "detail": f"invalid semantic config default: "
                                          f"{dflt_err}"}
    p = Path(path)
    health: Dict[str, Any] = {"state": "ok", "detail": ""}
    if not p.exists():
        return dict(defaults), health
    try:
        data = json.loads(p.read_text())
    except Exception as exc:
        return dict(defaults), {"state": "degraded",
                                "detail": f"semantic state unreadable: {exc}"}
    if not isinstance(data, dict):
        return dict(defaults), {"state": "degraded",
                                "detail": "semantic state is not an object"}
    ver = data.get("schema_version", SEMANTIC_STATE_SCHEMA)
    if not isinstance(ver, int) or ver > SEMANTIC_STATE_SCHEMA:
        return dict(defaults), {"state": "newer",
                                "detail": f"semantic state schema {ver} is newer "
                                          f"than supported {SEMANTIC_STATE_SCHEMA}"}
    unknown = [k for k in data if k not in _STATE_FIELDS]
    if unknown:
        return dict(defaults), {"state": "degraded",
                                "detail": f"unsupported state field(s): {unknown}"}
    candidate = dict(defaults)
    for k in _STATE_FIELDS:
        if k in data:
            candidate[k] = data[k]
    err = _validate_state_values(candidate)
    if err is not None:
        return dict(defaults), {"state": "degraded", "detail": err}
    return candidate, health


def _cleanup_tmp(tmp: Path) -> None:
    """Best-effort removal of our own temporary state file only. Never deletes
    directories or unrelated files."""
    try:
        if tmp.exists():
            tmp.unlink()
    except OSError:
        pass


def write_semantic_state(path: Path | str, state: Dict[str, Any]) -> Dict[str, Any]:
    """Validate-then-write the runtime state atomically.

    ANY failure — serialization, open, write, flush, file fsync, chmod, rename,
    or directory fsync — raises a typed :class:`SemanticStateError`; success is
    never reported when durability could not be guaranteed. On a failure before
    the rename, the temporary file is removed (best-effort). Returns the
    canonical validated state that was actually persisted.
    """
    if not isinstance(state, dict):
        raise SemanticStateError("semantic state must be an object")
    canonical = _default_state()
    canonical.update({k: state[k] for k in state if k in _STATE_FIELDS})
    err = _validate_state_values(canonical)
    if err is not None:
        raise SemanticStateError(f"refusing to write invalid semantic state: {err}")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.dumps(canonical, indent=2, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SemanticStateError(
            f"semantic state serialization failed: {exc}") from exc
    tmp = p.with_suffix(".json.tmp")
    try:
        fd = os.open(str(tmp), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        _cleanup_tmp(tmp)
        raise SemanticStateError(f"semantic state write failed: {exc}") from exc
    try:
        os.chmod(tmp, 0o600)
    except OSError as exc:
        _cleanup_tmp(tmp)
        raise SemanticStateError(
            f"semantic state permission failed: {exc}") from exc
    try:
        os.replace(tmp, p)
    except OSError as exc:
        _cleanup_tmp(tmp)
        raise SemanticStateError(f"semantic state rename failed: {exc}") from exc
    try:
        dir_fd = os.open(str(p.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as exc:
        raise SemanticStateError(
            f"semantic state directory fsync failed: {exc}") from exc
    return canonical


# ------------------------------------------------------------ embeddings


class HashProjection:
    """Deterministic offline embedding: a seeded random projection over
    token hashes. No model, no network; explicitly approximate. Used as the
    ``hash`` provider (offline replay/verification and opt-in semantic recall
    without any model download). Stdlib only."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = int(dim)
        self.name = "hash-projection"

    def embed(self, texts: List[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for t in texts:
            v = [0.0] * self.dim
            for tok in re.findall(r"[a-z0-9]+", normalize_text(t)):
                h = hashlib.sha256(tok.encode("utf-8")).digest()
                idx = int.from_bytes(h[:4], "big") % self.dim
                sign = 1.0 if h[4] & 1 else -1.0
                v[idx] += sign
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


class OllamaEmbed:
    """Optional embedding via the EXISTING Ollama ``nomic-embed-text``
    endpoint (same CPU-only placement contract as router/l1.py). Typed
    failures; never falls back silently to another model."""

    def __init__(self, url: str = "http://127.0.0.1:11434",
                 model: str = "nomic-embed-text", timeout_s: float = 10.0) -> None:
        self.url = url
        self.model = model
        self.timeout_s = float(timeout_s)
        self.name = "ollama:" + model

    def embed(self, texts: List[str]) -> List[List[float]]:
        from ..router.l1 import SemanticRouter
        try:
            return SemanticRouter._default_embed(
                texts, self.url, self.model, self.timeout_s)
        except Exception as exc:
            raise SemanticProviderUnavailable(
                f"embedding provider unavailable: {str(exc)[:120]}") from exc


# ---------------------------------------------------------------- adapter


@dataclass
class RecallResult:
    """One semantic-recall candidate with its provenance and score."""

    id: int
    text: str
    scope: str
    confidence: float
    source: str
    provenance: List[str]
    valid_from: Optional[float]
    valid_until: Optional[float]
    score: float


@dataclass
class BeliefConflict:
    entity: str
    fact_type: str
    value_a: str
    value_b: str
    source_a: str
    source_b: str
    believed_by_a: Optional[str]
    believed_by_b: Optional[str]


class SemanticMemory:
    """Narrow GENOME-contract adapter over a ``MemoryStore``.

    ``path_or_store`` accepts either a ``MemoryStore`` instance or a filesystem
    path (a new store is opened there; used for journal replay). Disabled by
    default: with ``enabled=False`` all reads/writes still work exactly like
    the underlying store and no embedding provider is ever contacted.
    """

    def __init__(self, path_or_store: MemoryStore | Path | str, *,
                 enabled: bool = False, provider: str = "none",
                 embed_model: str = "nomic-embed-text",
                 ollama_url: str = "http://127.0.0.1:11434",
                 timeout_s: float = 10.0, top_k: int = 5,
                 journal_path: Optional[Path | str] = None) -> None:
        if isinstance(path_or_store, MemoryStore):
            self.store = path_or_store
        else:
            self.store = MemoryStore(path_or_store)
        if not isinstance(enabled, bool):
            raise SemanticMemoryError(f"enabled must be a boolean, got {enabled!r}")
        self.enabled = enabled
        self._journal_path = Path(journal_path) if journal_path else None
        self._journal = Journal(self._journal_path) if self._journal_path else None
        self._journal_degraded = False
        self.set_provider(provider, embed_model=embed_model,
                          ollama_url=ollama_url, timeout_s=timeout_s)
        self.set_top_k(top_k)

    # -------------------------------------------------------------- config
    def set_top_k(self, top_k: Any) -> None:
        if not isinstance(top_k, int) or isinstance(top_k, bool) \
                or top_k <= 0 or top_k > STATE_TOP_K_MAX:
            raise SemanticMemoryError(
                f"top_k must be an integer in (0, {STATE_TOP_K_MAX}], "
                f"got {top_k!r}")
        self.top_k = int(top_k)

    def set_provider(self, provider: str, *, embed_model: str = "nomic-embed-text",
                     ollama_url: str = "http://127.0.0.1:11434",
                     timeout_s: float = 10.0) -> None:
        if provider not in PROVIDERS:
            raise SemanticMemoryError(
                f"unknown provider {provider!r}; choose one of {PROVIDERS}")
        if provider == "ollama":
            if not isinstance(embed_model, str) or not _MODEL_RE.match(embed_model):
                raise SemanticMemoryError(f"invalid embed_model {embed_model!r}")
            if not isinstance(ollama_url, str) or not _valid_url(ollama_url):
                raise SemanticMemoryError(f"invalid ollama_url {ollama_url!r}")
            try:
                tf = float(timeout_s)
            except (TypeError, ValueError) as exc:
                raise SemanticMemoryError("timeout_s must be a number") from exc
            if not math.isfinite(tf) or tf <= 0 or tf > STATE_TIMEOUT_MAX:
                raise SemanticMemoryError(
                    f"timeout_s must be in (0, {STATE_TIMEOUT_MAX}], got {timeout_s!r}")
        self.provider = provider
        if provider == "hash":
            self._embed = HashProjection()
        elif provider == "ollama":
            self._embed = OllamaEmbed(url=ollama_url, model=embed_model,
                                      timeout_s=timeout_s)
        else:
            self._embed = None

    def status(self) -> Dict[str, Any]:
        """Typed status: enabled/provider/embed availability/journal/counts.

        NEVER contacts a network service: ``embed_available`` is ``None`` until
        :meth:`check_provider` is explicitly called (an opt-in probe). The
        journal hash chain is checked locally (bounded personal-memory file).
        """
        active = self.store._active_select(scope=None, staged=False)
        beliefs = [r for r in active if r.get("kind") == "belief"]
        journal_degraded = self._journal_degraded
        journal_detail = ""
        if self._journal_path is not None and self._journal_path.is_file() \
                and self._journal_path.stat().st_size > 0:
            try:
                intact, reason = verify_chain(self._journal_path)
                if not intact:
                    journal_degraded = True
                    journal_detail = reason
            except OSError as exc:
                journal_degraded = True
                journal_detail = f"journal unreadable: {exc}"
        degraded = journal_degraded
        return {
            "enabled": self.enabled,
            "provider": self.provider if self.enabled else "none",
            "embed_available": None,   # not probed; call check_provider()
            "embed_degraded": False,
            "journal": str(self._journal_path) if self._journal_path else None,
            "journal_degraded": journal_degraded,
            "journal_detail": journal_detail,
            "state": "degraded" if degraded else "ok",
            "memories": len(active),
            "beliefs": len(beliefs),
        }

    def check_provider(self) -> Dict[str, Any]:
        """Opt-in live probe of the configured embedding provider (may contact
        Ollama when provider=ollama). Returns typed availability/degraded."""
        if not self.enabled or self.provider == "none" or self._embed is None:
            return {"provider": "none", "available": False, "degraded": False,
                    "detail": "no semantic provider enabled"}
        try:
            self._embed.embed(["ping"])
            return {"provider": self.provider, "available": True,
                    "degraded": False, "detail": "embedding provider responsive"}
        except SemanticProviderUnavailable as exc:
            return {"provider": self.provider, "available": False,
                    "degraded": True, "detail": str(exc)[:160]}

    # ------------------------------------------------------------- journal
    def _log(self, op: Dict[str, Any]) -> None:
        if self._journal is None:
            return
        try:
            self._journal.append(op)
        except OSError:
            self._journal_degraded = True

    def journal_verify(self) -> Dict[str, Any]:
        if self._journal_path is None:
            return {"state": "unavailable",
                    "detail": "no journal configured (disabled by default)"}
        ok, reason = _verify_journal(self._journal_path, self)
        return {"state": "ok" if ok else "failed", "detail": reason}

    def journal_replay(self, until_seq: Optional[int] = None,
                       out_path: Optional[Path | str] = None) -> Dict[str, Any]:
        """Replay the journal into a fresh temporary store and report its
        canonical snapshot hash (a rollback/branch preview; never mutates the
        live store)."""
        import tempfile
        if self._journal_path is None:
            return {"state": "unavailable",
                    "detail": "no journal configured (disabled by default)"}
        tmp = Path(out_path) if out_path else Path(
            tempfile.mkdtemp(prefix="vb-mem-replay-")) / "replay.db"
        fresh = SemanticMemory(tmp, enabled=False)
        try:
            n = _replay(self._journal_path, fresh, until_seq=until_seq)
            return {"state": "ok", "applied": n,
                    "snapshot_hash": snapshot_hash(fresh.store),
                    "until_seq": until_seq}
        finally:
            try:
                fresh.close()
            except Exception:
                pass
            if out_path is None:
                for suffix in ("", "-wal", "-shm"):
                    p = Path(str(tmp) + suffix)
                    if p.exists():
                        p.unlink()
                try:
                    Path(tmp).parent.rmdir()
                except OSError:
                    pass

    # ----------------------------------------------------------- mutations
    def remember(self, text: str, *, tier: str = "recall",
                 scope: str = "default", confidence: float = 1.0,
                 source: str = "user", provenance: Optional[List[str]] = None,
                 kind: str = "fact", expires_at: Optional[float] = None,
                 valid_from: Optional[float] = None,
                 valid_until: Optional[float] = None,
                 believed_by: Optional[str] = None,
                 mkey: Optional[str] = None,
                 label: str = "") -> Dict[str, Any]:
        """Store a fact. User-owned content is stored raw; external-origin
        content (web/tool/model/observed) is stored ONLY as a bounded
        non-recoverable reference, so raw private text never reaches the
        journal, history, FTS index, recall/explain output, or exports."""
        src = _require_source(source)
        for shape in _SECRET_SHAPES:
            if re.search(shape, text):
                raise SecretRejectedError("secret-shaped content rejected")
        stored = _store_text(text, src, label)
        rec = self.store.remember(stored, tier=tier, scope=scope,
                                  confidence=confidence, source=src,
                                  provenance=provenance, kind=kind,
                                  expires_at=expires_at,
                                  valid_from=valid_from,
                                  valid_until=valid_until,
                                  believed_by=believed_by, mkey=mkey)
        self._log({"op": "remember", "text": rec["text"], "tier": tier,
                   "scope": scope, "confidence": confidence, "source": src,
                   "provenance": list(provenance or []), "kind": kind,
                   "expires_at": expires_at, "valid_from": valid_from,
                   "valid_until": valid_until, "believed_by": believed_by,
                   "mkey": mkey, "id": rec["id"]})
        return rec

    def stage_inferred(self, text: str, *, scope: str = "default",
                       confidence: float = 0.5, source: str = "model",
                       source_ids: Optional[List[str]] = None,
                       tier: str = "recall",
                       label: str = "") -> int:
        if source not in SOURCES:
            raise SemanticMemoryError(
                f"unknown/forged source {source!r} (fail closed)")
        if source == "user":
            raise SemanticMemoryError(
                "inferred candidates must not claim source 'user'")
        for shape in _SECRET_SHAPES:
            if re.search(shape, text):
                raise SecretRejectedError(
                    "secret-shaped inferred content rejected")
        stored = _store_text(text, source, label)
        mid = self.store.stage_inferred(stored, scope=scope,
                                        confidence=confidence, source=source,
                                        source_ids=source_ids, tier=tier)
        self._log({"op": "stage_inferred", "text": stored, "scope": scope,
                   "confidence": confidence, "source": source,
                   "source_ids": list(source_ids or []), "tier": tier, "id": mid})
        return mid

    def correct(self, old_text: str, new_text: str, *,
                scope: str = "default", source: str = "user",
                label: str = "") -> Dict[str, Any]:
        """Correct a fact. Origin-bound authority: the correcting source's trust
        must be >= the existing fact's trust, so a web/tool/model actor can
        never replace a higher-trust user fact. The new text follows the
        external-content boundary (external-origin new text is stored as a
        non-recoverable reference)."""
        src = _require_source(source)
        for shape in _SECRET_SHAPES:
            if re.search(shape, new_text):
                raise SecretRejectedError("secret-shaped correction rejected")
        target = self.store.find_by_content(old_text, scope=scope)
        if target is not None and _trust(src) < _trust(target.get("source")):
            raise SemanticMemoryError(
                f"refusing to correct a higher-trust {target.get('source')!r} "
                f"fact from {source!r} (origin-bound authority)")
        stored_new = _store_text(new_text, src, label)
        rec = self.store.correct(old_text, stored_new, scope=scope, source=src)
        self._log({"op": "correct", "old_text": old_text,
                   "new_text": rec["text"], "scope": scope, "source": src,
                   "id": rec["id"]})
        return rec

    def forget(self, memory_id: int, *, reason: str = "user-forget",
               requested_by: str = "user") -> None:
        """Soft-delete with origin-bound authority: a caller may only tombstone
        facts of equal or lower trust."""
        src = _require_source(requested_by)
        rec = self.store.get(memory_id)
        if _trust(src) < _trust(rec.get("source")):
            raise SemanticMemoryError(
                f"refusing to forget a higher-trust {rec.get('source')!r} fact "
                f"from {src!r} (origin-bound authority)")
        self.store.forget(memory_id, reason=reason)
        self._log({"op": "forget", "id": memory_id, "reason": reason,
                   "requested_by": src})

    def purge(self, memory_id: int, *, requested_by: str = "user") -> None:
        """Hard-delete with origin-bound authority (same rule as forget)."""
        src = _require_source(requested_by)
        rec = self.store.get(memory_id)
        if _trust(src) < _trust(rec.get("source")):
            raise SemanticMemoryError(
                f"refusing to purge a higher-trust {rec.get('source')!r} fact "
                f"from {src!r} (origin-bound authority)")
        self.store.purge(memory_id)
        self._log({"op": "purge", "id": memory_id, "requested_by": src})

    def promote(self, memory_id: int, *, requested_by: str = "user"
                ) -> Dict[str, Any]:
        """Promotion is an explicit OWNER operation: only source='user' may
        promote a staged candidate into durable memory."""
        src = _require_source(requested_by)
        if src != "user":
            raise SemanticMemoryError(
                "promotion requires an explicit user operation (requested_by='user')")
        rec = self.store.promote(memory_id)
        self._log({"op": "promote", "id": memory_id, "requested_by": src})
        return rec

    # ------------------------------------------------------------ temporal
    @staticmethod
    def _belief_text(entity: str, fact_type: str, value: str) -> str:
        return f"{entity}: {fact_type}: {value}"

    @staticmethod
    def _parse_belief(text: str) -> tuple[str, str, str]:
        parts = text.split(":", 2)
        if len(parts) != 3:
            raise BeliefFormatError(f"not a belief record: {text[:60]!r}")
        return parts[0].strip(), parts[1].strip(), parts[2].strip()

    def _belief_records(self, entity: str, fact_type: Optional[str],
                        scope: str) -> List[Dict[str, Any]]:
        prefix = f"{entity}: "
        rows = self.store._conn.execute(
            "SELECT * FROM memories WHERE tombstoned=0 AND staged=0 "
            "AND kind='belief' AND scope=? ORDER BY id", (scope,)).fetchall()
        out = []
        for r in rows:
            d = self.store._row_to_dict(r)
            if not (d["text"] or "").startswith(prefix):
                continue
            if fact_type is not None:
                _, ft, _ = self._parse_belief(d["text"])
                if ft != fact_type:
                    continue
            out.append(d)
        return out

    def record_belief(self, entity: str, fact_type: str, value: str, *,
                      scope: str = "default", valid_from: Optional[float] = None,
                      valid_until: Optional[float] = None,
                      confidence: float = 1.0, source: str = "user",
                      provenance: Optional[List[str]] = None,
                      believed_by: Optional[str] = None,
                      invalidate_previous: bool = True,
                      label: str = "") -> Dict[str, Any]:
        """Record a fact believed true over [valid_from, valid_until) (NULL
        valid_until = open window). If ``invalidate_previous`` and an open
        fact of the same (entity, fact_type, believer) exists, close it at
        ``valid_from`` — unless the prior is higher-trust (origin-bound
        authority), in which case the two coexist as a conflict. A future-dated
        open prior caps our own window instead of creating two open facts.
        Entity/fact_type are schema identifiers and stay readable; an
        external-origin VALUE is stored only as a non-recoverable reference."""
        entity = (entity or "").strip()
        fact_type = (fact_type or "").strip()
        value = (value or "").strip()
        if not entity or not fact_type or not value:
            raise SemanticMemoryError("entity/fact_type/value must be non-empty")
        _validate_finite("confidence", confidence)
        if valid_from is not None:
            _validate_finite("valid_from", valid_from)
        if valid_until is not None:
            _validate_finite("valid_until", valid_until)
        src = _require_source(source)
        raw_text = self._belief_text(entity, fact_type, value)
        for shape in _SECRET_SHAPES:
            if re.search(shape, raw_text):
                raise SecretRejectedError(
                    "secret-shaped belief content rejected")
        value = _store_text(value, src, label)
        text = self._belief_text(entity, fact_type, value)
        now = valid_from if valid_from is not None else time.time()
        if valid_until is not None and valid_until <= now:
            raise SemanticMemoryError(
                "valid_until must be later than valid_from")
        incoming_trust = _SOURCE_TRUST[src]
        capped_until: Optional[float] = valid_until
        # Distinct temporal window identity: a re-recorded belief fact is a
        # new window, not a Mem0 dedup skip.
        mkey = f"{norm_hash(text)}|{now!r}"
        with self.store._lock:
            if invalidate_previous:
                for prior in self._belief_records(entity, fact_type, scope):
                    if prior["valid_until"] is not None:
                        continue
                    if prior.get("believed_by") != believed_by:
                        continue  # believer-aware: never close another's belief
                    prior_trust = _trust(prior.get("source"))
                    if incoming_trust < prior_trust:
                        continue  # refuse-close: low trust cannot close high trust
                    prior_from = prior["valid_from"] or 0.0
                    if prior_from <= now:
                        self.store._conn.execute(
                            "UPDATE memories SET valid_until=?, updated_at=? "
                            "WHERE id=?",
                            (now, time.time(), prior["id"]))
                        self.store._history(prior["id"], "belief_closed",
                                            {"closed_at": now,
                                             "by_fact": text})
                    else:
                        if capped_until is None or prior_from < capped_until:
                            capped_until = prior_from
            mid = self.store._insert(
                tier="recall", scope=scope, text=text,
                confidence=float(confidence), source=src,
                provenance=list(provenance or []), kind="belief",
                expires_at=None, supersedes=None, staged=False,
                valid_from=now, valid_until=capped_until,
                believed_by=believed_by, mkey=mkey)
            self.store._conn.commit()
        self._log({"op": "record_belief", "entity": entity,
                   "fact_type": fact_type, "value": value, "scope": scope,
                   "valid_from": now, "valid_until": capped_until,
                   "confidence": confidence, "source": src,
                   "provenance": list(provenance or []),
                   "believed_by": believed_by, "id": mid})
        return self.store.get(mid)

    def invalidate_belief(self, memory_id: int, *, at: Optional[float] = None,
                          requested_by: str = "user") -> Dict[str, Any]:
        """Close a belief window with origin-bound authority and sane timing:
        the caller must be at least as trusted as the fact, ``at`` must be a
        finite epoch not before the fact became valid, and the fact must not
        already be closed."""
        rec = self.store.get(memory_id)
        if rec.get("kind") != "belief":
            raise SemanticMemoryError(f"memory {memory_id} is not a belief fact")
        src = _require_source(requested_by)
        if _trust(src) < _trust(rec.get("source")):
            raise SemanticMemoryError(
                f"refusing to invalidate a higher-trust {rec.get('source')!r} "
                f"belief from {src!r} (origin-bound authority)")
        when = at if at is not None else time.time()
        _validate_finite("invalidation time", when)
        vf = rec.get("valid_from") or 0.0
        if when < vf:
            raise SemanticMemoryError(
                "invalidation time precedes the fact's valid_from")
        with self.store._lock:
            fresh = self.store._conn.execute(
                "SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
            if fresh is not None and fresh["valid_until"] is not None:
                raise SemanticMemoryError(
                    f"belief {memory_id} is already closed")
            self.store._conn.execute(
                "UPDATE memories SET valid_until=?, updated_at=? WHERE id=?",
                (when, time.time(), memory_id))
            self.store._history(memory_id, "belief_invalidated",
                                {"at": when})
            self.store._conn.commit()
        self._log({"op": "invalidate_belief", "id": memory_id, "at": when,
                   "requested_by": src})
        return self.store.get(memory_id)

    def facts_valid_at(self, entity: str, timestamp: float, *,
                       scope: str = "default",
                       fact_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Facts true at ``timestamp``: valid_from <= T < valid_until."""
        _validate_finite("timestamp", timestamp)
        return [r for r in self._belief_records(entity, fact_type, scope)
                if (r["valid_from"] or 0.0) <= timestamp
                and (r["valid_until"] is None or r["valid_until"] > timestamp)]

    def current_facts(self, entity: str, *, scope: str = "default",
                      fact_type: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.facts_valid_at(entity, time.time(), scope=scope,
                                   fact_type=fact_type)

    def entity_timeline(self, entity: str, *, scope: str = "default"
                        ) -> List[Dict[str, Any]]:
        return sorted(self._belief_records(entity, None, scope),
                      key=lambda r: r["valid_from"] or 0.0, reverse=True)

    def belief_conflicts(self, *, scope: str = "default") -> List[BeliefConflict]:
        """Overlapping validity windows for the same (entity, fact_type,
        believer) slot with differing values (coexisting beliefs)."""
        rows = [r for r in self.store._active_select(scope=scope, staged=False)
                if r.get("kind") == "belief"]
        by_slot: Dict[tuple, List[Dict[str, Any]]] = {}
        for r in rows:
            try:
                entity, ft, _ = self._parse_belief(r["text"])
            except BeliefFormatError:
                continue
            by_slot.setdefault(
                (entity, ft, r.get("believed_by")), []).append(r)
        conflicts: List[BeliefConflict] = []
        for slot, items in by_slot.items():
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    a, b = items[i], items[j]
                    if a["text"] == b["text"]:
                        continue
                    if _windows_overlap(a["valid_from"], a["valid_until"],
                                        b["valid_from"], b["valid_until"]):
                        ea, ft, va = self._parse_belief(a["text"])
                        _, _, vb = self._parse_belief(b["text"])
                        conflicts.append(BeliefConflict(
                            entity=redact_secrets(ea),
                            fact_type=redact_secrets(ft),
                            value_a=redact_secrets(va),
                            value_b=redact_secrets(vb),
                            source_a=a["source"], source_b=b["source"],
                            believed_by_a=a.get("believed_by"),
                            believed_by_b=b.get("believed_by")))
        return conflicts

    def explain(self, memory_id: int) -> Dict[str, Any]:
        """Stored-data-only explanation: record, validity window, provenance,
        source trust, and the append-only history. Never invents text.
        Secret-shaped content is redacted, and external-origin records are
        shown only as their bounded non-recoverable reference at this
        diagnostic boundary."""
        rec = self.store.get(memory_id)
        valid = None
        if rec.get("kind") == "belief":
            valid = {"valid_from": rec.get("valid_from"),
                     "valid_until": rec.get("valid_until")}
        src = rec.get("source")
        text = _store_text(rec["text"], src) if src in EXTERNAL_SOURCES \
            else rec["text"]
        history = []
        for h in self.store.history(memory_id):
            entry = dict(h)
            if entry.get("payload"):
                entry["payload"] = redact_secrets(
                    json.dumps(entry["payload"]))
            history.append(entry)
        return {
            "id": rec["id"], "text": redact_secrets(text),
            "tier": rec["tier"], "scope": rec["scope"], "kind": rec["kind"],
            "confidence": rec.get("confidence"),
            "source": src,
            "source_trust": _trust(src),
            "provenance": rec.get("provenance", []),
            "sensitivity": rec.get("sensitivity"),
            "believed_by": rec.get("believed_by"),
            "validity": valid,
            "supersedes": rec.get("supersedes"),
            "history": history,
        }

    # ---------------------------------------------------------- recall
    def recall(self, query: str, *, scope: Optional[str] = None,
               limit: Optional[int] = None) -> Dict[str, Any]:
        """Bounded recall. Baseline is FTS5 search (always available, LLM-free).
        With a semantic provider configured, the FTS candidate pool is
        re-ranked by embedding cosine similarity; a provider failure is a typed
        unavailable/degraded result, never a silent fake success."""
        pool = self.store.search(query, scope=scope,
                                 limit=max(limit or self.top_k, self.top_k))

        def _out(r: Dict[str, Any], score: float) -> Dict[str, Any]:
            src = r.get("source", "")
            text = _store_text(r["text"], src) if src in EXTERNAL_SOURCES \
                else r["text"]
            return {"id": r["id"], "text": redact_secrets(text),
                    "scope": r["scope"],
                    "confidence": r.get("confidence", 0.5),
                    "source": src,
                    "provenance": r.get("provenance", []),
                    "valid_from": r.get("valid_from"),
                    "valid_until": r.get("valid_until"),
                    "score": round(score, 4)}

        if not self.enabled or self.provider == "none" or self._embed is None:
            results = [_out(r, 0.0) for r in pool]
            return {"provider": "none", "engine": "fts5", "results": results,
                    "degraded": False, "unavailable": False,
                    "message": "FTS5 baseline (semantic provider not enabled)"}
        try:
            qv = self._embed.embed([query])[0]
        except SemanticProviderUnavailable as exc:
            results = [_out(r, 0.0) for r in pool]
            return {"provider": self.provider, "engine": "fts5",
                    "results": results, "degraded": True, "unavailable": True,
                    "message": redact_secrets(str(exc))[:160]}
        scored = []
        for r in pool:
            try:
                rv = self._embed.embed([r["text"]])[0]
            except SemanticProviderUnavailable:
                continue
            scored.append(_cosine(qv, rv))
        merged = []
        for r, score in zip(pool, scored):
            merged.append(_out(r, score))
        merged.sort(key=lambda x: -x["score"])
        results = merged[:max(limit or self.top_k, 1)]
        return {"provider": self.provider, "engine": "fts5+semantic",
                "results": results, "degraded": False, "unavailable": False,
                "message": f"semantic re-rank over FTS5 pool ({self.provider})"}

    # ------------------------------------------------------- export/import
    def export(self, *, scope: Optional[str] = None,
               path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
        """Scoped JSON export of active records. External-origin records are
        emitted ONLY as their bounded non-recoverable reference, so an exported
        bundle or report can never expose raw web/tool/model/observed content.
        User-owned records export raw (the 0600 file is the user's own data).
        Excludes tombstoned/staged by default."""
        records = []
        for r in self.store.export():
            d = dict(r)
            if d.get("source") in EXTERNAL_SOURCES:
                d["text"] = _store_text(d.get("text") or "",
                                        d.get("source", "observed"))
            records.append(d)
        if scope is not None:
            records = [r for r in records if r["scope"] == scope]
        if path is not None:
            out = Path(path)
            out.write_text(json.dumps(records, indent=2))
            try:
                os.chmod(out, 0o600)
            except OSError:
                pass
        return records

    def import_records(self, records: List[Dict[str, Any]], *,
                       default_scope: str = "default",
                       trusted_user_import: bool = False) -> Dict[str, Any]:
        """Re-remember a sanitized export. Imported data can NEVER claim
        source='user' UNLESS ``trusted_user_import=True`` is set by an explicit
        trusted user restore operation (the documented boundary that keeps
        user-owned private memory restorable). Records without that flag are
        treated as external-origin: they are stored only as a bounded
        non-recoverable reference. Secret-shaped, malformed, or invalid records
        are rejected and counted. Returns typed counts."""
        stored = rejected = invalid = 0
        for r in records:
            if not isinstance(r, dict) or not r.get("text"):
                invalid += 1
                continue
            raw_source = r.get("source")
            if trusted_user_import and raw_source == "user":
                source = "user"
            else:
                source = raw_source if raw_source in EXTERNAL_SOURCES else "observed"
            try:
                self.remember(
                    r["text"], tier=r.get("tier", "recall"),
                    scope=r.get("scope") or default_scope,
                    confidence=r.get("confidence", 0.5),
                    source=source,
                    provenance=r.get("provenance"),
                    kind=r.get("kind", "fact"),
                    valid_from=r.get("valid_from"), valid_until=r.get("valid_until"),
                    believed_by=r.get("believed_by"))
                stored += 1
            except (SecretRejectedError, MemoryStoreError, SemanticMemoryError):
                rejected += 1
        return {"state": "ok", "stored": stored, "rejected": rejected,
                "invalid": invalid}

    # ------------------------------------------------------------ plumbing
    @property
    def _path(self) -> Path:
        return self.store._path

    def close(self) -> None:
        self.store.close()


# ------------------------------------------------------------------ math
def _cosine(a: List[float], b: List[float]) -> float:
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def _windows_overlap(fa: Optional[float], ua: Optional[float],
                     fb: Optional[float], ub: Optional[float]) -> bool:
    a0 = fa if fa is not None else -math.inf
    a1 = ua if ua is not None else math.inf
    b0 = fb if fb is not None else -math.inf
    b1 = ub if ub is not None else math.inf
    return a0 < b1 and b0 < a1