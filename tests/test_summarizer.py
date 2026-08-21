

def test_clamp_words_under_cap_untouched():
    from voicebridge.summarizer import clamp_words
    short = "Short reply."
    assert clamp_words(short, 40) == short


def test_clamp_words_over_cap_truncates_cleanly():
    from voicebridge.summarizer import clamp_words
    long = " ".join(f"word{i}" for i in range(60))
    out = clamp_words(long, 40)
    assert len(out.split()) == 40
    assert out.endswith(".")


def test_trim_spoken_word_cap():
    from voicebridge.summarizer import trim_spoken
    long = " ".join(f"word{i}" for i in range(50)) + "."
    out = trim_spoken(long, max_chars=700, max_words=40)
    assert len(out.split()) == 40


def test_summarize_clamps_to_max_words():
    from voicebridge.summarizer import summarize
    long = "Sentence one here. " + " ".join(f"word{i}" for i in range(50)) + "."
    out = summarize(long, {"provider": "extractive", "max_chars": 900, "max_words": 40})
    assert len(out.split()) <= 40
