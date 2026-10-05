"""Minimal Jev judge over OpenRouter's alpha decisions endpoint.

Wire shape (verified against the ``jev-use`` ``openrouter.ts`` adapter):

* ``POST https://openrouter.ai/api/alpha/decisions`` (alpha path, may move —
  hence a single module constant, not a literal scattered across callers).
* ``Authorization: Bearer <OPENROUTER_API_KEY>`` (never in the body, never
  in logs — length-only fingerprints).
* Native-dialect JSON body ``{state, questions, model}`` where each question
  is a ``check`` (boolean), ``pick`` (one of options), or ``rate`` (0-1)
  question; the native answers parse back to typed
  ``{answer, confidence, escalate}`` triples.

Fail-CLOSED everywhere: transport errors, timeouts, missing keys, and
malformed payloads return a typed refusal (``ok: False`` + ``JEV_*`` code)
and NEVER a fabricated judgment (no ``answers`` key on failure).

Only stdlib (``json``/``urllib``) — no new dependencies. The HTTP layer is
an injectable ``http_post(url, body, key, timeout_s)`` seam so tests run
hermetic; production uses :func:`_default_http_post`.
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

#: Alpha decisions endpoint (may move upstream — one constant, one place).
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"

#: Credential name. Read via the ``_web_env_key``-style file/env source
#: (configured path -> canonical ``~/.config`` .env -> runtime .env ->
#: process env). NEVER printed (length-only in logs), never committed.
KEY_NAME = "OPENROUTER_API_KEY"

#: Default model id. SINGLE SOURCE — pass ``model=`` explicitly per call;
#: OpenRouter's public list currently shows ``typesafe/jev-router`` while the
#: adapter default was ``typesafe/jev-latest``; never hardcode one id in two
#: places, so callers read this constant or set ``jev.model`` in cfg.
DEFAULT_MODEL = "typesafe/jev-latest"

#: Bounded wait per judgment (the alpha endpoint is a judge, not a stream).
DEFAULT_TIMEOUT_S = 15.0

#: Env switch selecting the deterministic mock backend (tests / no-key hosts).
MOCK_ENV_VAR = "JEV_BACKEND"
MOCK_ENV_VALUE = "mock"

#: Question kinds the native dialect supports.
QUESTION_KINDS = ("check", "pick", "rate")

HttpPost = Callable[[str, Dict[str, Any], str, float], Any]


# ---------------------------------------------------------------------------
# key source (mirrors orchestrator._web_env_key, no instance needed)
# ---------------------------------------------------------------------------

def _cfg_get(cfg: Any, *path: str, default: Any = "") -> Any:
    try:
        get = getattr(cfg, "get", None)
        if callable(get):
            try:
                return get(*path, default=default)
            except TypeError:
                pass
        node: Any = cfg
        for seg in path:
            node = node.get(seg) if isinstance(node, dict) else None
            if node is None:
                return default
        return node
    except Exception:
        return default


def _read_key(cfg: Any = None) -> str:
    """Read ``OPENROUTER_API_KEY`` without ever logging the value."""
    candidates: List[str] = []
    for section_key in ("openrouter_env", "deepseek_env"):
        try:
            configured = str(
                _cfg_get(cfg, "intent", section_key, default="") or "")
        except Exception:
            configured = ""
        if configured.strip() and configured.strip() not in candidates:
            candidates.append(configured.strip())
    if "~/.config/qubit-voicebridge/.env" not in candidates:
        candidates.append("~/.config/qubit-voicebridge/.env")
    try:
        from ..config import canonical_runtime_env_path as _runtime_env_path
        _runtime_env = str(_runtime_env_path())
    except Exception:
        _runtime_env = ""
    if _runtime_env and _runtime_env not in candidates:
        candidates.append(_runtime_env)
    for candidate in candidates:
        try:
            lines = Path(candidate).expanduser().read_text(
                errors="replace").splitlines()
        except Exception:
            continue
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(KEY_NAME + "="):
                return (stripped.split("=", 1)[1].strip()
                        .strip('"').strip("'"))
    return os.environ.get(KEY_NAME, "")


def key_present(cfg: Any = None) -> bool:
    """True when a non-empty key resolves (value never exposed)."""
    try:
        return bool(_read_key(cfg))
    except Exception:
        return False


def key_fingerprint(key: str) -> str:
    """Length-only key descriptor for logs — the value never appears."""
    try:
        n = len(str(key or ""))
    except Exception:
        n = 0
    return f"{KEY_NAME}:{'present' if n else 'absent'}(len={n})"


# ---------------------------------------------------------------------------
# native-dialect body + answer parse
# ---------------------------------------------------------------------------

def to_native_body(state: Dict[str, Any],
                   questions: List[Dict[str, Any]],
                   model: str) -> Dict[str, Any]:
    """Build the native-dialect request body (credential never in-body)."""
    return {"model": str(model or DEFAULT_MODEL),
            "state": dict(state or {}),
            "questions": [dict(q) for q in (questions or [])]}


def _clamp_confidence(raw: Any) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


def parse_native_answers(
        payload: Any,
        questions: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]],
                                                  List[str]]:
    """Parse the native answers payload into typed verdicts.

    Returns ``(answers, problems)``. A question with a missing/null answer
    escalates (fail-open toward a human/local fallback, decided by the
    CALLER — this function never acts, only reports).
    """
    by_id = {str(q.get("id")): q for q in (questions or [])
             if isinstance(q, dict) and q.get("id") is not None}
    if not isinstance(payload, dict) or not isinstance(
            payload.get("answers"), list):
        return [], ["payload is not a native answers object"]
    answers: List[Dict[str, Any]] = []
    problems: List[str] = []
    for raw in payload["answers"]:
        if not isinstance(raw, dict) or raw.get("id") is None:
            problems.append("answer without id")
            continue
        qid = str(raw.get("id"))
        asked = by_id.get(qid, {})
        answer = raw.get("answer")
        escalate = bool(raw.get("escalate", False)) or answer is None
        answers.append({"id": qid,
                        "kind": str(asked.get("kind", "check")),
                        "answer": answer,
                        "confidence": _clamp_confidence(
                            raw.get("confidence", 0.0)),
                        "escalate": escalate,
                        "raw": {k: v for k, v in raw.items()
                                if k not in ("answer",)}})
    return answers, problems


# ---------------------------------------------------------------------------
# mock backend (deterministic canned verdicts — no network, no key)
# ---------------------------------------------------------------------------

def mock_judge(state: Dict[str, Any],
               questions: List[Dict[str, Any]],
               model: str = "") -> Dict[str, Any]:
    """Deterministic canned verdicts for tests and no-key hosts."""
    _ = state  # state shapes the question text in production, not the mock
    answers: List[Dict[str, Any]] = []
    for q in questions or []:
        if not isinstance(q, dict):
            continue
        qid = str(q.get("id", ""))
        kind = str(q.get("kind", "check"))
        if kind == "pick":
            options = q.get("options") or []
            answer: Any = options[0] if options else None
        elif kind == "rate":
            answer = 0.5
        else:  # "check" and unknown kinds degrade to boolean
            kind = "check" if kind == "check" else kind
            answer = True
        answers.append({"id": qid, "kind": kind, "answer": answer,
                        "confidence": 1.0,
                        "escalate": bool(q.get("escalate", False)),
                        "raw": {"backend": "mock"}})
    return {"ok": True, "backend": "mock",
            "model": str(model or DEFAULT_MODEL), "answers": answers}


# ---------------------------------------------------------------------------
# live backend (stdlib urllib; injectable seam for hermetic tests)
# ---------------------------------------------------------------------------

def _default_http_post(url: str, body: Dict[str, Any], key: str,
                       timeout_s: float) -> Any:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    # NOTE: the key travels ONLY in the Authorization header, built here at
    # the last moment — it never enters the body, logs, or exceptions.
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except socket.timeout as exc:
        raise TimeoutError(f"jev decisions timeout ({timeout_s}s)") from exc


def _refusal(code: str, detail: str) -> Dict[str, Any]:
    return {"ok": False, "code": code, "detail": detail,
            "backend": "live", "endpoint": DECISIONS_URL}


def resolve_model(model: str = "", cfg: Any = None) -> str:
    """Explicit arg > ``jev.model`` cfg > :data:`DEFAULT_MODEL` (one place)."""
    if str(model or "").strip():
        return str(model).strip()
    try:
        configured = str(_cfg_get(cfg, "jev", "model", default="") or "")
    except Exception:
        configured = ""
    return configured.strip() or DEFAULT_MODEL


def judge(state: Dict[str, Any],
          questions: List[Dict[str, Any]],
          model: str = "",
          cfg: Any = None,
          *,
          backend: str = "",
          timeout_s: float = DEFAULT_TIMEOUT_S,
          api_key: str = "",
          http_post: Optional[HttpPost] = None) -> Dict[str, Any]:
    """Ask Jev for typed verdicts; fail CLOSED (never a fabricated judgment).

    ``backend="mock"`` (or ``JEV_BACKEND=mock`` in the environment) returns
    deterministic canned verdicts with no network and no key. Otherwise the
    live alpha endpoint is used: a missing key is a loud ``JEV_KEY_ABSENT``
    refusal, and transport/timeout/malformed failures are typed refusals
    with NO ``answers`` key.
    """
    selected = resolve_model(model, cfg)
    use_mock = (str(backend or "").strip().lower() == MOCK_ENV_VALUE
                or os.environ.get(MOCK_ENV_VAR, "").strip().lower()
                == MOCK_ENV_VALUE)
    if use_mock or str(backend or "").strip().lower() == MOCK_ENV_VALUE:
        return mock_judge(state, questions, selected)
    key = str(api_key or "") or _read_key(cfg)
    if not key:
        return {"ok": False, "code": "JEV_KEY_ABSENT",
                "detail": (f"{KEY_NAME} is not configured (checked "
                           "intent.openrouter_env, the canonical "
                           "~/.config/qubit-voicebridge/.env, the runtime "
                           ".env, and the environment); no judgment made. "
                           "Set the key to unlock the live path."),
                "backend": "live", "endpoint": DECISIONS_URL}
    post = http_post or _default_http_post
    body = to_native_body(state, questions, selected)
    try:
        payload = post(DECISIONS_URL, body, key, float(timeout_s))
    except TimeoutError:
        return _refusal("JEV_TIMEOUT",
                        f"decisions endpoint timed out ({timeout_s}s); "
                        "no judgment made — caller keeps local heuristics.")
    except (OSError, urllib.error.URLError, ValueError) as exc:
        # ValueError: stdlib URL/SSL misconfiguration surfaces here; the
        # message is transport-shape only (the key never enters exceptions).
        return _refusal("JEV_TRANSPORT_ERROR",
                        f"decisions endpoint unreachable "
                        f"({type(exc).__name__}); no judgment made — "
                        "caller keeps local heuristics.")
    except Exception as exc:  # noqa: BLE001 - fail closed, never fabricate
        return _refusal("JEV_TRANSPORT_ERROR",
                        f"decisions endpoint error "
                        f"({type(exc).__name__}); no judgment made — "
                        "caller keeps local heuristics.")
    answers, problems = parse_native_answers(payload, questions or [])
    if problems and not answers:
        return _refusal("JEV_MALFORMED",
                        f"unparseable decisions payload ({problems[0]}); "
                        "no judgment made — caller keeps local heuristics.")
    return {"ok": True, "backend": "live", "model": selected,
            "answers": answers, "problems": problems}
