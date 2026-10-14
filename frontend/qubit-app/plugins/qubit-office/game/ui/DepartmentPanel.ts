/* F12 — the department panel: the per-floor fold the HUD already carries.
 *
 * SOURCE: `hud.floors[]` — `tower.py::hud` builds one row per `office_floor`
 * with `{floor_id, number, role, departments, revenue_cents, cost_cents,
 * net_cents}`. The per-floor money is summed from `office_v_department_pnl`
 * across that floor's departments, so a floor row is already the DEPARTMENT
 * fold rolled up. This panel reads it; it does not re-derive it.
 *
 * THE EDGE CASES THAT MATTER
 * --------------------------
 *   * A floor with `departments: 0` is not hidden. `28-HUD-PANELS.md` says an
 *     empty floor is still shown ("empty floor"), because a floor that vanishes
 *     is a floor the owner forgets exists.
 *   * Money is integer cents, formatted once, from the integer. A null/non-finite
 *     figure reads `unknown`, never `0`.
 *   * The `pnl_source` sentence is carried verbatim so the reader knows which
 *     fold they are looking at.
 */

import type { HudFloor, OfficeTowerPayload } from "../types";
import { money, staleness, type MoneyText, type Staleness } from "./money";

export interface DepartmentRow {
  floorId: number;
  number: number;
  role: string;
  departments: number;
  revenue: MoneyText;
  cost: MoneyText;
  net: MoneyText;
  /** True when the floor has no department on it. Shown, never hidden. */
  empty: boolean;
  /** Sign of the net, for colouring. Presentation only. */
  netSign: "positive" | "negative" | "zero" | "unknown";
  staleness: Staleness;
}

/** The per-floor rows, with the money formatted and the staleness attached. */
export function departmentRows(
  payload: OfficeTowerPayload | null,
  opts: { departmentsByFloor?: Record<number, number> } = {},
): DepartmentRow[] {
  const floors: HudFloor[] = payload?.hud?.floors ?? [];
  const now = payload?.now ?? null;
  const win = payload?.active_window_s ?? 0;
  return floors.map((f) => {
    const rev = money(f.revenue_cents);
    const cost = money(f.cost_cents);
    const net = money(f.net_cents);
    // The payload's own `departments` count is authoritative; the alternative
    // map is only a caller's cross-check and never overrides it.
    const departments = Number.isFinite(Number(f.departments))
      ? Number(f.departments) : 0;
    return {
      floorId: f.floor_id,
      number: f.number,
      role: f.role,
      departments,
      revenue: rev,
      cost,
      net,
      empty: departments === 0,
      netSign: net.known
        ? (net.cents as number) > 0 ? "positive"
          : (net.cents as number) < 0 ? "negative" : "zero"
        : "unknown",
      // The floor fold is computed against the same `now` the HUD was; there is
      // no per-floor timestamp in the payload, so it inherits the HUD's.
      staleness: staleness(now, now, win),
    };
  });
}

/** The org totals, from `hud` (the same fold the CLI reads). */
export interface OrgTotals {
  revenue: MoneyText;
  cost: MoneyText;
  net: MoneyText;
  source: string;
}

export function orgTotals(payload: OfficeTowerPayload | null): OrgTotals | null {
  if (!payload || !payload.hud) return null;
  return {
    revenue: money(payload.hud.revenue_cents),
    cost: money(payload.hud.cost_cents),
    net: money(payload.hud.net_cents),
    source: String(payload.hud.pnl_source ?? ""),
  };
}

/**
 * Cross-check: does the sum of the floor nets equal the org net? They are
 * different folds of the same ledger and SHOULD agree; a disagreement is
 * surfaced (a department assigned to no floor would explain it).
 */
export function floorsReconcile(
  payload: OfficeTowerPayload | null,
): { agree: boolean; floorSum: number | null; org: number | null } {
  if (!payload || !payload.hud) return { agree: true, floorSum: null, org: null };
  const floors = payload.hud.floors ?? [];
  if (floors.length === 0) return { agree: true, floorSum: null, org: null };
  const sum = floors.reduce((n, f) => n + Number(f.net_cents || 0), 0);
  const org = Number(payload.hud.net_cents);
  return { agree: sum === org, floorSum: sum, org };
}

// ---------------------------------------------------------------------------
// Render — uses the HOST's React.
// ---------------------------------------------------------------------------
type ReactLike = { createElement: (...args: unknown[]) => unknown };

export interface DepartmentPanelProps {
  R: ReactLike;
  payload: OfficeTowerPayload | null;
}

export function renderDepartmentPanel(props: DepartmentPanelProps): unknown {
  const { R, payload } = props;
  const rows = departmentRows(payload);
  const totals = orgTotals(payload);
  const reconcile = floorsReconcile(payload);

  if (rows.length === 0) {
    return R.createElement("section", {
      "data-testid": "office-department-panel", "data-floors": "0",
      role: "region", "aria-label": "Departments",
    }, "No floors in this payload.");
  }

  const head = totals
    ? R.createElement("div", {
        key: "org", "data-testid": "department-org-total",
        "data-net": totals.net.text,
        "data-source": totals.source,
      }, `Org: revenue ${totals.revenue.text} · cost ${totals.cost.text} · ` +
         `net ${totals.net.text}`)
    : null;

  const list = rows.map((r) => R.createElement("div", {
    key: r.floorId,
    "data-testid": `department-floor-${r.floorId}`,
    "data-empty": String(r.empty),
    "data-net-sign": r.netSign,
    "data-net": r.net.text,
  }, [
    R.createElement("span", { key: "n" }, `Floor ${r.number} · ${r.role}`),
    R.createElement("span", { key: "d", "data-testid": `department-floor-${r.floorId}-count` },
      r.empty ? "empty floor" : `${r.departments} departments`),
    R.createElement("span", { key: "m" },
      `net ${r.net.text} (rev ${r.revenue.text}, cost ${r.cost.text})`),
  ]));

  const disagree = !reconcile.agree
    ? R.createElement("div", {
        key: "x", role: "status", "data-testid": "department-reconcile",
        style: { color: "var(--office-warn, #d9a02b)",
                 fontSize: "var(--office-fs-xs)" },
      }, `Floor nets sum to ${reconcile.floorSum}c but the org net is ` +
         `${reconcile.org}c — a department may sit on no floor.`)
    : null;

  return R.createElement("section", {
    "data-testid": "office-department-panel",
    "data-floors": String(rows.length),
    role: "region", "aria-label": "Departments",
  }, [head, disagree, ...list]);
}
