#!/usr/bin/env python3
"""P7-02 / P7-03 — hermetic consequence + mature-policy verification.

Runs behavioral checks in memory: no store, no network, no live service and no
credential. It exercises the real ``ConsequenceEngine`` and
``MatureContentPolicy``, with recording authority callbacks, and fails closed
when behavior drifts.

Exit codes: 0 all checks pass, 1 a check failed, 2 the verifier itself failed
to run (unexpected exception or bad arguments). ``--json`` emits a
machine-readable report.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from typing import Any, Callable

from voicebridge.business.consequences import (
    CONSEQUENCE_ACTION_UNKNOWN_CODE,
    CONSEQUENCE_APPROVAL_REQUIRED_CODE,
    CONSEQUENCE_AUTHORITY_UNAVAILABLE_CODE,
    ApprovalGrant,
    AuthorityPort,
    ConsequenceAction,
    ConsequenceEngine,
)
from voicebridge.business.errors import BusinessError
from voicebridge.business.mature_policy import (
    LABEL_CONFLICT,
    LABEL_FADE_TO_BLACK,
    LABEL_SUBSTANCE,
    MATURE_CATEGORY_DISABLED_CODE,
    MATURE_CONSENT_REQUIRED_CODE,
    MATURE_DEPARTMENT_DISABLED_CODE,
    MATURE_DISABLED_CODE,
    MATURE_HOWTO_REFUSED_CODE,
    MatureContentPolicy,
    MatureRequest,
)

OWNER = "verify-owner"


def _expect_code(code: str, call: Callable[[], Any]) -> None:
    try:
        call()
    except BusinessError as exc:
        if exc.code != code:
            raise AssertionError(
                f"expected {code}, got {exc.code}: {exc.detail}"
            ) from exc
        return
    raise AssertionError(f"expected {code}, call succeeded")


def _recorder():
    calls: list[dict[str, Any]] = []

    def sink(payload):
        calls.append(dict(payload))
        return f"evt_{len(calls)}"

    return calls, sink


def _enable(policy: MatureContentPolicy) -> None:
    policy.enable_department(
        actor=OWNER,
        department="Ops",
        categories=("romance", "substances", "fighting", "conflict"),
    )


def _romance(**overrides) -> MatureRequest:
    data = {
        "action": "kiss",
        "department": "Ops",
        "participants": ("a", "b"),
        "actor": "a",
        "ages": {"a": 30, "b": 29},
        "consents": frozenset({"a", "b"}),
        "hr_acknowledged": True,
    }
    data.update(overrides)
    return MatureRequest(**data)


def run_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def record(name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a failed check is data
            checks.append(
                {
                    "name": name,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "trace": traceback.format_exc().splitlines()[-3:],
                }
            )
        else:
            checks.append({"name": name, "ok": True})

    # -- P7-02 -----------------------------------------------------
    def effects_bounded() -> None:
        engine = ConsequenceEngine()
        engine.register(
            ConsequenceAction(
                "verify_huge",
                effects={"stress": 10_000, "health": -10_000},
            )
        )
        result = engine.apply("verify_huge", actor="a", subject="a")
        assert result.after["stress"] == 100.0
        assert result.after["health"] == 0.0
        assert all(0.0 <= value <= 100.0 for value in result.after.values())

    record("p7-02.effects_bounded", effects_bounded)

    def simulation_authority_separated() -> None:
        calls, sink = _recorder()
        engine = ConsequenceEngine(
            authority=AuthorityPort(emit_hr=sink, emit_legal=sink)
        )
        sim = engine.apply("conflict_argument", actor="a", subject="a",
                           target="b")
        assert calls == []
        assert all(not event["authoritative"] for event in sim.simulation_events)
        assert sim.authoritative_events == ()
        approval = ApprovalGrant("apr-v1", "termination", "a", "termination")
        sensitive = engine.apply(
            "termination", actor="hr", subject="a", approved=approval
        )
        assert len(calls) == 1
        assert sensitive.authoritative_events[0]["kind"] == "hr"
        assert sensitive.authoritative_events[0]["event_id"] == "evt_1"
        assert all(
            event["type"].startswith("simulation.")
            for event in sensitive.simulation_events
        )

    record("p7-02.simulation_authority_separated", simulation_authority_separated)

    def approval_required() -> None:
        _calls, sink = _recorder()
        engine = ConsequenceEngine(authority=AuthorityPort(emit_hr=sink))
        before = engine.state.metrics("a")
        _expect_code(
            CONSEQUENCE_APPROVAL_REQUIRED_CODE,
            lambda: engine.apply("termination", actor="hr", subject="a"),
        )
        assert engine.state.metrics("a") == before
        assert engine.audit_records()[-1]["decision"] == "refused"

    record("p7-02.sensitive_requires_approval", approval_required)

    def authority_required() -> None:
        engine = ConsequenceEngine()
        approval = ApprovalGrant("apr-v2", "termination", "a", "termination")
        before = engine.state.metrics("a")
        _expect_code(
            CONSEQUENCE_AUTHORITY_UNAVAILABLE_CODE,
            lambda: engine.apply(
                "termination", actor="hr", subject="a", approved=approval
            ),
        )
        assert engine.state.metrics("a") == before
        assert engine.dead_letters()[-1]["reason"] == "authority_unavailable"

    record("p7-02.authority_required_no_fabrication", authority_required)

    def recovery_path() -> None:
        _calls, sink = _recorder()
        engine = ConsequenceEngine(authority=AuthorityPort(emit_hr=sink))
        approval = ApprovalGrant("apr-v3", "termination", "a", "termination")
        engine.apply("termination", actor="hr", subject="a", approved=approval)
        stressed = engine.state.metrics("a")
        support = engine.apply("counseling_support", actor="hr", subject="a")
        assert support.recovery is True
        assert support.after["stress"] < stressed["stress"]
        assert support.after["health"] > stressed["health"]

    record("p7-02.recovery_path", recovery_path)

    def audit_and_dead_letter() -> None:
        engine = ConsequenceEngine(dead_letter_limit=2)
        engine.apply("coffee_break", actor="a", subject="a")
        assert engine.audit_records()[-1]["decision"] == "applied"
        _expect_code(
            CONSEQUENCE_ACTION_UNKNOWN_CODE,
            lambda: engine.apply("verify_missing", actor="a", subject="a"),
        )
        for index in range(4):
            try:
                engine.apply(f"verify_missing_{index}", actor="a", subject="a")
            except BusinessError:
                pass
        assert len(engine.dead_letters()) == 2
        assert engine.audit_records()[-1]["decision"] == "refused"

    record("p7-02.audit_and_bounded_dead_letter", audit_and_dead_letter)

    # -- P7-03 -----------------------------------------------------
    def mature_off_by_default() -> None:
        policy = MatureContentPolicy(owner_id=OWNER)
        assert policy.enabled is False
        _expect_code(MATURE_DISABLED_CODE, lambda: policy.authorize(_romance()))

    record("p7-03.mature_off_by_default", mature_off_by_default)

    def opt_in_required() -> None:
        policy = MatureContentPolicy(owner_id=OWNER)
        policy.enable_department(actor=OWNER, department="Ops",
                                 categories=("romance",))
        _expect_code(
            MATURE_DEPARTMENT_DISABLED_CODE,
            lambda: policy.authorize(_romance(department="Finance")),
        )
        _expect_code(
            MATURE_CATEGORY_DISABLED_CODE,
            lambda: policy.authorize(_romance(action="alcohol")),
        )

    record("p7-03.department_and_category_opt_in", opt_in_required)

    def consent_required() -> None:
        policy = MatureContentPolicy(owner_id=OWNER)
        _enable(policy)
        _expect_code(
            MATURE_CONSENT_REQUIRED_CODE,
            lambda: policy.authorize(_romance(consents=frozenset({"a"}))),
        )

    record("p7-03.consent_required", consent_required)

    def non_graphic_labels() -> None:
        policy = MatureContentPolicy(owner_id=OWNER)
        _enable(policy)
        auth = policy.authorize(
            _romance(action="private_encounter", privacy="private")
        )
        assert auth.label == LABEL_FADE_TO_BLACK
        assert auth.presentation == "fade-to-black"
        substance = policy.authorize(
            MatureRequest(
                action="alcohol",
                department="Ops",
                participants=("a",),
                actor="a",
                ages={"a": 30},
                consents=frozenset({"a"}),
            )
        )
        assert substance.label == LABEL_SUBSTANCE
        fight = policy.authorize(
            MatureRequest(
                action="fight",
                department="Ops",
                participants=("a", "b"),
                actor="a",
                ages={"a": 30, "b": 29},
                consents=frozenset({"a", "b"}),
                hr_acknowledged=True,
            )
        )
        assert fight.label == LABEL_CONFLICT
        assert fight.presentation == "non-graphic"

    record("p7-03.non_graphic_labels", non_graphic_labels)

    def no_how_to() -> None:
        policy = MatureContentPolicy(owner_id=OWNER)
        _enable(policy)
        _expect_code(
            MATURE_HOWTO_REFUSED_CODE,
            lambda: policy.authorize(
                MatureRequest(
                    action="alcohol",
                    department="Ops",
                    participants=("a",),
                    actor="a",
                    ages={"a": 30},
                    consents=frozenset({"a"}),
                    detail={"dose": "2 mg"},
                )
            ),
        )

    record("p7-03.no_how_to_instructions", no_how_to)

    def audit_every_event() -> None:
        calls: list[dict[str, Any]] = []
        policy = MatureContentPolicy(
            owner_id=OWNER, audit_sink=lambda record: calls.append(dict(record))
        )
        _enable(policy)
        policy.authorize(_romance())
        _expect_code(
            MATURE_CONSENT_REQUIRED_CODE,
            lambda: policy.authorize(_romance(consents=frozenset())),
        )
        records = policy.audit_records()
        assert [record["decision"] for record in records] == [
            "allowed",
            "refused",
        ]
        assert len(calls) == 2
        assert all("detail_keys" in record for record in records)
        assert all("2 mg" not in json.dumps(record) for record in records)

    record("p7-03.every_mature_event_audited", audit_every_event)

    def simulation_never_business_truth() -> None:
        engine = ConsequenceEngine()
        result = engine.apply("coffee_break", actor="a", subject="a")
        assert not hasattr(engine, "store")
        assert not hasattr(engine, "ledger")
        assert result.authoritative_events == ()
        assert engine.incidents() == ()

    record("p7-02.no_fabricated_business_state", simulation_never_business_truth)

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse usage errors are exit 2
        return 2 if exc.code else 0
    try:
        checks = run_checks()
    except Exception as exc:  # noqa: BLE001 - the verifier failed to run
        report = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc().splitlines()[-5:],
        }
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"ERROR: {report['error']}")
        return 2
    failed = [check for check in checks if not check["ok"]]
    report = {
        "status": "PASS" if not failed else "FAIL",
        "checks_total": len(checks),
        "checks_passed": len(checks) - len(failed),
        "checks_failed": len(failed),
        "checks": checks,
        "limitations": [
            "hermetic: in-memory state and recording authority callbacks",
            "does not prove a live HR/legal system, live service or UI",
            "complements, and does not replace, the pytest suites",
        ],
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for check in checks:
            mark = "PASS" if check["ok"] else "FAIL"
            detail = "" if check["ok"] else f" — {check['error']}"
            print(f"{mark} {check['name']}{detail}")
        print(
            f"\n{report['status']}: {report['checks_passed']}/"
            f"{report['checks_total']} checks"
        )
    if failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
