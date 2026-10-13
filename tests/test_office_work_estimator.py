"""The cost estimator: measured, ranged, and refusing to guess.

Five proofs, and they are the five ways an estimate becomes a lie:

1. **No history refuses.** An empty ledger produces ``cents: None`` and
   confidence ``none`` — never a default that looks authoritative. A number
   invented here is the ``CHAR_FLOOR_ZOOM`` failure in another costume.
2. **Measured history produces a range that contains the mean**, at
   confidence ``measured``, with the basis naming how many runs it used.
3. **A flat-rate provider is classified from the evidence**, and the dollar
   estimate says plainly that it is not meaningful — the turn count is the
   bound. This is the honest answer for this repo on ``opencode-go``.
4. **The class flips to metered when receipts carry real usage**, with the
   evidence named.
5. **A partially-failed turn is excluded and reported** — including a run
   whose cost is unknown is how an estimate becomes a lie (D15).

Hermetic: tmp XDG state only, a real SQLite store built through the real
``receipts``/``ledger``/``store`` modules, no network, no provider call.
"""
from __future__ import annotations


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _store(tmp_path, monkeypatch):
    """A real store with one funded seat, so receipts have a seat to hang on."""
    from voicebridge.office import ledger, org as O, store

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    seat = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    ledger.record(cfg, "revenue", 1_000_000, agent_id=seat["id"],
                  note="owner capital")
    return cfg, seat["id"]


def _metered_turn(cfg, agent_id, *, cents, tokens_in=1000, tokens_out=250,
                  model="gpt-4o-mini", run_id=None):
    """One settled turn carrying the provider's own usage — a measurement."""
    from voicebridge.office import receipts

    run_id = run_id or f"run_m_{cents}_{tokens_in}_{tokens_out}"
    receipts.begin_run(cfg, agent_id, cap_cents=50, model=model,
                       run_id=run_id)
    receipts.finish_run(
        cfg, run_id, settled_cents=cents, tokens_in=tokens_in,
        tokens_out=tokens_out, provider_cost_micros=cents * 10_000,
        model=model, agent_id=agent_id, source="test", external_ref=run_id)
    return run_id


def _flat_turn(cfg, agent_id, *, model="deepseek-v4.1-flash", run_id=None):
    """One settled turn on a flat-rate provider: recorded, no usage (D16)."""
    from voicebridge.office import receipts

    run_id = run_id or f"run_f_{model}"
    receipts.begin_run(cfg, agent_id, cap_cents=50, model=model,
                       run_id=run_id)
    receipts.finish_run(cfg, run_id, settled_cents=0, unmetered=True,
                        tokens_in=0, tokens_out=0, model=model,
                        agent_id=agent_id,
                        note="subscription-backed turn (D16)")
    return run_id


# ------------------------------------------------- 1: no history refuses
def test_an_empty_ledger_refuses_rather_than_defaulting(tmp_path, monkeypatch):
    """The CHAR_FLOOR_ZOOM failure: a value that looks authoritative and is not."""
    from voicebridge.office import work_estimator as WE

    cfg, _ = _store(tmp_path, monkeypatch)

    est = WE.estimate(cfg)
    assert est["cents"] is None            # a refusal, not a default
    assert est["low"] is None and est["high"] is None
    assert est["confidence"] == "none"
    assert "no measured history" in est["reason"]
    assert est["runs_used"] == 0

    # …and the provider class is not asserted from nothing either.
    pc = WE.provider_class(cfg)
    assert pc["kind"] == "unknown"
    assert pc["flat_rate"] is None


def test_observe_on_an_empty_store_reports_zero_real_counts(tmp_path,
                                                            monkeypatch):
    from voicebridge.office import work_estimator as WE

    cfg, _ = _store(tmp_path, monkeypatch)
    obs = WE.observe(cfg)
    assert obs["runs"] == 0
    assert obs["metered_runs"] == 0
    assert obs["unmetered_runs"] == 0
    assert obs["cents_total"] == 0
    assert obs["tokens_in"] == 0 and obs["tokens_out"] == 0
    assert obs["provider_mix"] == {}
    assert obs["excluded"] == []


# --------------------------- 2: measured history -> a range containing the mean
def test_a_range_from_measured_history_contains_the_mean(tmp_path, monkeypatch):
    from voicebridge.office import work_estimator as WE

    cfg, agent_id = _store(tmp_path, monkeypatch)
    costs = [3, 7, 5, 6, 4]            # five metered turns -> "measured"
    for i, c in enumerate(costs):
        _metered_turn(cfg, agent_id, cents=c, run_id=f"run_meas_{i}")

    est = WE.estimate(cfg)
    mean = round(sum(costs) / len(costs))
    assert est["confidence"] == "measured"
    assert est["runs_used"] == len(costs)
    assert len(costs) >= WE.MIN_OBSERVATIONS
    # the range contains the observed mean, and the range is the observed spread
    assert est["low"] <= est["cents"] <= est["high"]
    assert est["cents"] == mean
    assert est["low"] == min(costs) and est["high"] == max(costs)
    # the basis says how many runs it used
    assert str(len(costs)) in est["basis"]
    assert "measured" in est["basis"] or "run(s)" in est["basis"]


def test_below_the_floor_the_confidence_is_weak_not_measured(tmp_path,
                                                             monkeypatch):
    from voicebridge.office import work_estimator as WE

    cfg, agent_id = _store(tmp_path, monkeypatch)
    for i in range(WE.MIN_OBSERVATIONS - 1):
        _metered_turn(cfg, agent_id, cents=5 + i, run_id=f"run_w_{i}")

    est = WE.estimate(cfg)
    assert est["confidence"] == "weak"
    assert est["cents"] is not None
    assert "weak" in est["reason"]


# ---------------- 3: flat-rate classified from evidence; dollars not meaningful
def test_unmetered_history_is_flat_rate_and_the_dollar_estimate_refuses(
        tmp_path, monkeypatch):
    """The real opencode-go case: turns recorded, no dollars to measure."""
    from voicebridge.office import work_estimator as WE

    cfg, agent_id = _store(tmp_path, monkeypatch)
    for i in range(6):
        _flat_turn(cfg, agent_id, model="deepseek-v4.1-flash",
                   run_id=f"run_flat_{i}")

    pc = WE.provider_class(cfg)
    assert pc["flat_rate"] is True
    assert pc["kind"] == "flat_rate"
    # the evidence is named, and it is counts — not a hardcoded provider name
    assert pc["evidence"]["unmetered_runs"] == 6
    assert pc["evidence"]["metered_runs"] == 0
    assert "flat-rate" in pc["reason"]

    obs = WE.observe(cfg)
    assert obs["unmetered_runs"] == 6
    assert obs["metered_runs"] == 0
    assert obs["runs"] == 6          # the runs happened and are on the record

    est = WE.estimate(cfg)
    assert est["cents"] is None      # a dollar figure here would be theatre
    assert est["confidence"] == "none"
    assert "not meaningful" in est["reason"]
    # …and it names the real bound instead: the turn count (M5)
    assert "turn count" in est["reason"]


# ------------------------------- 4: the class flips on real usage, evidence named
def test_provider_class_flips_to_metered_when_receipts_carry_usage(
        tmp_path, monkeypatch):
    from voicebridge.office import work_estimator as WE

    cfg, agent_id = _store(tmp_path, monkeypatch)
    # first, only flat-rate history
    for i in range(3):
        _flat_turn(cfg, agent_id, run_id=f"run_flatx_{i}")
    assert WE.provider_class(cfg)["flat_rate"] is True

    # now a turn that carries the provider's own tokens -> a measurement
    _metered_turn(cfg, agent_id, cents=6, tokens_in=1500, tokens_out=300,
                  model="gpt-4o-mini", run_id="run_metered_real")

    pc = WE.provider_class(cfg)
    assert pc["flat_rate"] is False
    assert pc["kind"] in ("metered", "mixed")
    assert pc["evidence"]["metered_runs"] >= 1
    assert pc["evidence"]["tokens_reported"] >= 1800
    assert "metered" in pc["reason"]


# ------------------------- 5: a partially-failed turn is excluded and reported
def test_an_unsettled_turn_is_excluded_and_reported(tmp_path, monkeypatch):
    """D15: a receipt with no settled ledger row is unknown spend, not zero."""
    from voicebridge.office import receipts, work_estimator as WE

    cfg, agent_id = _store(tmp_path, monkeypatch)
    for i in range(5):
        _metered_turn(cfg, agent_id, cents=5, run_id=f"run_ok_{i}")

    # A turn that began and never settled: its cost is UNKNOWN.
    receipts.begin_run(cfg, agent_id, cap_cents=50, model="gpt-4o-mini",
                       run_id="run_unknown_1")

    est = WE.estimate(cfg)
    # the unknown run is excluded from the basis…
    assert est["runs_used"] == 5
    assert est["confidence"] == "measured"
    # …and reported as excluded, named, with why
    assert any(e["run_id"] == "run_unknown_1" for e in est["excluded"])
    excl = [e for e in est["excluded"] if e["run_id"] == "run_unknown_1"][0]
    assert "unknown" in excl["why"].lower()

    obs = WE.observe(cfg)
    assert obs["unknown_runs"] == 1
    assert obs["metered_runs"] == 5
    # the excluded run did not inflate or deflate the totals
    assert obs["cents_total"] == 25


def test_a_limit_booked_turn_is_not_treated_as_a_measurement(tmp_path,
                                                             monkeypatch):
    """A cap is not a measurement (D15): it is excluded and named as such."""
    from voicebridge.office import receipts, work_estimator as WE

    cfg, agent_id = _store(tmp_path, monkeypatch)
    for i in range(5):
        _metered_turn(cfg, agent_id, cents=5, run_id=f"run_mok_{i}")

    # an unsettled run the owner closes at its own cap
    receipts.begin_run(cfg, agent_id, cap_cents=50, model="gpt-4o-mini",
                       run_id="run_limit_1")
    receipts.settle(cfg, "run_limit_1", cents=50, attested_as="limit")

    est = WE.estimate(cfg)
    assert est["runs_used"] == 5                 # the limit booking is not used
    assert not any(m["run_id"] == "run_limit_1"
                   for m in WE.observe(cfg)["measured"])
    assert any(e["run_id"] == "run_limit_1" for e in est["excluded"])


def test_a_mixed_provider_says_the_estimate_covers_only_the_metered_share(
        tmp_path, monkeypatch):
    from voicebridge.office import work_estimator as WE

    cfg, agent_id = _store(tmp_path, monkeypatch)
    for i in range(3):
        _metered_turn(cfg, agent_id, cents=5, run_id=f"run_mix_m_{i}")
    for i in range(3):
        _flat_turn(cfg, agent_id, run_id=f"run_mix_f_{i}")

    pc = WE.provider_class(cfg)
    assert pc["flat_rate"] is False          # it CAN bill…
    assert pc["kind"] == "mixed"
    assert "only the metered share" in pc["reason"]

    est = WE.estimate(cfg)
    assert est["cents"] is not None
    assert "metered share" in est["reason"]
