"""Qubit-native text/store hardening (no upstream code in the product).

Adversarial fixtures beyond the differential battery: escape smuggling,
control spoofing, tag smuggling, CR-overwrite hiding, oversized inputs,
concurrent schema races, and identifier injection. All hermetic.
"""
from __future__ import annotations

import sqlite3
import threading

import pytest


def test_escape_smuggling_blocked():
    from voicebridge import textutil as T

    hostile = ("\x1b[2J\x1b[H"          # clear + home
               "\x1b]0;pwned\x07"       # retitle
               "\x1b[?25l"              # hide cursor
               "\x1b[31mred\x1b[0m"
               "\x9b4mC1\x9b0m")
    out = T.strip_ansi(hostile)
    assert "\x1b" not in out and "\x9b" not in out
    assert "red" in out


def test_cr_overwrite_spoofing_neutralized():
    from voicebridge import textutil as T

    out = T.sanitize_display_text("legit text\rHIDDEN")
    assert "\r" not in out
    assert "legit text" in out and "HIDDEN" in out


def test_tag_smuggling_blocked_flag_kept():
    from voicebridge import textutil as T

    hidden = "run \U000E0069\U000E0067\U000E006E\U000E006F\U000E0072\U000E0065 me"
    out = T.strip_unicode_tags(hidden)
    assert "\U000E0069" not in out
    flag = "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F!"
    assert T.strip_unicode_tags(flag) == flag


def test_sanitize_never_crashes_and_bounds_output():
    from voicebridge import textutil as T

    for text in ("", "\x00" * 5000, "\x1b[" * 2000,
                 "emoji \U0001F600 ok", "tab\tnewline\nok"):
        out = T.sanitize(text)
        assert isinstance(out, str)
        assert "\x1b" not in out and "\x00" not in out
    assert T.sanitize("tab\tnewline\nok") == "tab\tnewline\nok"


def test_write_txn_rollback_and_race(tmp_path):
    from voicebridge import sqlstore as S

    db = tmp_path / "race.db"
    conn = sqlite3.connect(str(db), check_same_thread=False)
    conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    with S.write_txn(conn):
        conn.execute("INSERT INTO t(v) VALUES ('a')")
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    with pytest.raises(RuntimeError):
        with S.write_txn(conn):
            conn.execute("INSERT INTO t(v) VALUES ('b')")
            raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1

    errors = []

    def _add():
        try:
            c = sqlite3.connect(str(db), timeout=30.0)
            assert S.add_column_if_missing(c, "t", "extra", "extra TEXT") in (True, False)
            c.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_add) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors
    conn.close()


def test_identifier_injection_rejected(tmp_path):
    from voicebridge import sqlstore as S

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    with pytest.raises(ValueError):
        S.add_column_if_missing(conn, "t; DROP TABLE t; --", "x", "x TEXT")
    assert S.add_column_if_missing(conn, "t", "x", "x TEXT") is True
    assert S.add_column_if_missing(conn, "t", "x", "x TEXT") is False
    conn.close()
