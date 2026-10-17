Correction to 4629727d6 — the quoted count was not measured.

That commit message says:

    "tmp/audit/find_brand_leaks.py classifies every remaining occurrence and
     reports 1,069 user-visible strings across 479 production files"

Neither figure was produced by the probe. The probe was run at 1,059 / 473 before the
commit was written, and the "1,069 / 479" pair was carried in from an earlier session's
note and asserted, not re-measured. A verifier reading the commit against the committed
script could not reproduce it, which is how this was caught.

The script DOES emit both numbers (find_brand_leaks.py:77-78), so the claim was
checkable and I did not check it. That is the whole of the error: a measured-sounding
number that was not measured.

Independently re-measured now, at three points across the wave:

    before the wave   1,059 strings / 473 files
    after the wave      970 strings / 450 files
    delta               -89 strings / -23 files

The direction and the per-wave delta are sound. The absolute baseline I originally
published was wrong by 10 strings / 6 files, and every later reference in this session
repeated it.

The lesson: a number in a commit message is a claim like any other and has to come from
a command whose output you read. Restating a figure from memory, in a message whose
whole purpose is honest measurement, is the exact failure mode this work is meant to
avoid.

No source change accompanies this correction — the code is correct, the description
of it was not.
