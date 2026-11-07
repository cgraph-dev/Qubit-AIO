"""Typed decisions from a decision model (Jev), as an OPTIONAL capability.

Derived from hermes-jev-skills (MIT, Copyright (c) 2026 Steve Darlow);
see ``NOTICE`` and ``PROVENANCE.json`` beside this file.

A decision model never writes text. It is given a *state* and typed *questions*
(``noul`` yes/no, ``choice`` one of a closed set, ``score`` a level on a rubric) and
returns a calibrated answer: a probability, a distribution, an expected level.

**The wire is bespoke, not chat-completions.** A request is
``{"state":…, "model":…, "questions":{name: {type,instructions,criteria}}}`` and a reply is
``{"model":…, "answers":{name: {type,…,probabilities}}, "usage":…}``. No ``messages``, no
``choices``, no ``content``. It therefore cannot be served by an OpenAI-compatible relay, and
this module will not pretend otherwise by prompting a chat model for JSON — that returns an
LLM's guess with a made-up confidence, which is the one thing a decision model exists not to
do. Callers that need one of these have to reach a host that speaks this contract.

One door speaks it: OpenCode **Zen** (``https://opencode.ai/zen/v1/systemone``) with
``jev-1.13-free``. Its free tier authenticates nothing, so **no key is required and none
should be sent**: ``Authorization: Bearer <anything>`` is refused 401 ("Invalid credential")
while the same body with no auth header answers 200 with ``billing_source: "free"``,
``cost: 0``. Two consequences for the design:

* decisions are available on a stock install with no signup and no credential — the strictest
  possible reading of "optional";
* when ``OPENCODE_ZEN_API_KEY`` *is* configured (paid Zen chat), the key is deliberately not
  sent here, because sending it breaks the door. ``TYPESAFE_API_KEY`` is the one credential
  that is sent, to ``https://api.typesafe.ai/v1/systemone``, when an operator has one.

``opencode-go`` is deliberately absent: it is a paid *chat* relay (``/zen/go/v1``) that lists
no decision model, refuses this contract, and would need a bearer that the decision door
rejects. See ``docs/JEV_INTEGRATION_NOTE.md``.

Fail-soft is the contract: a caller reports ``available: false`` with a typed reason instead of
guessing, for every way a decision can fail — network, server, malformed or self-contradictory
reply, oversized state, pinned-but-keyless provider. Nothing here raises for anything the
network, the server or the model does.
"""

from __future__ import annotations

import json
import logging
import math
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ── the doors ────────────────────────────────────────────────────────────────

PROVIDER_ZEN = "zen"
PROVIDER_TYPESAFE = "typesafe"

# (provider, key env var, endpoint, model, send_key). ``zen`` is the free tier: it authenticates
# nothing and REFUSES any bearer ("Invalid credential" 401), so its key is never sent even when
# one is configured. ``typesafe`` is a metered account and needs its key.
CANDIDATES: Tuple[Tuple[str, str, str, str, bool], ...] = (
    (PROVIDER_ZEN, "OPENCODE_ZEN_API_KEY", "https://opencode.ai/zen/v1/systemone", "jev-1.13-free", False),
    (PROVIDER_TYPESAFE, "TYPESAFE_API_KEY", "https://api.typesafe.ai/v1/systemone", "jev-latest", True),
)

DEFAULT_TIMEOUT = 4.0
MAX_RESPONSE_BYTES = 1_000_000
MAX_STATE_CHARS = 60_000
USER_AGENT = "QubitAgent-jev/1.0"

QUESTION_TYPES = ("choice", "score", "noul")
MIN_CRITERIA = 2
MAX_CHOICE_OPTIONS = 255
MAX_SCORE_LEVELS = 10
NOUL_CRITERIA_KEYS = ("true", "false")

# Reply invariants, taken from upstream client.py (which took its two tolerances from a
# validator running against the same API, jkudish/jev-mcp MIT lib.ts:11/:258):
# probabilities are a distribution (sum 1 ± slack), the chosen option is its argmax, and a
# score equals the mean of its own two-decimal distribution. A reply that breaks one of these
# is not "a weak answer", it is two answers, and must not be acted on.
PROBABILITY_SUM_TOLERANCE = 0.01 + 1e-12
SCORE_MEAN_TOLERANCE = 0.02 + 1e-12
ARGMAX_TOLERANCE = 1e-9
ROUNDING_STEP = 0.005


class JevUnavailable(RuntimeError):
    """No decision can be produced. ``code`` is short and safe to log.

    Every caller treats this as "act as you would have without a decision" — never as an
    instruction to guess.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ── key resolution (never os.environ directly) ───────────────────────────────

def _secret(name: str) -> Optional[str]:
    """One credential by env-var name through the active profile's secret scope.

    ``agent.secret_scope.get_secret`` is the only reader: it honours the profile scope under
    multiplexing and fails closed rather than leaking another profile's value. A read that
    raises (fail-closed ``UnscopedSecretError``, or any import problem) means "no key here".
    """
    try:
        from agent.secret_scope import get_secret

        value = get_secret(name)
    except Exception:  # noqa: BLE001 - an unreadable credential is an absent credential
        return None
    text = (value or "").strip()
    return text or None


def resolve_provider(preferred: Optional[str] = None) -> Optional[Tuple[str, Optional[str], str, str]]:
    """The door to use: ``(provider, key_or_None, endpoint, model)`` or ``None``.

    Zen is preferred: it serves the contract on a free, unauthenticated tier, so a stock install
    needs no signup. Its key (if any) is never returned, because the door rejects a bearer.
    ``preferred`` (``plugins.entries.jev.settings.provider``) pins one door; a pinned
    ``typesafe`` without its key yields ``None`` rather than silently using a provider the
    operator did not pick.
    """
    want = (preferred or "").strip().lower()
    for provider, env_var, endpoint, model, send_key in CANDIDATES:
        if want and want != provider:
            continue
        if not send_key:
            return provider, None, endpoint, model
        key = _secret(env_var)
        if key:
            return provider, key, endpoint, model
    return None


def unavailable_reason(preferred: Optional[str] = None) -> str:
    """A one-line, credential-free explanation of why decisions are unavailable."""
    if resolve_provider(preferred) is not None:
        return ""
    want = (preferred or "").strip().lower()
    if want:
        names = [c[1] for c in CANDIDATES if c[0] == want]
        if not names:
            return f"unknown decision provider {preferred!r}; expected one of {', '.join(c[0] for c in CANDIDATES)}"
        return f"no {names[0]} configured"
    return "no decision provider reachable"


# ── question builders and validation ─────────────────────────────────────────

def noul(instructions: str, criteria: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """A yes/no question, answered as a probability of yes."""
    question: Dict[str, Any] = {"type": "noul", "instructions": instructions}
    if criteria is not None:
        question["criteria"] = dict(criteria)
    return question


def choice(instructions: str, options: Mapping[str, str]) -> Dict[str, Any]:
    """Pick one of a closed set, answered as a distribution over the options."""
    return {"type": "choice", "instructions": instructions, "criteria": dict(options)}


def score(instructions: str, levels: Sequence[str]) -> Dict[str, Any]:
    """A position on an ordered rubric (lowest first), answered as an expected level."""
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


def _structured(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, (Mapping, list, tuple)) and bool(value)


def check_question(name: str, question: Any) -> Dict[str, Any]:
    """One question in the shape the API answers, or ``ValueError`` saying what is wrong.

    Refused before any request is made, so a caller bug cannot reach the wire dressed as "the
    model had no opinion".
    """
    if not isinstance(question, Mapping):
        raise ValueError(f'question {name!r} must be an object like {{"type":…, "instructions":…}}')
    kind = question.get("type")
    if kind not in QUESTION_TYPES:
        raise ValueError(f"question {name!r} has type {kind!r}; use one of {', '.join(QUESTION_TYPES)}")
    text = question.get("instructions")
    if not _structured(text):
        raise ValueError(f"question {name!r} has no instructions")
    criteria = question.get("criteria")
    if kind == "noul":
        if criteria is None:
            return {"type": "noul", "instructions": text}
        if (not isinstance(criteria, Mapping) or not criteria
                or not set(map(str, criteria)) <= set(NOUL_CRITERIA_KEYS)):
            raise ValueError(f'question {name!r} is a noul: criteria must be {{"true":…, "false":…}}')
        return {"type": "noul", "instructions": text, "criteria": dict(criteria)}
    wanted = Mapping if kind == "choice" else list
    shape = 'an object of 2 to 255 options, {"option": "what it means"}' if kind == "choice" else "a list of 2 to 10 levels, lowest first"
    if not isinstance(criteria, wanted) or len(criteria) < MIN_CRITERIA:
        raise ValueError(f"question {name!r}: criteria for a {kind} must be {shape}")
    if kind == "choice" and len(criteria) > MAX_CHOICE_OPTIONS:
        raise ValueError(f"question {name!r}: {len(criteria)} options; a choice takes at most {MAX_CHOICE_OPTIONS}")
    if kind == "score":
        if len(criteria) > MAX_SCORE_LEVELS:
            raise ValueError(f"question {name!r}: {len(criteria)} levels; a score takes at most {MAX_SCORE_LEVELS}")
        if not all(_structured(level) for level in criteria):
            raise ValueError(f"question {name!r}: every score level needs text")
    return {"type": kind, "instructions": text, "criteria": list(criteria) if kind == "score" else dict(criteria)}


def check_questions(questions: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    if not isinstance(questions, Mapping) or not questions:
        raise ValueError("questions must be a non-empty mapping of {name: question}")
    return {str(name): check_question(str(name), question) for name, question in questions.items()}


# ── reply validation ─────────────────────────────────────────────────────────

def _unit(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JevUnavailable("malformed", f"{name} is not numeric")
    number = float(value)
    if not math.isfinite(number) or not -1e-6 <= number <= 1 + 1e-6:
        raise JevUnavailable("malformed", f"{name} is outside 0..1")
    return min(1.0, max(0.0, number))


def _distribution(name: str, raw: Any, keys: Sequence[str]) -> Dict[str, float]:
    if not isinstance(raw, dict):
        raise JevUnavailable("invalid_response", f"{name}: probabilities are not an object")
    expected, found = set(keys), set(raw)
    if found != expected:
        missing, extra = sorted(expected - found), sorted(found - expected)
        raise JevUnavailable("invalid_response", f"{name}: missing {missing or 'none'}, unexpected {extra or 'none'}")
    values = {key: _unit(raw[key], f"{name}.p[{key}]") for key in keys}
    total = sum(values.values())
    if abs(total - 1.0) > PROBABILITY_SUM_TOLERANCE:
        raise JevUnavailable("invalid_response", f"{name}: mass sums to {total!r}")
    return values


def score_mean_tolerance(levels: int) -> float:
    return max(SCORE_MEAN_TOLERANCE, ROUNDING_STEP * (sum(range(max(int(levels), 1))) + 1) + 1e-12)


def check_answer(name: str, question: Mapping[str, Any], answer: Any) -> Dict[str, Any]:
    """One validated answer, or ``JevUnavailable``. See the invariants at the module head."""
    if not isinstance(answer, dict) or answer.get("type") != question["type"]:
        raise JevUnavailable("malformed", f"answer {name} has the wrong type")
    kind = question["type"]
    if kind == "noul":
        return {"type": "noul", "noul": _unit(answer.get("noul"), f"{name}.noul")}
    if kind == "choice":
        options = list(question["criteria"])
        picked = answer.get("choice")
        if not isinstance(picked, str) or picked not in set(options):
            raise JevUnavailable("malformed", f"answer {name} chose an option that was not offered")
        probabilities = _distribution(name, answer.get("probabilities"), options)
        if probabilities[picked] < max(probabilities.values()) - ARGMAX_TOLERANCE:
            raise JevUnavailable("invalid_response", f"answer {name} is not the maximum of its own distribution")
        return {"type": "choice", "choice": picked, "probabilities": probabilities,
                "confidence": _unit(answer.get("confidence"), f"{name}.confidence")}
    levels = len(question["criteria"])
    value = answer.get("score")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise JevUnavailable("malformed", f"answer {name} has no numeric score")
    if not -0.5 <= float(value) <= levels - 0.5:
        raise JevUnavailable("malformed", f"answer {name} scored off the rubric")
    spread: Dict[int, float] = {}
    raw = answer.get("probabilities")
    if isinstance(raw, dict) and raw:
        keys = []
        for key in raw:
            if not str(key).isdigit() or int(key) >= levels:
                raise JevUnavailable("invalid_response", f"{name}: level {key!r} is not 0..{levels - 1}")
            keys.append(str(key))
        spread = {int(k): v for k, v in _distribution(name, raw, sorted(keys, key=int)).items()}
        mean = sum(level * probability for level, probability in spread.items())
        if abs(mean - float(value)) > score_mean_tolerance(levels):
            raise JevUnavailable("invalid_response", f"answer {name} disagrees with its own distribution")
    return {"type": "score", "score": float(value), "probabilities": spread,
            "spread_reported": bool(spread),
            "confidence": _unit(answer.get("confidence", 1.0), f"{name}.confidence")}


# ── transport ────────────────────────────────────────────────────────────────

def _post(endpoint: str, body: bytes, headers: Dict[str, str], timeout: float) -> bytes:
    """One POST. Redirects are never followed: a 3xx would carry the bearer to another host."""
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - https literals only
            return response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        code = {401: "auth_failed", 402: "credits_exhausted", 403: "auth_failed", 429: "rate_limited",
                529: "overloaded"}.get(error.code, f"http_{error.code}")
        raise JevUnavailable(code, f"HTTP {error.code}") from None
    except urllib.error.URLError as error:
        raise JevUnavailable("network", type(getattr(error, "reason", error)).__name__) from None
    except TimeoutError:
        raise JevUnavailable("timeout") from None


def ask(
    state: Any,
    questions: Mapping[str, Mapping[str, Any]],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    model: Optional[str] = None,
    preferred_provider: Optional[str] = None,
    transport: Optional[Any] = None,
) -> Dict[str, Any]:
    """Ask every question against one state, in a single request.

    Returns ``{"answers": {...validated...}, "provider": str, "jev_model": str|None,
    "latency_ms": int, "input_tokens": int|None}``. Raises :class:`JevUnavailable` for
    anything the caller must not act on, including a missing key. ``transport`` exists so
    tests can exercise the parser without a network.
    """
    checked = check_questions(questions)
    encoded = state if isinstance(state, str) else json.dumps(state, separators=(",", ":"), default=str)
    if len(encoded) > MAX_STATE_CHARS:
        raise JevUnavailable("state_too_large", f"{len(encoded)} chars > {MAX_STATE_CHARS}")
    resolved = resolve_provider(preferred_provider)
    if resolved is None:
        raise JevUnavailable("no_key", unavailable_reason(preferred_provider))
    provider, key, endpoint, default_model = resolved
    request = {"state": state, "model": model or default_model, "questions": checked}
    body = json.dumps(request, separators=(",", ":"), default=str).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    started = time.monotonic()
    raw = (transport or _post)(endpoint, body, headers, timeout)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise JevUnavailable("response_too_large")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise JevUnavailable("malformed", "reply is not JSON") from None
    answers = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers, dict):
        raise JevUnavailable("malformed", "reply has no answers")
    validated = {name: check_answer(name, question, answers.get(name)) for name, question in checked.items()}
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    tokens = usage.get("input_tokens")
    seen = payload.get("model")
    return {"answers": validated, "provider": provider,
            "jev_model": seen if isinstance(seen, str) and seen else None,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "input_tokens": int(tokens) if isinstance(tokens, (int, float)) and not isinstance(tokens, bool)
            and math.isfinite(float(tokens)) and tokens >= 0 else None}


def batches(items: Sequence[Any], size: int) -> List[Sequence[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]
