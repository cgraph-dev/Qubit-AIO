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

Independently re-measured now (probe rooted at a throwaway worktree, so the number
comes from that tree and not the live one):

    before the wave   1,070 strings / 480 files
    after the wave      970 strings / 450 files
    delta              -100 strings / -30 files

A second correction, on top of the first. The figure this document originally carried
(1,059 / 473) was itself restated, not measured — and this document exists precisely to
fault restating. A verifier could not reproduce 1,059/473 at any commit; the pre-wave
parent measures 1,070/480. Re-measured here at f9d4900be with the probe's hardcoded
RUNTIME repointed at that worktree: 1070 / 480.

Two errors of the same shape, in a document about that shape. The rule this establishes:
the probe hardcodes its root, so measuring an older tree requires a worktree with the
probe's path repointed — running it from a different cwd silently measures the live tree
and reports the current number twice.

The lesson: a number in a commit message is a claim like any other and has to come from
a command whose output you read. Restating a figure from memory, in a message whose
whole purpose is honest measurement, is the exact failure mode this work is meant to
avoid.

No source change accompanies this correction — the code is correct, the description
of it was not.
