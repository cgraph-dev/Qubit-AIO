"""L1.5 EmbeddingGemma-2 voice-command augmenter (disabled by default).

This layer is a *last* semantic hint for the deterministic voice-command
registry. It is consulted only when all of the following hold:

* ``intent.router.l1_5_enabled`` is true;
* L0 (:mod:`voicebridge.router.l0`) found nothing;
* ``voice_commands.resolve()`` returned ``UNKNOWN``;
* no confirmation is pending;
* the utterance carries no control/negation marker.

It proposes at most ONE existing registry entry by max-similarity over that
entry's alias/example prototypes (never centroids). Anything else is an
explicit NO-MATCH and the existing classifier/LLM path stays authoritative.

Hard safety contract: EG2 must never decide a high-risk intent. A candidate
is rejected when the entry is confirmation-gated, risk-keyed, destructive,
financial, or when the utterance itself carries a stop/cancel/negation
marker. High-risk commands stay on the deterministic path.

The ONNX model is text-only and optional: unavailable runtime/model yields
NO_MATCH. No network download is performed here.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

NO_MATCH = None
TASK_PREFIX = "task: classification | query: "
DEFAULT_THRESHOLD = 0.87
DEFAULT_MARGIN = 0.002
DEFAULT_MODEL_DIR = (
    "~/.local/share/qubit-voicebridge/models/embeddinggemma-2-onnx")
EMBED_DIM = 768
DEFAULT_THREADS = 4

_CONTROL_MARKERS = re.compile(
    r"\b(?:stop|halt|pause|resume|cancel|abort|kill|terminate|shutdown|"
    r"reboot|restart|reject|deny|refuse|revoke|unpair|delete|remove|wipe|"
    r"forget|erase|destroy|drop|purge|truncate|overwrite|send|post|pay|buy|"
    r"purchase|transfer|invoice|payment|money|refund|approve|accept|confirm|"
    r"yes|no|don'?t|do\s+not|never|without|emergency|e-?stop|danger|"
    r"destructive|rm|sudo|archive)\b",
    re.I,
)

_ENTRY_MARKERS = re.compile(
    r"\b(?:delete|remove|forget|wipe|erase|destroy|drop|purge|revoke|unpair|"
    r"pair|reject|deny|send|approve|accept|pay|buy|purchase|transfer|invoice|"
    r"payment|money|refund|wire|deposit|execute|fire|install|uninstall|apply|"
    r"rollback|enable|disable|configure|unconfigure|grant|link|write|set|"
    r"patch|reset|clear|cancel|abort|kill|shutdown|reboot|restart|stop|pause|"
    r"resume|emergency|e-?stop|danger|destructive|overwrite|truncate|upload|"
    r"download|sync|migrate|archive)\b",
    re.I,
)

class ModelUnavailable(RuntimeError):
    """Raised when the optional EG2 runtime/model cannot be loaded."""


@dataclass(frozen=True)
class L15Decision:
    entry_id: str
    score: float
    runner_up: Optional[str]
    margin: float
    threshold: float
    prototype: str


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def is_risky_utterance(text: str) -> bool:
    return bool(_CONTROL_MARKERS.search(str(text or "")))


def entry_is_high_risk(entry: Any) -> bool:
    if entry is None:
        return True
    if bool(getattr(entry, "confirm", False)):
        return True
    if _ENTRY_MARKERS.search(str(getattr(entry, "id", "") or "")):
        return True
    texts: List[str] = [str(getattr(entry, "title", "") or "")]
    for alias in getattr(entry, "aliases", ()) or ():
        if not str(alias).startswith("rx:"):
            texts.append(str(alias))
    return any(_ENTRY_MARKERS.search(t) for t in texts)


def _norm(vector: Sequence[float]) -> List[float]:
    values = [float(x) for x in vector]
    length = math.sqrt(sum(x * x for x in values)) or 1.0
    return [x / length for x in values]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def _prototype_texts(entries: Iterable[Any]) -> Tuple[List[str], List[str]]:
    texts: List[str] = []
    owners: List[str] = []
    for entry in entries:
        candidates: List[str] = [str(getattr(entry, "title", "") or "")]
        candidates.extend(
            str(a) for a in (getattr(entry, "aliases", ()) or ())
            if not str(a).startswith("rx:"))
        candidates.extend(str(x) for x in (getattr(entry, "examples", ()) or ()))
        for text in candidates:
            text = normalize_text(text)
            if text:
                texts.append(text)
                owners.append(str(entry.id))
    return texts, owners


class RegistryAugmenter:
    def __init__(self, entries: Sequence[Any], cfg: Any,
                 embedder: Any) -> None:
        self.entries = list(entries)
        self.entry_map = {str(e.id): e for e in self.entries}
        self.cfg = cfg
        self.embedder = embedder
        self.threshold = float(cfg.get(
            "intent", "router", "l1_5_threshold",
            default=DEFAULT_THRESHOLD))
        self.margin = float(cfg.get(
            "intent", "router", "l1_5_margin", default=DEFAULT_MARGIN))
        self._prototype_texts: List[str] = []
        self._prototype_owners: List[str] = []
        self._prototype_vectors: List[List[float]] = []
        self._groups: Dict[str, List[int]] = {}
        self._matrix = None

    def fit(self) -> None:
        texts, owners = _prototype_texts(self.entries)
        if not texts:
            raise ModelUnavailable("no registry prototypes")
        raw = self.embedder.embed(list(texts))
        if len(raw) != len(texts):
            raise ModelUnavailable("embedder returned the wrong count")
        vectors = []
        for vec in raw:
            vector = _norm(vec)
            if len(vector) != EMBED_DIM:
                raise ModelUnavailable(
                    f"expected {EMBED_DIM}-d embedding, got {len(vector)}")
            vectors.append(vector)
        self._prototype_texts = texts
        self._prototype_owners = owners
        groups: Dict[str, List[int]] = {}
        for index, owner in enumerate(owners):
            groups.setdefault(owner, []).append(index)
        self._groups = groups
        try:
            import numpy as _np
        except Exception:
            self._matrix = None
            self._prototype_vectors = vectors
        else:
            self._matrix = _np.asarray(vectors, dtype=_np.float32)
            self._prototype_vectors = []

    def _entry_scores(self, query: Sequence[float]) -> List[Tuple[str, float]]:
        if self._matrix is not None:
            import numpy as _np
            q = _np.asarray(_norm(query), dtype=_np.float32)
            scores = self._matrix @ q
            ranked = [
                (entry_id, float(scores[indexes].max()))
                for entry_id, indexes in self._groups.items()
            ]
        else:
            ranked = [
                (entry_id, max(_cosine(query, self._prototype_vectors[i])
                               for i in indexes))
                for entry_id, indexes in self._groups.items()
            ]
        ranked.sort(key=lambda item: -item[1])
        return ranked

    def _best_prototype(self, entry_id: str, query: Sequence[float]) -> str:
        indexes = self._groups.get(entry_id) or []
        if not indexes:
            return ""
        if self._matrix is not None:
            import numpy as _np
            q = _np.asarray(_norm(query), dtype=_np.float32)
            scores = self._matrix[indexes] @ q
            return self._prototype_texts[indexes[int(scores.argmax())]]
        best = ""
        best_score = -2.0
        for index in indexes:
            score = _cosine(query, self._prototype_vectors[index])
            if score > best_score:
                best_score = score
                best = self._prototype_texts[index]
        return best

    def decide(self, text: str) -> Optional[L15Decision]:
        text = normalize_text(text)
        if not text or is_risky_utterance(text):
            return NO_MATCH
        raw = self.embedder.embed([text])
        if len(raw) != 1:
            return NO_MATCH
        query = _norm(raw[0])
        if len(query) != EMBED_DIM:
            return NO_MATCH
        return self.decide_vector(query)

    def decide_vector(self, query: Sequence[float]) -> Optional[L15Decision]:
        if not self._groups:
            return NO_MATCH
        ranked = self._entry_scores(query)
        if not ranked:
            return NO_MATCH
        entry_id, score = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else None
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = score - second
        if score < self.threshold or margin < self.margin:
            return NO_MATCH
        entry = self.entry_map.get(entry_id)
        if entry_is_high_risk(entry):
            return NO_MATCH
        return L15Decision(
            entry_id=entry_id,
            score=round(score, 4),
            runner_up=runner_up,
            margin=round(margin, 4),
            threshold=self.threshold,
            prototype=self._best_prototype(entry_id, query),
        )


class OnnxEg2Embedder:
    """Text-only EmbeddingGemma-2 ONNX embedder (no torch)."""

    def __init__(self, model_dir: str | Path, *, threads: int = DEFAULT_THREADS):
        self.model_dir = Path(model_dir).expanduser()
        self.threads = max(1, int(threads))
        self._session = None
        self._tokenizer = None
        self._np = None

    def _locate(self) -> Tuple[Path, Path]:
        root = self.model_dir
        tokenizer_path = root / "tokenizer.json"
        if not tokenizer_path.is_file():
            raise ModelUnavailable(f"tokenizer missing under {root}")
        for candidate in (
            root / "onnx" / "model_q4.onnx",
            root / "model_q4.onnx",
            root / "onnx" / "model.onnx",
            root / "model.onnx",
        ):
            if candidate.is_file():
                return candidate, tokenizer_path
        raise ModelUnavailable(f"ONNX model missing under {root}")

    def load(self) -> None:
        if self._session is not None:
            return
        try:
            import numpy as _np
            import onnxruntime as _ort
            from tokenizers import Tokenizer
        except Exception as exc:
            raise ModelUnavailable(str(exc)) from exc
        model_path, tokenizer_path = self._locate()
        options = _ort.SessionOptions()
        options.intra_op_num_threads = self.threads
        options.graph_optimization_level = (
            _ort.GraphOptimizationLevel.ORT_ENABLE_ALL)
        try:
            self._session = _ort.InferenceSession(
                str(model_path), options, providers=["CPUExecutionProvider"])
            self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        except Exception as exc:
            raise ModelUnavailable(str(exc)) from exc
        self._np = _np

    def embed(self, texts: Sequence[str], *, batch_size: int = 32
              ) -> List[List[float]]:
        self.load()
        assert self._session is not None and self._tokenizer is not None
        assert self._np is not None
        np = self._np
        prepared = [TASK_PREFIX + normalize_text(text) for text in texts]
        out: List[List[float]] = []
        for start in range(0, len(prepared), max(1, batch_size)):
            chunk = prepared[start:start + max(1, batch_size)]
            encodings = [self._tokenizer.encode(text) for text in chunk]
            width = max(1, max(len(enc.ids) for enc in encodings))
            ids = np.zeros((len(encodings), width), dtype=np.int64)
            mask = np.zeros_like(ids)
            for row, encoding in enumerate(encodings):
                length = len(encoding.ids)
                ids[row, :length] = encoding.ids
                mask[row, :length] = 1
            features = np.zeros((0, 512), dtype=np.float32)
            outputs = self._session.run(None, {
                "input_ids": ids,
                "attention_mask": mask,
                "image_features": features,
                "video_features": features,
                "audio_features": features,
            })
            embeddings = outputs[1]
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            out.extend((embeddings / norms).astype(np.float32).tolist())
        return out


_CFG_CACHE: Any = None


def _load_cfg() -> Any:
    global _CFG_CACHE
    if _CFG_CACHE is None:
        from ..config import Config
        _CFG_CACHE = Config.load()
    return _CFG_CACHE


def _enabled(cfg: Any) -> bool:
    return bool(cfg.get("intent", "router", "l1_5_enabled", default=False))


def _default_entries() -> List[Any]:
    from .. import voice_commands
    return list(voice_commands._iter_entries())


def _signature(entries: Sequence[Any], model_dir: str) -> str:
    digest = hashlib.sha256()
    digest.update(str(EMBED_DIM).encode())
    digest.update(str(model_dir).encode())
    texts, owners = _prototype_texts(entries)
    for text, owner in zip(texts, owners):
        digest.update(text.encode("utf-8"))
        digest.update(b"\0")
        digest.update(owner.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _cache_path(model_dir: str) -> Path:
    return Path(model_dir).expanduser() / ".l1_5_proto_cache.npz"


def _load_cached(entry_list: Sequence[Any], cfg: Any, embedder: Any,
                 model_dir: str) -> Optional[RegistryAugmenter]:
    path = _cache_path(model_dir)
    if not path.is_file():
        return None
    try:
        import numpy as np
        data = np.load(path, allow_pickle=False)
        if str(data["signature"]) != _signature(entry_list, model_dir):
            return None
        vectors = np.asarray(data["vectors"], dtype=np.float32)
        texts = [str(x) for x in data["texts"]]
        owners = [str(x) for x in data["owners"]]
    except Exception:
        return None
    if vectors.ndim != 2 or vectors.shape[1] != EMBED_DIM \
            or vectors.shape[0] != len(texts) or len(texts) != len(owners):
        return None
    augmenter = RegistryAugmenter(entry_list, cfg, embedder)
    augmenter._prototype_texts = texts
    augmenter._prototype_owners = owners
    augmenter._prototype_vectors = []
    groups: Dict[str, List[int]] = {}
    for index, owner in enumerate(owners):
        groups.setdefault(owner, []).append(index)
    augmenter._groups = groups
    augmenter._matrix = vectors
    return augmenter


def _save_cache(augmenter: RegistryAugmenter, model_dir: str) -> None:
    if augmenter._matrix is None:
        return
    try:
        import numpy as np
        np.savez_compressed(
            _cache_path(model_dir),
            vectors=np.asarray(augmenter._matrix, dtype=np.float32),
            texts=np.asarray(augmenter._prototype_texts),
            owners=np.asarray(augmenter._prototype_owners),
            signature=np.asarray(_signature(augmenter.entries, model_dir)),
        )
    except Exception:
        pass


def build_augmenter(cfg: Any, *, entries: Optional[Sequence[Any]] = None,
                    embedder: Any = None) -> Optional[RegistryAugmenter]:
    try:
        explicit = entries is not None or embedder is not None
        entry_list = list(entries) if entries is not None else _default_entries()
        if not explicit:
            entry_list = [e for e in entry_list if not entry_is_high_risk(e)]
        model_dir = cfg.get("intent", "router", "l1_5_model_dir",
                            default=DEFAULT_MODEL_DIR)
        if embedder is None:
            embedder = OnnxEg2Embedder(model_dir)
        if not explicit:
            cached = _load_cached(entry_list, cfg, embedder, str(model_dir))
            if cached is not None:
                return cached
        augmenter = RegistryAugmenter(entry_list, cfg, embedder)
        augmenter.fit()
        if not explicit:
            _save_cache(augmenter, str(model_dir))
        return augmenter
    except Exception:
        return None


_TEST_AUGMENTER: Optional[RegistryAugmenter] = None
_CACHE_KEY: Optional[Tuple[Any, ...]] = None
_CACHE: Optional[RegistryAugmenter] = None


def install_test_augmenter(augmenter: Optional[RegistryAugmenter]) -> None:
    global _TEST_AUGMENTER
    _TEST_AUGMENTER = augmenter


def reset_for_tests() -> None:
    global _TEST_AUGMENTER, _CACHE_KEY, _CACHE, _CFG_CACHE
    _TEST_AUGMENTER = None
    _CACHE_KEY = None
    _CACHE = None
    _CFG_CACHE = None


def _cached_augmenter(cfg: Any) -> Optional[RegistryAugmenter]:
    global _CACHE_KEY, _CACHE
    key = (
        str(cfg.get("intent", "router", "l1_5_model_dir",
                    default=DEFAULT_MODEL_DIR)),
        str(cfg.get("intent", "router", "l1_5_threshold",
                    default=DEFAULT_THRESHOLD)),
        str(cfg.get("intent", "router", "l1_5_margin", default=DEFAULT_MARGIN)),
    )
    if _CACHE_KEY != key:
        _CACHE = build_augmenter(cfg)
        _CACHE_KEY = key
    return _CACHE


def augment_unknown(text: str, *, cfg: Any = None,
                    pending_confirmation: bool = False,
                    entries: Optional[Sequence[Any]] = None,
                    embedder: Any = None) -> Optional[L15Decision]:
    """Return one low-risk registry candidate, or NO_MATCH.

    Called by ``voice_commands.resolve()`` after the deterministic registry
    returned UNKNOWN. Every gate is fail-closed.
    """
    if pending_confirmation:
        return NO_MATCH
    if not normalize_text(text):
        return NO_MATCH
    if is_risky_utterance(text):
        return NO_MATCH
    injected = entries is not None or embedder is not None
    test_augmenter = None if injected else _TEST_AUGMENTER
    if cfg is None and test_augmenter is None:
        try:
            cfg = _load_cfg()
        except Exception:
            return NO_MATCH
    if test_augmenter is None and cfg is not None and not _enabled(cfg):
        return NO_MATCH
    try:
        from .l0 import route as l0_route
        if l0_route(text) is not None:
            return NO_MATCH
    except Exception:
        return NO_MATCH
    if injected:
        augmenter = build_augmenter(cfg, entries=entries, embedder=embedder)
    elif test_augmenter is not None:
        augmenter = test_augmenter
    else:
        augmenter = _cached_augmenter(cfg)
    if augmenter is None:
        return NO_MATCH
    try:
        return augmenter.decide(text)
    except Exception:
        return NO_MATCH
