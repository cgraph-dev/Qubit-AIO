"""L1 optional CPU semantic route matching (Packet 4C).

ADAPT of pinned Semantic Router semantics (docs/archive/2026-09-21/UPSTREAM_REUSE.md R6):
per-route thresholds, top-one/top-two margin gate, explicit NO-MATCH,
calibration/evaluation on labeled examples. stdlib-only: vectors are plain
float lists, scoring is a dot product; embeddings come from the EXISTING
Ollama endpoint (nomic-embed-text) and are precomputed once per process.

Every embedding request is issued with num_gpu=0 (CPU-only placement) and
the caller is expected to verify zero generative-runner eviction.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

ROUTE_SCHEMA = "voicebridge.l1-routes/1"
NO_MATCH = None  # explicit sentinel: uncertainty never becomes authority
CLASSIFICATION_PREFIX = "classification: "   # nomic task prefix (model card)
QUERY_PREFIX = "search_query: "

DEFAULT_THRESHOLD = 0.55
DEFAULT_MARGIN = 0.05


@dataclass
class L1Decision:
    route: str
    score: float
    runner_up: Optional[str]
    margin: float
    threshold: float

    @property
    def matched(self) -> bool:
        return self.score >= self.threshold and self.margin >= DEFAULT_MARGIN


@dataclass
class SemanticRouter:
    routes: Dict[str, List[str]]
    centroids: Dict[str, List[float]] = field(default_factory=dict)
    thresholds: Dict[str, float] = field(default_factory=dict)
    # Canonical resolver config for the ``embedding`` role. When set, embedding
    # requests resolve provider/model through the one resolver; the Ollama call
    # itself lives only in ``providers.embedding``.
    cfg: Any = None
    _embed_fn: Any = None

    # ---------------------------------------------------------- embedding
    @staticmethod
    def _default_embed(texts: List[str], url: str, model: str,
                       timeout_s: float) -> List[List[float]]:
        """Shared local embedding adapter (no per-caller Ollama request)."""
        from ..providers import embedding as _embedding
        try:
            return _embedding.embed_local(texts, url=url, model=model,
                                          timeout_s=timeout_s)
        except _embedding.EmbeddingError as exc:
            raise RuntimeError(exc.detail) from exc

    def embed(self, texts: List[str], *, query: bool = False,
              url: str = "http://127.0.0.1:11434",
              model: str = "nomic-embed-text", timeout_s: float = 10.0,
             ) -> List[List[float]]:
        prefix = QUERY_PREFIX if query else CLASSIFICATION_PREFIX
        prepared = [(prefix + t)[:600] for t in texts]
        if self._embed_fn is not None:
            return self._embed_fn(prepared, url, model, timeout_s)
        # Route through the canonical embedding role + shared adapter. The
        # resolver's effective model applies unless a caller pinned one.
        from ..providers import gateway as _gateway
        try:
            return _gateway.embed_texts(prepared, self.cfg, url=url, model=model,
                                        timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001 - preserve RuntimeError contract
            raise RuntimeError(str(exc)) from exc

    # ------------------------------------------------------------ fitting
    def fit(self, url: str = "http://127.0.0.1:11434",
            model: str = "nomic-embed-text", **kw) -> None:
        """Precompute per-route centroid vectors once."""
        flat: List[str] = []
        owner: List[str] = []
        for route, examples in self.routes.items():
            for ex in examples:
                flat.append(ex)
                owner.append(route)
        vecs = self.embed(flat, url=url, model=model, **kw)
        acc: Dict[str, List[List[float]]] = {r: [] for r in self.routes}
        for route, v in zip(owner, vecs):
            acc[route].append(v)
        self.centroids = {r: _centroid(vs) for r, vs in acc.items() if vs}

    def calibrate(self, labeled: List[Tuple[str, str]],
                  grid: Optional[List[float]] = None,
                  min_margin: float = DEFAULT_MARGIN, **kw) -> Dict[str, float]:
        """Per-route threshold sweep on the CALIBRATION slice (never the
        held-out report slice). Picks the lowest threshold whose accuracy
        is maximal (R6 threshold_search semantics, deterministic grid)."""
        if not self.centroids:
            raise RuntimeError("fit() required before calibrate()")
        grid = grid or [round(0.30 + 0.05 * i, 2) for i in range(13)]
        texts = [t for t, _ in labeled]
        vecs = self.embed(texts, query=True, **kw)
        # per-route threshold sweep: accuracy counts accept-correct for the
        # route's own items AND reject (no-match) for every other item.
        best: Dict[str, Tuple[float, float]] = {}   # route -> (acc, thr)
        for route in self.centroids:
            route_items = [(v, lbl) for (lbl, txt), v in
                           zip(labeled, vecs) if lbl == route]
            other = [(v, lbl) for (lbl, txt), v in
                     zip(labeled, vecs) if lbl != route]
            route_acc_best = (0.0, DEFAULT_THRESHOLD)
            for thr in grid:
                correct = total = 0
                for v, lbl in route_items:
                    d = _decide(v, self.centroids, route, thr, min_margin)
                    total += 1
                    correct += 1 if d == route else 0
                for v, lbl in other:
                    d = _decide(v, self.centroids, route, thr, min_margin)
                    total += 1
                    correct += 1 if d is None else 0
                acc = correct / total if total else 0.0
                if acc > route_acc_best[0]:
                    route_acc_best = (acc, thr)
            best[route] = route_acc_best
        self.thresholds = {r: b[1] for r, b in best.items()}
        return dict(self.thresholds)

    # ---------------------------------------------------------- inference
    def decide(self, query_text: str, *, url: str = "http://127.0.0.1:11434",
               model: str = "nomic-embed-text", **kw) -> Optional[L1Decision]:
        if not self.centroids:
            raise RuntimeError("fit() required before decide()")
        v = self.embed([query_text], query=True, url=url, model=model, **kw)[0]
        return self.decide_vector(v)

    def decide_vector(self, v: List[float]) -> Optional[L1Decision]:
        ranked = _rank(v, self.centroids)
        top_route, top_score = ranked[0]
        runner = ranked[1][0] if len(ranked) > 1 else None
        second_score = _score(v, self.centroids[runner]) if runner else 0.0
        thr = self.thresholds.get(top_route, DEFAULT_THRESHOLD)
        decision = L1Decision(route=top_route, score=round(top_score, 4),
                              runner_up=runner,
                              margin=round(top_score - second_score, 4),
                              threshold=thr)
        return decision if decision.matched else NO_MATCH


# ------------------------------------------------------------------ math
def _centroid(vecs: List[List[float]]) -> List[float]:
    n = len(vecs)
    dim = len(vecs[0])
    out = [0.0] * dim
    for v in vecs:
        for i in range(dim):
            out[i] += v[i]
    return _norm([x / n for x in out])


def _norm(v: List[float]) -> List[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _score(v: List[float], centroid: List[float]) -> float:
    """True cosine — defensive normalization on both sides so a non-unit
    embedding response can never inflate scores past thresholds."""
    nv = math.sqrt(sum(x * x for x in v)) or 1.0
    nc = math.sqrt(sum(x * x for x in centroid)) or 1.0
    return sum(a * b for a, b in zip(v, centroid)) / (nv * nc)


def _rank(v: List[float], centroids: Dict[str, List[float]]
          ) -> List[Tuple[str, float]]:
    scored = [(r, _score(v, c)) for r, c in centroids.items()]
    scored.sort(key=lambda x: -x[1])
    return scored


def _decide(v: List[float], centroids: Dict[str, List[float]],
            forced_route: str, threshold: float,
            min_margin: float) -> Optional[str]:
    """Calibration helper: would `forced_route`'s threshold accept this?"""
    top_route, top_score = _rank(v, centroids)[0]
    if top_route != forced_route:
        return None
    second = _second_score(v, centroids, forced_route)
    if top_score >= threshold and (top_score - second) >= min_margin:
        return forced_route
    return None


def _second_score(v: List[float], centroids: Dict[str, List[float]],
                  except_route: str) -> float:
    others = [c for r, c in centroids.items() if r != except_route]
    return max((_score(v, c) for c in others), default=0.0)


# ------------------------------------------------------------- route set
def load_default_routes() -> Dict[str, List[str]]:
    """Versioned sanitized route examples (l1-routes/1). Synthetic only."""
    return {
        "conversation": [
            "how was your day so far?",
            "tell me something interesting about space",
            "give me a two sentence fun fact about octopuses",
            "say something nice about rainy days",
            "what is the capital of France",
        ],
        "coding": [
            "write a python script that renames all files to lowercase",
            "fix the failing unit test in the repo",
            "why does this function throw an exception",
            "refactor the login module and add tests",
            "inspect the project and run the test suite",
        ],
        "local": [
            "set the volume to thirty percent",
            "pause the music",
            "open the text editor",
            "take a screenshot of my screen",
            "unmute the microphone",
        ],
        "clarify": [
            "can you take care of that thing we talked about earlier?",
            "fix it please",
            "uhh th-the thingy wth the umm flr plz",
            "do the usual with the report from yesterday",
            "make it better somehow",
        ],
    }
