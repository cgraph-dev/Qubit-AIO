"""Global wake-word store: normalization, caps, atomic persistence (TDD)."""
from __future__ import annotations

import json


def test_normalize_drops_punctuation_case_whitespace():
    from voicebridge.voice_edit import wake_words as W
    assert W.normalize_trigger("  Hey,   Qubit!! ") == "hey qubit"
    assert W.normalize_trigger("HEY\tQUBIT") == "hey qubit"
    assert W.normalize_trigger("“Hey” Qubit") == "hey qubit"
    assert W.normalize_trigger("!!! ???") == ""
    assert W.normalize_trigger("") == ""
    assert W.normalize_trigger(None) == ""
    # inner punctuation survives (only token edges are stripped)
    assert W.normalize_trigger("don't stop") == "don't stop"
    assert W.normalize_trigger("hey.qubit") == "hey.qubit"


def test_defaults_derived_from_wake_phrase():
    from voicebridge.voice_edit import wake, wake_words as W
    assert W.DEFAULT_TRIGGERS == (wake.DEFAULT_PHRASE,)
    assert list(W.DEFAULT_TRIGGERS) == ["hey qubit"]


def test_load_missing_corrupt_empty_is_defaults(tmp_path):
    from voicebridge.voice_edit import wake_words as W
    assert W.load_triggers(tmp_path / "nope.json") == list(W.DEFAULT_TRIGGERS)

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert W.load_triggers(bad) == list(W.DEFAULT_TRIGGERS)

    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert W.load_triggers(wrong) == list(W.DEFAULT_TRIGGERS)

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"triggers": []}), encoding="utf-8")
    assert W.load_triggers(empty) == list(W.DEFAULT_TRIGGERS)

    junk = tmp_path / "junk.json"
    junk.write_text(json.dumps({"triggers": ["", "  ", "!!!"]}),
                    encoding="utf-8")
    assert W.load_triggers(junk) == list(W.DEFAULT_TRIGGERS)


def test_save_dedupes_caps_and_roundtrips(tmp_path):
    from voicebridge.voice_edit import wake_words as W
    p = tmp_path / "wake_words.json"

    saved = W.save_triggers(
        [" Hey,  Qubit!! ", "hey qubit", "Computer", "computer"], p)
    assert saved == ["hey qubit", "computer"]
    assert W.load_triggers(p) == saved

    # count cap
    many = [f"word {i}" for i in range(W.MAX_WORDS + 8)]
    capped = W.save_triggers(many, p)
    assert len(capped) == W.MAX_WORDS
    assert capped[0] == "word 0"

    # length cap: over-length dropped, exactly-at-cap kept
    long_ok = "x" * W.MAX_LENGTH
    long_bad = "x" * (W.MAX_LENGTH + 1)
    got = W.save_triggers([long_ok, long_bad], p)
    assert got == [long_ok]


def test_save_is_atomic_and_replaces_corrupt(tmp_path):
    from voicebridge.voice_edit import wake_words as W
    p = tmp_path / "wake_words.json"

    # a corrupt file is fully replaced, never merged
    p.write_text("{broken", encoding="utf-8")
    saved = W.save_triggers(["Hey Qubit"], p)
    assert saved == ["hey qubit"]

    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["triggers"] == ["hey qubit"]
    assert data["updatedAtMs"] > 0

    # no partial temp file left behind
    leftovers = [q.name for q in tmp_path.iterdir() if q.name.endswith(".tmp")]
    assert leftovers == []


def test_empty_save_reads_back_as_defaults(tmp_path):
    from voicebridge.voice_edit import wake_words as W
    p = tmp_path / "wake_words.json"
    assert W.save_triggers([], p) == []
    # the persisted file is genuinely empty ...
    assert json.loads(p.read_text(encoding="utf-8"))["triggers"] == []
    # ... but the effective list falls back to the defaults
    assert W.load_triggers(p) == list(W.DEFAULT_TRIGGERS)
    assert W.active_triggers(p) == list(W.DEFAULT_TRIGGERS)


def test_add_remove_helpers_are_pure():
    from voicebridge.voice_edit import wake_words as W
    base = ["hey qubit"]

    assert W.add_trigger(base, "  Computer! ") == ["hey qubit", "computer"]
    assert W.add_trigger(base, "Hey Qubit") == base          # dup (normalized)
    assert W.add_trigger(base, "  !!!  ") == base            # empty
    assert W.add_trigger(base, "x" * (W.MAX_LENGTH + 1)) == base  # too long
    full = [f"word {i}" for i in range(W.MAX_WORDS)]
    assert W.add_trigger(full, "extra") == full              # at count cap

    assert W.remove_trigger(["hey qubit", "computer"], "Computer!") == \
        ["hey qubit"]
    assert W.remove_trigger(base, "absent") == base
    assert W.remove_trigger(base, None) == base
    # the input list is never mutated
    assert base == ["hey qubit"]


def test_remove_last_falls_back_to_defaults_on_load(tmp_path):
    from voicebridge.voice_edit import wake_words as W
    p = tmp_path / "wake_words.json"
    W.save_triggers(["hey qubit", "computer"], p)

    remaining = W.remove_trigger(W.load_triggers(p), "computer")
    assert remaining == ["hey qubit"]
    W.save_triggers(remaining, p)
    assert W.load_triggers(p) == ["hey qubit"]

    W.save_triggers(W.remove_trigger(remaining, "hey qubit"), p)
    assert W.load_triggers(p) == list(W.DEFAULT_TRIGGERS)


def test_never_raises_on_junk(tmp_path):
    from voicebridge.voice_edit import wake_words as W
    p = tmp_path / "wake_words.json"

    # junk inputs never raise, and always return a list
    for junk in (None, "nope", 42, {"a": 1}, [None, {}, [], 42],
                 [[["deep"]]], object()):
        out = W.save_triggers(junk, p)
        assert isinstance(out, list)
        added = W.add_trigger(junk, junk)
        assert isinstance(added, list)
        removed = W.remove_trigger(junk, junk)
        assert isinstance(removed, list)
        assert isinstance(W.active_triggers(p), list)

    # a directory where a file is expected degrades to defaults
    assert W.load_triggers(tmp_path) == list(W.DEFAULT_TRIGGERS)

    # normalize always yields a string
    assert isinstance(W.normalize_trigger(object()), str)
    assert W.normalize_trigger(42) == "42"
