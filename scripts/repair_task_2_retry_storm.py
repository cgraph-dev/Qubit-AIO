"""Repair task #2: give the stranded stub a real brief and a real exit check.

Run once:  .venv/bin/python scripts/repair_task_2_retry_storm.py
"""
from voicebridge.config import Config
from voicebridge.office import store, work as W

BRIEF = """Investigate the retry storm in the inference path.

Reported context: "three turns died on retries last week". The task arrived with
an EMPTY brief and exit_check 'test -f /tmp/proof.txt', so nothing about it was
checkable. It was claimed by seat 4 on 2026-10-16 and the turn never ran
(turns_used=0, no run_id), so it sat at the head of the dispatch queue for ~24h.

THE INVESTIGATION IS ALREADY DONE. The finding is written up at
docs/RETRY_STORM_ROOT_CAUSE_2026-10-17.md. Summary:

It is not a network retry and runner.py contains no retry loop. It is a
self-perpetuating spend loop: a seat turn runs on a route the pricing layer
cannot price -> cost_status "unknown" -> runner.py:354 records UNKNOWN_SPEND and
leaves the receipt open (D15) -> ledger.health().complete is false -> the
serialized admission gate refuses EVERY seat -> the 900s autowork tick files
another 'Self-directed: <slug>' stub. 10 of the 12 recorded runs are the same
tick brief re-attempted at ~16-minute intervals.

Root cause: every seat has empty office_agent.model/provider and therefore
inherits profile cheapvibecode-deepseek-v4.1-flash, a third-party relay with no
published rate, which is deliberately unpriceable. A second defect sat behind
it: the pricing table listed deepseek-v4-flash but not the current spelling
deepseek-v4.1-flash, so even the direct DeepSeek route could not price it. That
alias is fixed in qubit_runtime/agent/usage_pricing.py.

REMAINING WORK FOR THIS TASK: none on the investigation. The open decisions are
owner-level (which route the seats run on; what to do with the 101 stale stubs)
and are recorded in the write-up.

Completion is checked by running the exit_check below. It passes only while the
write-up still carries the finding AND the pricing alias stays pinned by its test.
"""

EXIT_CHECK = (
    "cd /home/trick/Projects/Repos/qubit-voicebridge"
    " && grep -q \"opencode-go\" docs/RETRY_STORM_ROOT_CAUSE_2026-10-17.md"
    " && grep -q \"UNKNOWN_SPEND\" docs/RETRY_STORM_ROOT_CAUSE_2026-10-17.md"
    " && .venv/bin/python -m pytest qubit_runtime/tests/agent/test_usage_pricing.py"
    " -q >/dev/null 2>&1"
)


def main() -> None:
    cfg = Config.load(None)
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        before = conn.execute(
            "SELECT id,status,assignee_id,length(brief),exit_check"
            " FROM office_task WHERE id=2").fetchone()
        print("before:", tuple(before))
        conn.execute(
            "UPDATE office_task SET brief=?, exit_check=? WHERE id=2",
            (BRIEF, EXIT_CHECK))
        conn.commit()
        after = conn.execute(
            "SELECT id,status,assignee_id,length(brief),exit_check"
            " FROM office_task WHERE id=2").fetchone()
        print("after: ", tuple(after))
    finally:
        conn.close()
    W._trail(cfg, 2, actor="seat:3", kind="repaired",
                 body=("real brief and machine-checkable exit check installed; "
                       "the stub's empty brief and 'test -f /tmp/proof.txt' "
                       "replaced after the investigation completed"),
                 payload={"by": "lead-quality"})


if __name__ == "__main__":
    main()
