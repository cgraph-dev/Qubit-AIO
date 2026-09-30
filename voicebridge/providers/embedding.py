"""Canonical local embedding adapter (one Ollama ``/api/embeddings`` call site).

Every embedding request in the product goes through
:func:`voicebridge.providers.gateway.embed_texts`, which resolves the
``embedding`` role through the canonical model/profile resolver and then calls
:func:`embed_local` here. Keeping the HTTP call in exactly one module is what
lets the router (L1 semantic routing) and semantic memory share the exact
CPU-only (``num_gpu=0``) placement contract instead of each issuing its own
Ollama request.

Typed failures only: an unavailable/empty embedding endpoint raises
:class:`EmbeddingError` and is never silently replaced by a different model or
a zero vector.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, List

DEFAULT_EMBED_URL = "http://127.0.0.1:11434"
DEFAULT_EMBED_MODEL = "nomic-embed-text"


class EmbeddingError(Exception):
    """Typed embedding failure (never a fabricated vector)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail

    def to_dict(self) -> dict:
        return {"ok": False, "code": self.code, "detail": self.detail}


def runner_url(cfg: Any) -> str:
    """The resident runner base URL from canonical config (no network probe).

    Mirrors the runner-contract precedence but never fetches a digest, so an
    embedding request cannot trigger a model HTTP probe just to learn a URL.
    """
    if cfg is not None:
        for path in (("intent", "runner", "url"),
                     ("intent", "local_llm", "url"),
                     ("intent", "ollama_url")):
            try:
                value = cfg.get(*path, default=None)
            except Exception:
                value = None
            if isinstance(value, str) and value.strip():
                return value.strip()
    return DEFAULT_EMBED_URL


def embed_local(texts: List[str], *, url: str = "", model: str = "",
                timeout_s: float = 10.0, num_gpu: int = 0) -> List[List[float]]:
    """Embed ``texts`` one at a time via the local Ollama endpoint.

    ``num_gpu=0`` keeps placement CPU-only so the resident generative runner is
    never evicted (same contract the L1 router and semantic memory relied on).
    """
    base = (str(url or "").strip() or DEFAULT_EMBED_URL).rstrip("/")
    mdl = str(model or "").strip() or DEFAULT_EMBED_MODEL
    vecs: List[List[float]] = []
    for text in texts:
        payload = {"model": mdl, "prompt": text,
                   "options": {"num_gpu": int(num_gpu)}}
        req = urllib.request.Request(
            f"{base}/api/embeddings",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
                body = json.loads(resp.read().decode())
        except Exception as exc:  # noqa: BLE001 - typed, redacted
            raise EmbeddingError(
                "EMBEDDING_UNAVAILABLE",
                f"embedding provider unavailable: {str(exc)[:120]}") from exc
        vector = body.get("embedding")
        if not vector:
            raise EmbeddingError("EMPTY_EMBEDDING",
                                 "empty embedding response")
        vecs.append([float(x) for x in vector])
    return vecs


__all__ = ["EmbeddingError", "embed_local", "runner_url",
           "DEFAULT_EMBED_URL", "DEFAULT_EMBED_MODEL"]
