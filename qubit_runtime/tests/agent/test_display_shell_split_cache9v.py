"""The replayed shell preview is memoised, not re-parsed: identical output, no shared-list aliasing."""

import pytest

from agent import display


COMMANDS = [
    "cd /tmp && pytest -q tests 2>&1 | tail -5",
    "git status --short",
    "ls -la | head",
    "echo done",
    'python - <<"PY"\nprint("a && b")\nPY',
    "VAR=1 make build && VAR=2 make test; echo ok",
    "cat ~/.qubit/config.yaml > /tmp/x 2>&1",
    "",
]


@pytest.mark.parametrize("command", COMMANDS)
def test_cached_split_matches_the_uncached_word_split(command):
    assert display._split_shell_words(command) == display._split_shell_words_uncached(command)


@pytest.mark.parametrize("command", COMMANDS)
def test_cached_compound_split_matches_the_uncached_split(command):
    assert list(display._split_shell_compound(command)) == list(display._split_shell_compound_uncached(command))


def test_cached_word_list_is_not_shared_between_callers():
    # ``_shell_head_word`` pops leading VAR= assignments from the list it gets; a cached
    # list object handed out twice would leak that mutation into later previews.
    first = display._split_shell_words("A=1 B=2 run now")
    before = list(first)
    display._shell_head_word("A=1 B=2 run now")
    assert list(first) == before
    assert display._split_shell_words("A=1 B=2 run now") == before


def test_long_shell_input_skips_the_cache_but_stays_correct():
    huge = "echo " + ("x" * (display._SHELL_SPLIT_CACHE_MAX_CHARS + 16))
    assert display._split_shell_words(huge) == display._split_shell_words_uncached(huge)
    assert display.summarize_shell_command("cd /tmp && echo hi | tail -1") == "echo hi"
