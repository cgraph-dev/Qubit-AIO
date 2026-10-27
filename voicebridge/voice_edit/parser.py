"""Deterministic spoken-command parser for the voice typing / edit surface.

The parser is intentionally conservative: it only maps an utterance to a
transformation when it recognises a supported action or style. Anything it does
not recognise becomes a *clarification request* -- it is never silently treated
as a rewrite. When there is no selection at all, the caller keeps the utterance
as ordinary dictation and never calls the parser for a transform.
"""
from __future__ import annotations

import re
from typing import List, Optional

from .schema import ACTIONS, AUDIENCES, STYLES, EditCommand

# Leading wake words / politeness that are not part of the instruction.
_PREFIX_RE = re.compile(
    r"^\s*(?:hey\s+|ok(?:ay)?\s+|q(?:ubit)?[,\s]+|please\s+|"
    r"can you\s+|could you\s+|i(?:'d| would) like you to\s+)+",
    re.IGNORECASE)

_LANG_RE = re.compile(
    r"\b(?:translate|convert|render)\b[^.]*?\b(?:in|into|to)\s+"
    r"([a-z][a-z\- ]{1,24}?)(?:\s*[.,!?]|$|\s+please\b)", re.IGNORECASE)
_LEN_RE = re.compile(
    r"\b(?:under|less than|at most|max(?:imum)?(?: of)?|no more than)\s+"
    r"(\d{1,4})\s*(characters|chars|letters|words)\b", re.IGNORECASE)

# Action phrases are matched longest/most-specific first.
_ACTION_RULES = [
    ("improve_prompt", ("improve the prompt", "improve this prompt",
                        "rewrite this prompt", "make this a better prompt")),
    ("clarify", ("so an llm understands it", "so a language model understands",
                 "clear for llm", "for an llm", "make this clearer",
                 "make it clearer", "clarify this", "clarify", "make clear",
                 "more clear", "clearer")),
    ("professionalize", ("professionally", "professional", "more formal",
                         "business tone", "make it professional")),
    ("humanize", ("sound more human", "sound human", "more human",
                  "humanize", "natural sounding", "sound natural")),
    ("make_funny", ("make this funnier", "make it funnier", "make it funny",
                    "make this funny", "funnier", "funny", "humorous",
                    "add humor", "add humour")),
    ("shorten", ("without changing the requirements", "remove unnecessary words",
                   "remove unnecessary", "cut the fluff", "make this shorter",
                   "make it shorter", "shorten", "shorter", "be brief",
                   "make it brief", "make this brief",
                   "be concise", "concise", "trim this", "less wordy",
                   "reduce this text", "reduce the text", "reduce this",
                   "reduce it")),
    ("expand", ("expand this", "expand it", "expand with", "add more detail",
                 "more detail", "elaborate", "expand", "longer version",
                 "make it longer", "make this longer", "lengthen")),
    ("summarize", ("summarize", "summarise", "sum up", "sum this up",
                    "sum it up", "summary",
                    "tl;dr", "tldr")),
    ("correct_grammar", ("fix the grammar", "fix grammar", "correct grammar",
                         "grammar", "fix the spelling", "spelling",
                         "proofread", "fix typos", "typos", "punctuation")),
    ("bullet_points", ("bullet points", "bulleted list", "as bullets",
                        "into bullets", "make a list", "bullet this",
                        "bullets")),
    ("email", ("suitable for an email", "as an email", "into an email",
                "write an email", "make it an email", "make this an email",
                "email-ready", "for an email")),
    ("technical", ("for a technical audience", "technical audience",
                   "technical reader", "for engineers", "technical",
                   "technically accurate")),
    ("casual", ("sound casual", "more casual", "casual", "relaxed",
                "informal", "chill")),
    ("translate", ("translate", "translation", "convert this",
                    "convert it", "convert to")),
    ("rewrite", ("redo this text for me", "redo this text", "redo this",
                 "redo it", "rewrite this", "rewrite it", "rewrite", "reword",
                 "rephrase", "paraphrase")),
]

# Loose redo trigger (Lane 3, 2026-10-03): ASR mishears the middle noun
# ("redo THESE PACKS for me" for "redo THIS TEXT for me"), so any
# "redo <anything> for me" routes to rewrite. The "for me" anchor is
# required — bare "redo the thing" must not steal unrelated turns.
_REDO_FOR_ME_RE = re.compile(
    r"\bredo\b.+?\bfor\s+me\b\s*[,:\-–—]*\s*(.*)$",
    re.IGNORECASE | re.DOTALL)

# Deterministic case commands. Unlike the substring action rules below these
# are anchored to the whole utterance: a selection plus ordinary dictation
# ("I keep the lowercase letters in the password") must not be mistaken for a
# "make it lowercase" command.
_CASE_VERB = (r"(?:make|change|convert|turn|switch|set|put|write|type|"
              r"render|format|transform)")
_CASE_OBJECT = (
    r"(?:it|this|that|these|those|everything|all\s+of\s+it|"
    r"(?:this|that)\s+text|"
    r"the\s+(?:selected\s+|whole\s+|entire\s+)?(?:text|selection|sentence|"
    r"words?|letters?|thing)|all\s+(?:the\s+)?(?:text|words?|letters?))")
_CASE_UPPER = (r"(?:uppercase|upper[\s-]?case|all[\s-]?caps|"
               r"all\s+capital\s+letters|capital\s+letters|caps)")
_CASE_LOWER = (r"(?:lowercase|lower[\s-]?case|all[\s-]?lowercase|"
               r"all\s+small\s+letters|small\s+letters)")
_CASE_TITLE = (r"(?:title[\s-]?case|titlecase|proper[\s-]?case)")
_CASE_TAIL = r"\s*(?:please|now|for\s+me)?\s*[.!?]*\s*$"


def _case_re(word: str) -> "re.Pattern[str]":
    verb_led = (r"^(?:please\s+|just\s+)?" + _CASE_VERB +
                r"\s+(?:" + _CASE_OBJECT + r"\s+)?"
                r"(?:to|into|in)?\s*" + word + _CASE_TAIL)
    word_led = (r"^(?:please\s+|just\s+)?" + word + r"\s*"
                r"(?:" + _CASE_OBJECT + r"\s*)?" + _CASE_TAIL)
    return re.compile(r"(?:" + verb_led + r")|(?:" + word_led + r")",
                      re.IGNORECASE)


_CASE_ACTION_RES = (
    ("uppercase", _case_re(_CASE_UPPER)),
    ("lowercase", _case_re(_CASE_LOWER)),
    ("title_case", _case_re(_CASE_TITLE)),
    # "capitalize the words" / "capitalize each word" / "capitalize it" is
    # title case (the owner's phrasing), never a model round-trip.
    ("title_case", re.compile(
        r"^(?:please\s+|just\s+)?capitali[sz]e\s+"
        r"(?:the\s+|each\s+|every\s+|all\s+(?:the\s+)?)*"
        r"(?:words?|letters?|text|selection|sentence|title|it|this|that)"
        r"\b" + _CASE_TAIL, re.IGNORECASE)),
    ("title_case", re.compile(
        r"^(?:please\s+|just\s+)?capitali[sz]e\s+the\s+first\s+letter\s+"
        r"of\s+(?:each|every)\s+word\b" + _CASE_TAIL, re.IGNORECASE)),
)


def _match_case_action(low: str) -> Optional[str]:
    for action, rx in _CASE_ACTION_RES:
        if rx.match(low):
            return action
    return None


# Style words that can appear without an explicit action ("make it persuasive").
_STYLE_RULES = [
    ("persuasive", ("persuasive", "convincing", "compelling")),
    ("friendly", ("friendly", "warm")),
    ("formal", ("formal",)),
    ("direct", ("direct", "straightforward", "get to the point")),
    ("creative", ("creative", "imaginative")),
    ("clear", ("clear",)),
    ("concise", ("concise", "brief", "short")),
    ("human", ("human",)),
    ("professional", ("professional",)),
    ("funny", ("funny", "humorous")),
    ("casual", ("casual",)),
    ("technical", ("technical",)),
]

_AUDIENCE_RULES = [
    ("llm", ("for an llm", "for a language model", "for an ai")),
    ("technical", ("technical audience", "technical reader", "engineers")),
    ("executive", ("executive", "for leadership", "for management")),
    ("customer", ("customer", "client")),
    ("general", ("general audience", "everyone")),
    ("email", ("for an email", "email")),
]

_CONSTRAINT_RULES = [
    ("do not change the meaning", ("don't change the meaning",
                                   "do not change the meaning",
                                   "keep the meaning", "preserve the meaning")),
    ("keep requirements", ("without changing the requirements",
                           "don't change the requirements",
                           "preserve the requirements", "keep the requirements")),
    ("preserve facts", ("preserve the facts", "don't change facts",
                        "do not change facts", "keep the facts")),
    ("preserve format", ("preserve the format", "keep the formatting",
                         "keep the format")),
    ("keep it short", ("keep it short", "keep it brief")),
]


def _clean(text: str) -> str:
    t = (text or "").strip()
    t = _PREFIX_RE.sub("", t, count=1).strip()
    return t


# Explicit verbatim-dictation triggers: everything AFTER the phrase is typed
# as-is (no model, no rewriting). Longest phrases are matched first so
# "type the following" wins over "type".
_DICTATE_TRIGGERS = (
    "type this for me", "write this for me", "type the following",
    "write the following", "dictate the following", "type this out",
    "type this", "write this", "dictate this", "insert this", "enter this",
    "type for me", "write for me", "type out", "dictate",
)


_DICTATE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in
                        sorted(_DICTATE_TRIGGERS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE)


def has_dictation_cue(transcript: str) -> bool:
    """True when the utterance is an explicit dictation command.

    The helper uses this to decide whether spontaneous speech should be typed
    at the cursor: only an explicit "type this ..." command types.
    """
    return _DICTATE_RE.search(_clean(transcript)) is not None


# A dictation verb immediately followed by a colon, or by filler words. The
# filler/colon requirement keeps genuine dictation like "Write a function ..."
# intact (it is not a cue) while still stripping "type this for me: ...".
_CUE_COLON_RE = re.compile(
    r"^\s*(?:type|write|dictate|insert|enter|note)\s*[:\-]\s*(.*)$",
    re.IGNORECASE | re.DOTALL)
_CUE_FILLER_RE = re.compile(
    r"^\s*(?:type|write|dictate|insert|enter|note)\b[\s,:;]*"
    r"(?:(?:this|that|out|it|all|down|the following|following|for me|"
    r"to me|please|now|exactly|verbatim|for us)[\s,:;]+)+(.*)$",
    re.IGNORECASE | re.DOTALL)


def strip_dictation_cue(transcript: str) -> Optional[str]:
    """Loose fallback for a dictation cue ("type this for me: X").

    Used only when the exact phrase match fails (ASR variance). Returns None
    when there is no cue, so ordinary dictation is never truncated.
    """
    t = (transcript or "").strip()
    if not t:
        return None
    m = _CUE_COLON_RE.match(t)
    if m:
        return m.group(1).strip()
    m = _CUE_FILLER_RE.match(t)
    if m:
        return m.group(1).lstrip(" \t,.:;!?-–—").strip()
    return None


def extract_dictation(transcript: str) -> Optional[str]:
    """Return the verbatim payload after a dictation trigger, or None.

    ``"Qubit, type this for me: hello world"`` -> ``"hello world"``.
    Returns ``""`` when the trigger is present but no payload was spoken (the
    caller must ask for the content, never insert an empty string).

    Matching is word-bounded, so ``"rewrite this"`` never matches the trigger
    ``"write this"`` and ``"dictated"`` never matches ``"dictate"``.
    """
    cleaned = _clean(transcript)
    m = _DICTATE_RE.search(cleaned)
    if m is None:
        return None
    payload = cleaned[m.end():]
    # Strip the separators and any filler the speaker put after the command
    # (e.g. "type this, for me, hello" -> "hello").
    payload = re.sub(r"^[\s:,\-–—]+", "", payload)
    payload = re.sub(r"^(?:for me|to me|please|now|the following)\b[\s,:;\-–—]*",
                     "", payload, flags=re.IGNORECASE)
    return payload.strip()


def _contains_any(low: str, phrases) -> Optional[str]:
    for p in phrases:
        if p in low:
            return p
    return None


def parse(transcript: str, *, selected_text: str = "",
          request_id: str = "", session_id: str = "",
          target_application: str = "") -> EditCommand:
    """Parse one utterance into an :class:`EditCommand`.

    ``selected_text`` is copied into the command for the transform step but is
    never used to decide the action -- a spoken command must be explicit.
    """
    raw = (transcript or "").strip()
    low = _clean(raw).lower()
    cmd = EditCommand(
        selected_text=selected_text or "", request_id=request_id,
        session_id=session_id, target_application=target_application,
        transcript=raw,
    )
    if not low:
        cmd.needs_clarification = True
        cmd.clarification = ("I did not catch an editing instruction. Say, for "
                            "example, 'make this clearer' or 'fix the grammar'.")
        return cmd

    # Verbatim dictation wins over every transformation: "type this for me: X".
    dictated = extract_dictation(raw)
    if dictated is not None:
        cmd.action = "dictate_text"
        cmd.dictated_text = dictated
        if not dictated:
            cmd.needs_clarification = True
            cmd.clarification = ("What should I type? Say 'Qubit, type this "
                                 "for me: your text'.")
        return cmd

    action_hit: Optional[str] = None
    # Case commands are checked first and whole-utterance anchored so they
    # work without a model (and never steal ordinary sentences).
    case_hit = _match_case_action(low)
    if case_hit is not None:
        action_hit = case_hit
    else:
        for action, phrases in _ACTION_RULES:
            if _contains_any(low, phrases):
                action_hit = action
                break

    # Loose redo fallback: "redo <anything> for me" (any middle, including
    # ASR-misheard nouns). Content after "for me" becomes the selection when
    # no selection is present; a present selection always wins.
    _redo_suffix = ""
    try:
        _redo_m = _REDO_FOR_ME_RE.search(_clean(raw))
        if _redo_m is not None:
            if action_hit is None:
                action_hit = "rewrite"
            _redo_suffix = (_redo_m.group(1) or "").strip().lstrip(
                " \t,.:;!?-\u2013\u2014").strip()
    except Exception:
        _redo_suffix = ""

    style_hit = ""
    for style, phrases in _STYLE_RULES:
        if _contains_any(low, phrases):
            style_hit = style
            break

    # A style-only utterance ("make it persuasive") is a rewrite with style.
    if action_hit is None and style_hit:
        action_hit = "rewrite"

    # An explicit custom instruction is honoured as custom_transform.
    custom = ""
    m = re.match(r"^(?:custom(?: transform)?|transform)\s*[:\-]?\s*(.+)$",
                 low)
    if m and action_hit is None:
        action_hit = "custom_transform"
        custom = m.group(1).strip()

    if action_hit is None or action_hit not in ACTIONS:
        cmd.needs_clarification = True
        cmd.clarification = (
            "I heard an editing request but could not tell which change you "
            "want. Try 'make it shorter', 'fix the grammar', 'summarize', "
             "'translate to English', or 'redo this text for me professionally'.")
        return cmd

    cmd.action = action_hit
    cmd.style = style_hit
    cmd.custom_instruction = custom
    if action_hit == "rewrite" and _redo_suffix and not (selected_text or "").strip():
        cmd.selected_text = _redo_suffix

    # Explicit style words refine a coarse action when unambiguous.
    if action_hit == "rewrite" and not style_hit:
        for style, phrases in _STYLE_RULES:
            if _contains_any(low, phrases):
                cmd.style = style
                break

    for audience, phrases in _AUDIENCE_RULES:
        if _contains_any(low, phrases):
            cmd.audience = audience
            break

    if action_hit == "translate":
        m = _LANG_RE.search(low)
        if m:
            cmd.language = m.group(1).strip().title()
        else:
            cmd.language = "English"

    cmd.constraints = [name for name, phrases in _CONSTRAINT_RULES
                       if _contains_any(low, phrases)]

    m = _LEN_RE.search(low)
    if m:
        amount = int(m.group(1))
        if m.group(2).lower().startswith("word"):
            amount = amount * 6  # approximate words -> chars
        cmd.max_length = amount

    if "without changing the requirements" in low:
        cmd.preserve_facts = True
        cmd.preserve_format = True
        if "keep requirements" not in cmd.constraints:
            cmd.constraints.append("keep requirements")

    return cmd


__all__ = ["parse", "extract_dictation", "strip_dictation_cue",
           "has_dictation_cue", "ACTIONS", "STYLES", "AUDIENCES"]
