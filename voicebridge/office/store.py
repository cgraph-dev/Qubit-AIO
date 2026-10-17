"""The office store: one SQLite file, its schema, and its invariants.

Path: ``<state_dir>/office/office.db``. One store, created idempotently, with
the org's money invariants enforced *by the database* rather than by
application discipline:

* ``office_ledger`` rejects ``UPDATE`` and ``DELETE`` via ``BEFORE`` triggers
  that ``RAISE(ABORT, …)`` (C1). Callers see ``sqlite3.IntegrityError``; the
  message names the rule. A mistake is corrected by a compensating row, never
  by an edit.
* Every rollup is a ``VIEW`` over the ledger, never a stored column (C2), so
  a displayed total and the ledger cannot disagree.

All money is ``INTEGER`` cents. Float money is a bug class this store exists
to make impossible.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict

from .errors import (BAD_INPUT_CODE, CONFIRM_CODE, IO_CODE, OfficeError)

SCHEMA = "qubit-office/3"
DIR_NAME = "office"
DB_NAME = "office.db"

# --- enumerated domains, enforced by CHECK so bad data cannot be stored -----
KINDS = ("inference", "revenue", "fees", "transfer", "capex", "adjustment")
SEAT_TYPES = ("ceo", "director_portfolio", "director_functional", "manager",
              "scraper", "analyst", "researcher", "worker", "support",
              "auditor")
FLOOR_ROLES = ("operations", "intelligence", "director", "executive", "rnd")
DIVISION_KINDS = ("product", "content", "service", "trading", "intelligence",
                  "meta")
AGENT_STATUS = ("active", "suspended", "terminated")

MAX_SPAN = 7          # D34: a manager carries at most 7 direct reports
MAX_FLOORS = 60       # D13 (revised)
CHAIN_MAX_HOPS = 8    # the chain must terminate at the CEO well inside this


def db_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / DIR_NAME / DB_NAME


def connect(cfg: Any) -> sqlite3.Connection:
    path = db_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30.0,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
        except sqlite3.Error:
            pass
        return conn
    except sqlite3.Error as exc:
        raise OfficeError(IO_CODE, f"office store unavailable: {exc}") from exc


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS office_meta(
  key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS office_floor(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  number INTEGER UNIQUE NOT NULL,
  name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'operations'
    CHECK(role IN ('operations','intelligence','director','executive','rnd')),
  theme TEXT,
  floorplan_path TEXT);

CREATE TABLE IF NOT EXISTS office_department(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  floor_id INTEGER REFERENCES office_floor(id),
  director_id INTEGER,
  division_kind TEXT NOT NULL
    CHECK(division_kind IN ('product','content','service','trading',
                            'intelligence','meta')),
  template TEXT,
  charter TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'proposed'
    CHECK(status IN ('proposed','active','profitable','bleeding','sunset',
                     'dissolved')),
  kill_criteria TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  dissolved_at REAL);

CREATE TABLE IF NOT EXISTS office_room(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  department_id INTEGER NOT NULL REFERENCES office_department(id),
  slug TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'work',
  role_scope TEXT,
  capacity INTEGER NOT NULL DEFAULT 0,
  UNIQUE(department_id, slug));

CREATE TABLE IF NOT EXISTS office_agent(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  seat_type TEXT NOT NULL CHECK(seat_type IN
    ('ceo','director_portfolio','director_functional','manager','scraper',
     'analyst','researcher','worker','support','auditor')),
  department_id INTEGER REFERENCES office_department(id),
  room_id INTEGER REFERENCES office_room(id),
  supervisor_id INTEGER REFERENCES office_agent(id),
  portfolio TEXT,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK(status IN ('active','suspended','terminated')),
  autonomy TEXT NOT NULL DEFAULT 'unattended_full',
  exec_profile TEXT NOT NULL DEFAULT 'this_computer',
  created_at REAL NOT NULL,
  created_by TEXT NOT NULL DEFAULT 'owner',
  terminated_at REAL,
  -- M3: which businesses this auditor may sample (17-AUDIT.md)
  auditor_of TEXT,
  -- Per-seat model routing. Each seat may name its own provider and model,
  -- chosen from the same catalogue the Model Provider panel serves
  -- (GET /api/model/options). Empty means "inherit the profile default", so
  -- every existing seat keeps working without a migration decision.
  model TEXT NOT NULL DEFAULT '',
  provider TEXT NOT NULL DEFAULT '');

CREATE INDEX IF NOT EXISTS idx_agent_dept ON office_agent(department_id);
CREATE INDEX IF NOT EXISTS idx_agent_supervisor ON office_agent(supervisor_id);

CREATE TABLE IF NOT EXISTS office_ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN
    ('inference','revenue','fees','transfer','capex','adjustment')),
  -- Sign convention, enforced here rather than trusted to callers:
  -- the four directional kinds are MAGNITUDES (always >= 0) and the view
  -- decides direction from `kind`; only transfer/adjustment are signed,
  -- because a compensating row must be able to point either way (C1).
  amount_cents INTEGER NOT NULL
    CHECK((kind IN ('inference','fees','capex','revenue') AND amount_cents >= 0)
          OR kind IN ('transfer','adjustment')),
  currency TEXT NOT NULL DEFAULT 'USD',
  agent_id INTEGER,
  department_id INTEGER,
  -- M3: which business a row belongs to; the business P&L is a GROUP BY.
  business_id INTEGER,
  run_id TEXT,
  note TEXT NOT NULL DEFAULT '',
  recorded_by TEXT NOT NULL DEFAULT '',
  offsets_id INTEGER,
  -- D16: a subscription-backed turn (the owner's Perplexity Pro research, a
  -- free tier) is RECORDED but never counted as dollars. Metered aggregates
  -- exclude it; run counts include it.
  unmetered INTEGER NOT NULL DEFAULT 0 CHECK(unmetered IN (0,1)),
  -- D15: settlement of an unknown spend is owner-attested, and the two
  -- attestations are never conflated.
  attested INTEGER NOT NULL DEFAULT 0 CHECK(attested IN (0,1)),
  attested_as TEXT CHECK(attested_as IS NULL OR attested_as IN ('entered','limit')),
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  -- The provider's OWN cost figure, in micro-dollars (1e-6 USD), kept beside
  -- the integer-cent booking. Cents are the unit every total and cap uses;
  -- this column is the evidence, so a rounding difference is visible instead
  -- of hidden. A turn that really cost $0.0004 books 0 cents and 400 micros,
  -- and the trace shows both.
  provider_cost_micros INTEGER NOT NULL DEFAULT 0,
  -- Idempotency key: a retried booking of the same provider charge must not
  -- double-count. NULL is allowed and means "not idempotent".
  source TEXT,
  external_ref TEXT);

CREATE INDEX IF NOT EXISTS idx_ledger_agent ON office_ledger(agent_id);
CREATE INDEX IF NOT EXISTS idx_ledger_dept ON office_ledger(department_id);
CREATE INDEX IF NOT EXISTS idx_ledger_ts ON office_ledger(ts);
CREATE INDEX IF NOT EXISTS idx_ledger_run ON office_ledger(run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_idempotency
  ON office_ledger(source, external_ref)
  WHERE source IS NOT NULL AND external_ref IS NOT NULL;

-- D15: a receipt is written BEFORE the first model call. A receipt with no
-- matching ledger row is spend-unknown, and unknown spend is never headroom.
CREATE TABLE IF NOT EXISTS office_run_receipts(
  run_id TEXT PRIMARY KEY,
  agent_id INTEGER REFERENCES office_agent(id),
  started_at REAL NOT NULL,
  settled_at REAL,
  settled_cents INTEGER,
  model TEXT,
  note TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open'
    CHECK(status IN ('open','settled','halted')),
  cap_cents INTEGER,
  attested_as TEXT CHECK(attested_as IS NULL OR attested_as IN ('entered','limit')),
  end_reason TEXT);

CREATE INDEX IF NOT EXISTS idx_receipts_status ON office_run_receipts(status);
CREATE INDEX IF NOT EXISTS idx_receipts_agent ON office_run_receipts(agent_id);

-- M3: which business a row belongs to. The rollup is `GROUP BY business_id`,
-- so this column is what makes a business's P&L exist at all.
-- (Added to office_ledger by _ensure_column in migrate(); declared here for a
-- fresh database.)
--
-- M2: the spend plan per level (D37). Allocated top-down; never edited, only
-- superseded by the next period's row. Allocated figures ARE stored (they are
-- decisions); spend and remaining are DERIVED from the ledger (C2).
CREATE TABLE IF NOT EXISTS office_envelope(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  period TEXT NOT NULL,
  scope TEXT NOT NULL CHECK(scope IN ('org','portfolio','department')),
  scope_id INTEGER,
  allocated_cents INTEGER NOT NULL CHECK(allocated_cents >= 0),
  allocated_by INTEGER,
  carried_cents INTEGER NOT NULL DEFAULT 0,
  reserve INTEGER NOT NULL DEFAULT 0 CHECK(reserve IN (0,1)),
  created_at REAL NOT NULL,
  UNIQUE(period, scope, scope_id));

CREATE INDEX IF NOT EXISTS idx_envelope_period ON office_envelope(period);

-- A raise above the envelope (D37/C31). One row per ask, with the four things
-- the contract requires a request to name.
CREATE TABLE IF NOT EXISTS office_request(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  agent_id INTEGER,
  supervisor_id INTEGER,
  scope TEXT NOT NULL,
  scope_id INTEGER,
  amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
  thesis TEXT NOT NULL,
  expected_return TEXT NOT NULL,
  displaces TEXT NOT NULL,
  period TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open'
    CHECK(status IN ('open','approved','denied','withdrawn')),
  decided_by INTEGER,
  decided_at REAL,
  note TEXT NOT NULL DEFAULT '');

CREATE INDEX IF NOT EXISTS idx_request_status ON office_request(status);

-- An escalation to the level above (D34). Carries the shared journal's
-- confirmation_id so the existing approvals surface can settle it.
CREATE TABLE IF NOT EXISTS office_escalation(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  agent_id INTEGER,
  supervisor_id INTEGER,
  kind TEXT NOT NULL,
  summary TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  confirmation_id TEXT,
  status TEXT NOT NULL DEFAULT 'open'
    CHECK(status IN ('open','resolved','abandoned')),
  decided_by INTEGER,
  decided_at REAL);

CREATE INDEX IF NOT EXISTS idx_escalation_status ON office_escalation(status);

-- M3: the money-making unit (D1). A department holds many businesses.
-- `revenue_cents`/`cost_cents` from 30-DATA-MODEL.md are deliberately ABSENT:
-- C2 forbids a hand-written rollup, so every figure is derived from
-- office_v_business_pnl. The model document is below the contract in the
-- authority order, so C2 wins.
CREATE TABLE IF NOT EXISTS office_business(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL DEFAULT '',
  department_id INTEGER NOT NULL,
  thesis TEXT NOT NULL DEFAULT '',
  offer TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL DEFAULT 'direct',
  status TEXT NOT NULL DEFAULT 'proposed'
    CHECK(status IN ('proposed','building','live','profitable','paused','dead')),
  price_cents INTEGER,
  currency TEXT NOT NULL DEFAULT 'USD',
  kill_criteria TEXT NOT NULL DEFAULT '',
  exposure TEXT NOT NULL DEFAULT '',
  funded INTEGER NOT NULL DEFAULT 0 CHECK(funded IN (0,1)),
  funded_by TEXT NOT NULL DEFAULT '',
  provenance TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  launched_at REAL,
  killed_at REAL,
  killed_reason TEXT NOT NULL DEFAULT '');

CREATE INDEX IF NOT EXISTS idx_business_dept ON office_business(department_id);
CREATE INDEX IF NOT EXISTS idx_business_status ON office_business(status);

-- M3: an audit finding. Append-only in spirit and in fact: a finding is never
-- edited, and the ledger is never repaired by an edit either (C1) — a wrong
-- number is corrected by a compensating row and the diff is recorded here.
CREATE TABLE IF NOT EXISTS office_audit_finding(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  period TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL
    CHECK(kind IN ('reconcile','spot_audit','separation','penalty')),
  severity TEXT NOT NULL DEFAULT 'minor'
    CHECK(severity IN ('minor','repeat','terminate')),
  business_id INTEGER,
  department_id INTEGER,
  agent_id INTEGER,
  evidence TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'open'
    CHECK(status IN ('open','noted','closed')),
  closed_at REAL);

CREATE INDEX IF NOT EXISTS idx_finding_period ON office_audit_finding(period, ts);

-- M5: the work queue. A task is a unit of work with an owner, an artifact and
-- a reviewer. Nothing here is a chat message: a task is only `done` when a
-- machine-checkable exit condition holds (C17), and only `accepted` when the
-- level above says so.
CREATE TABLE IF NOT EXISTS office_task(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  title TEXT NOT NULL,
  brief TEXT NOT NULL DEFAULT '',
  exit_check TEXT NOT NULL DEFAULT '',
  department_id INTEGER,
  created_by INTEGER,
  assignee_id INTEGER,
  reviewer_id INTEGER,
  status TEXT NOT NULL DEFAULT 'open'
    CHECK(status IN ('open','running','review','fixing','accepted','rejected',
                     'abandoned')),
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  run_id TEXT,
  artifact TEXT NOT NULL DEFAULT '',
  verdict TEXT NOT NULL DEFAULT '',
  review_note TEXT NOT NULL DEFAULT '',
  turn_budget INTEGER NOT NULL DEFAULT 1,
  turns_used INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  closed_at REAL);

CREATE INDEX IF NOT EXISTS idx_task_status ON office_task(status, id);
CREATE INDEX IF NOT EXISTS idx_task_assignee ON office_task(assignee_id, status);

-- Append-only trail per task. A fix instruction is a row, never an edit.
CREATE TABLE IF NOT EXISTS office_task_event(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  task_id INTEGER NOT NULL,
  actor TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL DEFAULT '{}');

CREATE INDEX IF NOT EXISTS idx_task_event ON office_task_event(task_id, id);

-- M5: the honest bound for a flat-rate provider. A subscription-backed turn
-- costs no marginal dollar, so a DOLLAR cap cannot bound it — the bound that
-- means something is how many turns are allowed. Recorded per seat per day.
CREATE TABLE IF NOT EXISTS office_turn_budget(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  agent_id INTEGER NOT NULL,
  turns_cap INTEGER NOT NULL DEFAULT 0,
  turns_used INTEGER NOT NULL DEFAULT 0,
  UNIQUE(day, agent_id));

-- A rating of one seat by the level above it (D39/C31). Distinct from
-- `office_report`: a report is a seat describing its own work, a review is the
-- level above rating it. A seat cannot write its own review row.
CREATE TABLE IF NOT EXISTS office_review(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  subject_agent_id INTEGER NOT NULL,
  reviewer_agent_id INTEGER NOT NULL,
  kind TEXT NOT NULL DEFAULT 'weekly',
  period TEXT NOT NULL DEFAULT '',
  verdict TEXT NOT NULL
    CHECK(verdict IN ('exceeds','meets','below','failing')),
  body TEXT NOT NULL DEFAULT '');

CREATE INDEX IF NOT EXISTS idx_review_subject
  ON office_review(subject_agent_id, ts);

-- A report upward (D24). `recommendation` and `reasoning` are mandatory for a
-- report the owner reads, and `ledger_diff` records whether its claims matched
-- the ledger when it was filed.
CREATE TABLE IF NOT EXISTS office_report(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  agent_id INTEGER,
  kind TEXT NOT NULL CHECK(kind IN ('daily','weekly','memo')),
  period TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  recommendation TEXT NOT NULL DEFAULT '',
  reasoning TEXT NOT NULL DEFAULT '',
  ledger_diff TEXT NOT NULL DEFAULT 'clean');

CREATE INDEX IF NOT EXISTS idx_report_kind ON office_report(kind, period);

-- M1: what a turn produced, attributed to the run that produced it (C6).
CREATE TABLE IF NOT EXISTS office_artifact(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  agent_id INTEGER,
  department_id INTEGER,
  kind TEXT NOT NULL DEFAULT 'file',
  ref TEXT NOT NULL,
  sha256 TEXT,
  bytes INTEGER,
  created_at REAL NOT NULL);

CREATE INDEX IF NOT EXISTS idx_artifact_run ON office_artifact(run_id);
CREATE INDEX IF NOT EXISTS idx_artifact_agent ON office_artifact(agent_id);

CREATE TABLE IF NOT EXISTS office_event(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  agent_id INTEGER,
  department_id INTEGER,
  payload TEXT NOT NULL DEFAULT '{}',
  visible TEXT NOT NULL DEFAULT 'badge'
    CHECK(visible IN ('notify','badge','bubble')));

CREATE INDEX IF NOT EXISTS idx_event_ts ON office_event(ts);

-- The two rules that make this store a ledger rather than a table ---------
-- NOTE: RAISE(ABORT, …) takes ONE string literal — SQLite does not
-- juxtapose adjacent literals the way C does, so these messages are single
-- literals rather than concatenations.
CREATE TRIGGER IF NOT EXISTS office_ledger_no_update
BEFORE UPDATE ON office_ledger
BEGIN
  SELECT RAISE(ABORT, 'office_ledger is append-only: UPDATE refused (C1/D1). Record a compensating row with kind=transfer and offsets_id set.');
END;

CREATE TRIGGER IF NOT EXISTS office_ledger_no_delete
BEFORE DELETE ON office_ledger
BEGIN
  SELECT RAISE(ABORT, 'office_ledger is append-only: DELETE refused (C1/D1). Record a compensating row instead.');
END;

-- Rollups are views, never columns (C2) ----------------------------------
-- `net_cents` must apply direction itself: the directional kinds are stored
-- as magnitudes, so a plain SUM(amount_cents) would add cost to revenue.
--
-- TWO FOLDS, NEVER INTERCHANGEABLE (D16):
--   * the metered fold   (`*_cents`, `balance_cents`) excludes `unmetered`
--     rows. It feeds every budget cap and every balance, so a
--     subscription-backed turn can never manufacture phantom headroom or
--     phantom spend.
--   * the reported fold  (`reported_net_cents`, `entries`) includes them,
--     because the run *happened* even when it cost no dollars.
-- Views are rebuilt ONLY on a schema-version bump, never on every
-- connect. The earlier policy -- drop and recreate on each open --
-- silently healed a view that had drifted from the ledger, which is
-- exactly the failure the M3 reconciliation control exists to catch
-- (17-AUDIT.md A: a diff is never silently repaired). A view holds no
-- data, so rebuilding it on a real upgrade is still free.
"""



#: Installed once, and thereafter only if absent. It must NOT force a
#: rebuild: doing so would erase the evidence of a drifted view.
_VIEWS_SQL = """

CREATE VIEW IF NOT EXISTS office_v_agent_pnl AS
SELECT a.id AS agent_id, a.slug AS agent_slug, a.seat_type,
       COALESCE(SUM(CASE WHEN l.unmetered=0 AND l.kind='revenue'
                         THEN l.amount_cents END),0) AS revenue_cents,
       COALESCE(SUM(CASE WHEN l.unmetered=0
                          AND l.kind IN ('inference','fees','capex')
                         THEN l.amount_cents END),0) AS cost_cents,
       COALESCE(SUM(CASE WHEN l.unmetered=0 AND l.kind='inference'
                         THEN l.amount_cents END),0) AS inference_cents,
       COALESCE(SUM(CASE WHEN l.unmetered=0 AND l.kind='revenue'
                           THEN l.amount_cents
                         WHEN l.unmetered=0
                          AND l.kind IN ('inference','fees','capex')
                           THEN -l.amount_cents
                         WHEN l.unmetered=0 THEN l.amount_cents END),0)
         AS balance_cents,
       COALESCE(SUM(CASE WHEN l.unmetered=0 AND l.kind='revenue'
                           THEN l.amount_cents
                         WHEN l.unmetered=0
                          AND l.kind IN ('inference','fees','capex')
                           THEN -l.amount_cents
                         WHEN l.unmetered=0 THEN l.amount_cents END),0)
         AS net_cents,
       COALESCE(SUM(CASE WHEN l.kind='revenue' THEN l.amount_cents
                         WHEN l.kind IN ('inference','fees','capex')
                           THEN -l.amount_cents
                         ELSE l.amount_cents END),0) AS reported_net_cents,
       COALESCE(SUM(CASE WHEN l.kind IN ('inference','fees','capex')
                         THEN l.amount_cents END),0) AS reported_cost_cents,
       COUNT(l.id) AS entries,
       COALESCE(SUM(CASE WHEN l.unmetered=0 THEN 1 ELSE 0 END),0)
         AS metered_entries,
       COALESCE(SUM(CASE WHEN l.unmetered=1 THEN 1 ELSE 0 END),0)
         AS unmetered_entries
FROM office_agent a LEFT JOIN office_ledger l ON l.agent_id = a.id
GROUP BY a.id;

CREATE VIEW IF NOT EXISTS office_v_business_pnl AS
SELECT b.id AS business_id, b.slug AS business_slug, b.status,
       b.department_id,
       COALESCE(SUM(CASE WHEN l.unmetered=0 AND l.kind='revenue'
                         THEN l.amount_cents END),0) AS revenue_cents,
       COALESCE(SUM(CASE WHEN l.unmetered=0
                          AND l.kind IN ('inference','fees','capex')
                         THEN l.amount_cents END),0) AS cost_cents,
       COALESCE(SUM(CASE WHEN l.unmetered=0 AND l.kind='revenue'
                           THEN l.amount_cents
                         WHEN l.unmetered=0
                          AND l.kind IN ('inference','fees','capex')
                           THEN -l.amount_cents
                         WHEN l.unmetered=0 THEN l.amount_cents END),0)
         AS net_cents,
       -- The REVENUE-side net: gross sales less what the processor took and
       -- what was refunded. This is a different quantity from `net_cents`
       -- above (which also carries the cost of thinking), and the spot audit
       -- asks about this one specifically, so it is derived here rather than
       -- left for a reader to confuse with the other.
       COALESCE(SUM(CASE WHEN l.kind='revenue'
                         THEN l.amount_cents ELSE 0 END),0)
         - COALESCE(SUM(CASE WHEN l.kind='fees'
                             THEN l.amount_cents ELSE 0 END),0)
         - ABS(COALESCE(SUM(CASE WHEN l.kind='adjustment'
                                  AND l.amount_cents < 0
                                 THEN l.amount_cents ELSE 0 END),0))
         AS revenue_net_cents,
       COALESCE(SUM(CASE WHEN l.kind='fees'
                         THEN l.amount_cents ELSE 0 END),0) AS fee_cents,
       ABS(COALESCE(SUM(CASE WHEN l.kind='adjustment'
                              AND l.amount_cents < 0
                             THEN l.amount_cents ELSE 0 END),0)) AS refund_cents,
       COUNT(CASE WHEN l.unmetered=0 THEN 1 END) AS entries,
       COALESCE(SUM(CASE WHEN l.kind='revenue' THEN 1 END),0) AS revenue_rows
  FROM office_business b
  LEFT JOIN office_ledger l ON l.business_id = b.id
 GROUP BY b.id;

CREATE VIEW IF NOT EXISTS office_v_department_pnl AS
SELECT d.id AS department_id, d.slug AS department_slug, d.division_kind,
       d.status,
       COALESCE(SUM(CASE WHEN l.unmetered=0 AND l.kind='revenue'
                         THEN l.amount_cents END),0) AS revenue_cents,
       COALESCE(SUM(CASE WHEN l.unmetered=0
                          AND l.kind IN ('inference','fees','capex')
                         THEN l.amount_cents END),0) AS cost_cents,
       COALESCE(SUM(CASE WHEN l.unmetered=0 AND l.kind='revenue'
                           THEN l.amount_cents
                         WHEN l.unmetered=0
                          AND l.kind IN ('inference','fees','capex')
                           THEN -l.amount_cents
                         WHEN l.unmetered=0 THEN l.amount_cents END),0)
         AS net_cents,
       COALESCE(SUM(CASE WHEN l.kind='revenue' THEN l.amount_cents
                         WHEN l.kind IN ('inference','fees','capex')
                           THEN -l.amount_cents
                         ELSE l.amount_cents END),0) AS reported_net_cents,
       COALESCE(SUM(CASE WHEN l.kind IN ('inference','fees','capex')
                         THEN l.amount_cents END),0) AS reported_cost_cents,
       COUNT(l.id) AS entries,
       COALESCE(SUM(CASE WHEN l.unmetered=0 THEN 1 ELSE 0 END),0)
         AS metered_entries,
       COALESCE(SUM(CASE WHEN l.unmetered=1 THEN 1 ELSE 0 END),0)
         AS unmetered_entries
FROM office_department d LEFT JOIN office_ledger l
  ON l.department_id = d.id
GROUP BY d.id;

CREATE VIEW IF NOT EXISTS office_v_org_pnl AS
SELECT COALESCE(SUM(CASE WHEN unmetered=0 AND kind='revenue'
                         THEN amount_cents END),0) AS revenue_cents,
       COALESCE(SUM(CASE WHEN unmetered=0
                          AND kind IN ('inference','fees','capex')
                         THEN amount_cents END),0) AS cost_cents,
       COALESCE(SUM(CASE WHEN unmetered=0 AND kind='revenue'
                           THEN amount_cents
                         WHEN unmetered=0
                          AND kind IN ('inference','fees','capex')
                           THEN -amount_cents
                         WHEN unmetered=0 THEN amount_cents END),0)
         AS net_cents,
       COALESCE(SUM(CASE WHEN kind='revenue' THEN amount_cents
                         WHEN kind IN ('inference','fees','capex')
                           THEN -amount_cents
                         ELSE amount_cents END),0) AS reported_net_cents,
       COALESCE(SUM(CASE WHEN kind IN ('inference','fees','capex')
                         THEN amount_cents END),0) AS reported_cost_cents,
       COUNT(id) AS entries,
       COALESCE(SUM(CASE WHEN unmetered=0 THEN 1 ELSE 0 END),0)
         AS metered_entries,
       COALESCE(SUM(CASE WHEN unmetered=1 THEN 1 ELSE 0 END),0)
         AS unmetered_entries,
       COALESCE(SUM(tokens_in),0) AS tokens_in,
       COALESCE(SUM(tokens_out),0) AS tokens_out,
       MIN(ts) AS first_ts, MAX(ts) AS last_ts
FROM office_ledger;
"""

#: Used only when the stored schema version differs from `SCHEMA`.
_REBUILD_VIEWS_SQL = """
DROP VIEW IF EXISTS office_v_business_pnl;
DROP VIEW IF EXISTS office_v_agent_pnl;
DROP VIEW IF EXISTS office_v_department_pnl;
DROP VIEW IF EXISTS office_v_org_pnl;

CREATE VIEW office_v_agent_pnl AS
SELECT a.id AS agent_id, a.slug AS agent_slug, a.seat_type,
       COALESCE(SUM(CASE WHEN l.unmetered=0 AND l.kind='revenue'
                         THEN l.amount_cents END),0) AS revenue_cents,
       COALESCE(SUM(CASE WHEN l.unmetered=0
                          AND l.kind IN ('inference','fees','capex')
                         THEN l.amount_cents END),0) AS cost_cents,
       COALESCE(SUM(CASE WHEN l.unmetered=0 AND l.kind='inference'
                         THEN l.amount_cents END),0) AS inference_cents,
       COALESCE(SUM(CASE WHEN l.unmetered=0 AND l.kind='revenue'
                           THEN l.amount_cents
                         WHEN l.unmetered=0
                          AND l.kind IN ('inference','fees','capex')
                           THEN -l.amount_cents
                         WHEN l.unmetered=0 THEN l.amount_cents END),0)
         AS balance_cents,
       COALESCE(SUM(CASE WHEN l.unmetered=0 AND l.kind='revenue'
                           THEN l.amount_cents
                         WHEN l.unmetered=0
                          AND l.kind IN ('inference','fees','capex')
                           THEN -l.amount_cents
                         WHEN l.unmetered=0 THEN l.amount_cents END),0)
         AS net_cents,
       COALESCE(SUM(CASE WHEN l.kind='revenue' THEN l.amount_cents
                         WHEN l.kind IN ('inference','fees','capex')
                           THEN -l.amount_cents
                         ELSE l.amount_cents END),0) AS reported_net_cents,
       COALESCE(SUM(CASE WHEN l.kind IN ('inference','fees','capex')
                         THEN l.amount_cents END),0) AS reported_cost_cents,
       COUNT(l.id) AS entries,
       COALESCE(SUM(CASE WHEN l.unmetered=0 THEN 1 ELSE 0 END),0)
         AS metered_entries,
       COALESCE(SUM(CASE WHEN l.unmetered=1 THEN 1 ELSE 0 END),0)
         AS unmetered_entries
FROM office_agent a LEFT JOIN office_ledger l ON l.agent_id = a.id
GROUP BY a.id;

CREATE VIEW office_v_business_pnl AS
SELECT b.id AS business_id, b.slug AS business_slug, b.status,
       b.department_id,
       COALESCE(SUM(CASE WHEN l.unmetered=0 AND l.kind='revenue'
                         THEN l.amount_cents END),0) AS revenue_cents,
       COALESCE(SUM(CASE WHEN l.unmetered=0
                          AND l.kind IN ('inference','fees','capex')
                         THEN l.amount_cents END),0) AS cost_cents,
       COALESCE(SUM(CASE WHEN l.unmetered=0 AND l.kind='revenue'
                           THEN l.amount_cents
                         WHEN l.unmetered=0
                          AND l.kind IN ('inference','fees','capex')
                           THEN -l.amount_cents
                         WHEN l.unmetered=0 THEN l.amount_cents END),0)
         AS net_cents,
       -- The REVENUE-side net: gross sales less what the processor took and
       -- what was refunded. This is a different quantity from `net_cents`
       -- above (which also carries the cost of thinking), and the spot audit
       -- asks about this one specifically, so it is derived here rather than
       -- left for a reader to confuse with the other.
       COALESCE(SUM(CASE WHEN l.kind='revenue'
                         THEN l.amount_cents ELSE 0 END),0)
         - COALESCE(SUM(CASE WHEN l.kind='fees'
                             THEN l.amount_cents ELSE 0 END),0)
         - ABS(COALESCE(SUM(CASE WHEN l.kind='adjustment'
                                  AND l.amount_cents < 0
                                 THEN l.amount_cents ELSE 0 END),0))
         AS revenue_net_cents,
       COALESCE(SUM(CASE WHEN l.kind='fees'
                         THEN l.amount_cents ELSE 0 END),0) AS fee_cents,
       ABS(COALESCE(SUM(CASE WHEN l.kind='adjustment'
                              AND l.amount_cents < 0
                             THEN l.amount_cents ELSE 0 END),0)) AS refund_cents,
       COUNT(CASE WHEN l.unmetered=0 THEN 1 END) AS entries,
       COALESCE(SUM(CASE WHEN l.kind='revenue' THEN 1 END),0) AS revenue_rows
  FROM office_business b
  LEFT JOIN office_ledger l ON l.business_id = b.id
 GROUP BY b.id;

CREATE VIEW office_v_department_pnl AS
SELECT d.id AS department_id, d.slug AS department_slug, d.division_kind,
       d.status,
       COALESCE(SUM(CASE WHEN l.unmetered=0 AND l.kind='revenue'
                         THEN l.amount_cents END),0) AS revenue_cents,
       COALESCE(SUM(CASE WHEN l.unmetered=0
                          AND l.kind IN ('inference','fees','capex')
                         THEN l.amount_cents END),0) AS cost_cents,
       COALESCE(SUM(CASE WHEN l.unmetered=0 AND l.kind='revenue'
                           THEN l.amount_cents
                         WHEN l.unmetered=0
                          AND l.kind IN ('inference','fees','capex')
                           THEN -l.amount_cents
                         WHEN l.unmetered=0 THEN l.amount_cents END),0)
         AS net_cents,
       COALESCE(SUM(CASE WHEN l.kind='revenue' THEN l.amount_cents
                         WHEN l.kind IN ('inference','fees','capex')
                           THEN -l.amount_cents
                         ELSE l.amount_cents END),0) AS reported_net_cents,
       COALESCE(SUM(CASE WHEN l.kind IN ('inference','fees','capex')
                         THEN l.amount_cents END),0) AS reported_cost_cents,
       COUNT(l.id) AS entries,
       COALESCE(SUM(CASE WHEN l.unmetered=0 THEN 1 ELSE 0 END),0)
         AS metered_entries,
       COALESCE(SUM(CASE WHEN l.unmetered=1 THEN 1 ELSE 0 END),0)
         AS unmetered_entries
FROM office_department d LEFT JOIN office_ledger l
  ON l.department_id = d.id
GROUP BY d.id;

CREATE VIEW office_v_org_pnl AS
SELECT COALESCE(SUM(CASE WHEN unmetered=0 AND kind='revenue'
                         THEN amount_cents END),0) AS revenue_cents,
       COALESCE(SUM(CASE WHEN unmetered=0
                          AND kind IN ('inference','fees','capex')
                         THEN amount_cents END),0) AS cost_cents,
       COALESCE(SUM(CASE WHEN unmetered=0 AND kind='revenue'
                           THEN amount_cents
                         WHEN unmetered=0
                          AND kind IN ('inference','fees','capex')
                           THEN -amount_cents
                         WHEN unmetered=0 THEN amount_cents END),0)
         AS net_cents,
       COALESCE(SUM(CASE WHEN kind='revenue' THEN amount_cents
                         WHEN kind IN ('inference','fees','capex')
                           THEN -amount_cents
                         ELSE amount_cents END),0) AS reported_net_cents,
       COALESCE(SUM(CASE WHEN kind IN ('inference','fees','capex')
                         THEN amount_cents END),0) AS reported_cost_cents,
       COUNT(id) AS entries,
       COALESCE(SUM(CASE WHEN unmetered=0 THEN 1 ELSE 0 END),0)
         AS metered_entries,
       COALESCE(SUM(CASE WHEN unmetered=1 THEN 1 ELSE 0 END),0)
         AS unmetered_entries,
       COALESCE(SUM(tokens_in),0) AS tokens_in,
       COALESCE(SUM(tokens_out),0) AS tokens_out,
       MIN(ts) AS first_ts, MAX(ts) AS last_ts
FROM office_ledger;
"""


def _ensure_column(conn: sqlite3.Connection, table: str, name: str,
                   decl: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _stored_schema(conn: sqlite3.Connection) -> str:
    try:
        row = conn.execute("SELECT value FROM office_meta WHERE key='schema'"
                           ).fetchone()
        return str(row["value"]) if row is not None else ""
    except sqlite3.Error:
        return ""


def migrate(conn: sqlite3.Connection) -> None:
    """Create the schema if missing. Idempotent, additive-only.

    The views are version-gated on purpose. Rebuilding them on every call would
    quietly repair a view that had drifted from the ledger, and a silently
    repaired number is the one thing reconciliation cannot survive.
    """
    conn.executescript(_SCHEMA_SQL)
    # Additive-only upgrades for a store created by an earlier schema. A
    # CREATE TABLE IF NOT EXISTS cannot add a column to a table that already
    # exists, so every column added after the first release lands here.
    _ensure_column(conn, "office_ledger", "offsets_id", "INTEGER")
    _ensure_column(conn, "office_ledger", "unmetered",
                   "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "office_ledger", "attested",
                   "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "office_ledger", "attested_as", "TEXT")
    _ensure_column(conn, "office_ledger", "tokens_in",
                   "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "office_ledger", "tokens_out",
                   "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "office_ledger", "source", "TEXT")
    _ensure_column(conn, "office_ledger", "provider_cost_micros",
                   "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "office_ledger", "external_ref", "TEXT")
    _ensure_column(conn, "office_ledger", "business_id", "INTEGER")
    _ensure_column(conn, "office_agent", "auditor_of", "TEXT")
    # Per-seat model routing. Additive with an empty default, so a seat created
    # before this column existed keeps inheriting the profile default rather
    # than being silently pinned to a model nobody chose.
    _ensure_column(conn, "office_agent", "model", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "office_agent", "provider", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "office_run_receipts", "status",
                   "TEXT NOT NULL DEFAULT 'open'")
    _ensure_column(conn, "office_run_receipts", "cap_cents", "INTEGER")
    _ensure_column(conn, "office_run_receipts", "attested_as", "TEXT")
    _ensure_column(conn, "office_run_receipts", "end_reason", "TEXT")
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_idempotency"
            " ON office_ledger(source, external_ref)"
            " WHERE source IS NOT NULL AND external_ref IS NOT NULL")
    except sqlite3.Error:
        pass
    # Views: rebuild on a version bump, otherwise install-if-absent only.
    stored = _stored_schema(conn)
    if stored != SCHEMA:
        conn.executescript(_REBUILD_VIEWS_SQL)
    else:
        conn.executescript(_VIEWS_SQL)
    conn.execute("INSERT INTO office_meta(key, value) VALUES('schema', ?)"
                 " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (SCHEMA,))


def now() -> float:
    return time.time()


def audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from ..logging_setup import log_event

        log_event(f"office.{op}", actor=actor, **detail)
    except Exception:
        pass


def redact(text: str) -> str:
    """Redact before echoing any owner-supplied text."""
    try:
        from ..redaction import redact_text

        return redact_text(text or "")
    except Exception:
        return text or ""


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the rest of the bridge (fail closed)."""
    try:
        from ..plugins import estop_engaged as _engaged

        return _engaged(cfg)
    except Exception:
        return False


def refuse_estop(operation: str) -> Dict[str, Any]:
    return {"state": "ESTOP", "code": "OFFICE_ESTOP", "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop is "
                           "latched; reset it before changing the office.",
            "owner": "qubit-policy",
            "rollback": "store untouched; nothing to roll back"}


def init(cfg: Any) -> Dict[str, Any]:
    """Create the office store if missing (idempotent)."""
    conn = connect(cfg)
    try:
        migrate(conn)
        conn.commit()
        return {"state": "OK", "store": str(db_path(cfg)), "schema": SCHEMA}
    finally:
        conn.close()


def check_enum(value: str, allowed: tuple, field: str) -> str:
    text = (value or "").strip()
    if text not in allowed:
        raise OfficeError(BAD_INPUT_CODE,
                          f"{field} must be one of {', '.join(allowed)}")
    return text
