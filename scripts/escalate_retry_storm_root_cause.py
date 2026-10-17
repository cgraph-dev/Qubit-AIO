"""File the root cause of the org-wide retry storm with the level above."""
from voicebridge.config import Config
from voicebridge.office import escalation as ESC

SUMMARY = (
    "ROOT CAUSE FOUND for the org-wide retry storm, and it is not the dispatch "
    "dial. Escalations #1/#2 asked for the dial and the stubs; neither named why "
    "every seat turn is unpriceable. Answer: every office_agent has empty "
    "model/provider, so all 10 seats inherit the live profile "
    "'cheapvibecode-deepseek-v4.1-flash' (voice.yaml:186). cheapvibecode.ru is a "
    "third-party relay with no published rate, so resolve_billing_route returns "
    "billing_mode=unknown by design (pinned by "
    "tests/test_office_seat_turn_pricing.py::test_an_unpriceable_route_is_still_unknown). "
    "Therefore EVERY seat turn reports cost_status=unknown -> runner.py:354 records "
    "UNKNOWN_SPEND and leaves the receipt open (D15) -> ledger.health().complete is "
    "false -> the serialized admission gate refuses EVERY seat, not just the one "
    "that ran -> the 900s autowork tick files another 'Self-directed: <slug>' stub. "
    "That loop IS the retry storm: 10 of 12 recorded run_started events are the same "
    "tick brief re-run at ~16min intervals, each paying a real provider call to "
    "rediscover it is not allowed to work, and 6 run_unpriced events say exactly "
    "'the provider returned usage but its price could not be established'. This is "
    "why settling receipts by hand at 0c never held: the org re-wedges within one "
    "tick. "
    "SECOND, INDEPENDENT DEFECT, NOW FIXED: the pricing table listed "
    "deepseek-v4-flash but not the current spelling deepseek-v4.1-flash, so even the "
    "DIRECT DeepSeek route could not price the live model (measured: unknown before, "
    "estimated $0.0511 per 319k/5.3k-token turn after). Fixed in "
    "qubit_runtime/agent/usage_pricing.py with the alias test extended; "
    "qubit_runtime/tests/agent/test_usage_pricing.py 52 passed. "
    "MEASURED ROUTE MATRIX (would it settle?): cheapvibecode+deepseek-v4.1-flash = "
    "unknown -> NO (live, wedges org); cheapvibecode+deepseek-v4-flash = unknown -> "
    "NO (relay, by design); deepseek-direct+deepseek-v4.1-flash = estimated $0.0511 "
    "-> YES (only after the fix above); opencode-go+deepseek-v4.1-flash = "
    "subscription_included $0 -> YES, FREE. An archived session from 2026-09-22 ran "
    "opencode-go, which is why older receipts settled at 0c. "
    "ASK (owner, one decision): point the office seats at a route that is priceable. "
    "opencode-go costs $0 and is the recommended option; deepseek-direct costs "
    "~$0.051 per turn; leaving cheapvibecode guarantees the org re-wedges on the next "
    "seat turn regardless of how many receipts are settled by hand. No further code "
    "change can unblock this. "
    "ALSO DONE THIS TURN: task #2 ('investigate the retry storm') was stranded since "
    "2026-10-16 (claimed by seat 4, turns_used=0, no run_id) with an empty brief and "
    "exit_check 'test -f /tmp/proof.txt'. It is repaired: requeued through "
    "work.requeue(), given a real brief and a machine-checkable exit check, so it is "
    "no longer a landmine at the head of the dispatch queue. Write-up at "
    "docs/RETRY_STORM_ROOT_CAUSE_2026-10-17.md. The book itself is clean: the only "
    "open receipt is the current turn's own, which is expected. Still open and "
    "unchanged: 101 'Self-directed' stubs with exit_check 'true' (do not arm dispatch "
    "on them) and office.autowork.dispatch=false."
)


def main() -> None:
    cfg = Config.load(None)
    out = ESC.raise_to_supervisor(
        cfg, agent_id=3, kind="blocked", summary=SUMMARY,
        payload={"root_cause": "seat route inherits an unpriceable relay",
                 "fix": "point seats at opencode-go (included) or deepseek-direct",
                 "alias_bug_fixed": "qubit_runtime/agent/usage_pricing.py",
                 "task_2": "repaired: real brief + machine-checkable exit check",
                 "stubs_open": 101},
        requested_by="office:lead-quality")
    print(out)


if __name__ == "__main__":
    main()
