/* P6-04 panel inventory — typed client and parser.
 *
 * The registry itself is backend-declared (`voicebridge.business.
 * panel_inventory`). This module does not keep a second catalog: it parses the
 * backend document, rejects a row that could make a panel invent state or
 * point at a renderer as its authority, and turns every failure into a named
 * `error`/`unavailable` state instead of an empty "all good" list.
 *
 * The catalog route is `/agent/api/office/business/panel-inventory`; the
 * Office lead mounts this panel and ensures that route is registered. Until it
 * is, the panel renders the honest typed unavailable state.
 */
import { BusinessApiError, businessFetch } from "./business-api";

export const INVENTORY_SCHEMA = "qubit-office-panel-inventory/1";

export const PANEL_STATES = [
  "loading",
  "empty",
  "error",
  "retry",
  "unavailable",
  "typed_refusal",
] as const;

export type PanelState = typeof PANEL_STATES[number];

export interface PanelAuthority {
  module: string;
  symbol: string;
  route?: string;
  route_method?: string;
}

export interface PanelPermissions {
  read: string;
  write: string;
}

export interface PanelRealAction {
  kind: "real";
  label: string;
  method?: string;
  route?: string;
  module?: string;
  symbol?: string;
}

export interface PanelRefusalAction {
  kind: "typed_refusal";
  label: string;
  code: string;
  detail: string;
  route?: string;
}

export type PanelAction = PanelRealAction | PanelRefusalAction;

export interface PanelRow {
  panel_id: string;
  title: string;
  group: string;
  description?: string;
  authority: PanelAuthority;
  permissions: PanelPermissions;
  states: PanelState[];
  action: PanelAction;
  deep_link: string;
  layout: { split: string; stack: string };
  source_command: string;
}

export interface PanelInventory {
  schema: string;
  required_states?: string[];
  required_panels?: string[];
  panels: PanelRow[];
}

export type InventoryRead =
  | { kind: "loading" }
  | { kind: "available"; inventory: PanelInventory }
  | { kind: "unavailable"; code: string; detail: string }
  | { kind: "error"; message: string };

export class PanelInventoryError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "PanelInventoryError";
    this.code = code;
  }
}

const WRITE_SCOPES = new Set(["owner", "operator", "tenant", "session", "none"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function fail(panelId: string, detail: string): never {
  throw new PanelInventoryError(
    "INVENTORY_INVALID",
    `panel ${panelId || "<unknown>"}: ${detail}`,
  );
}

function isRenderer(moduleName: string): boolean {
  const lowered = moduleName.toLowerCase();
  return lowered.startsWith("frontend")
    || lowered.includes(".game.")
    || lowered.startsWith("game")
    || lowered.startsWith("qubit_office_renderer");
}

function parseAuthority(row: Record<string, unknown>, panelId: string):
PanelAuthority {
  const raw = row.authority;
  if (!isRecord(raw)) fail(panelId, "authority is missing");
  const moduleName = text(raw.module);
  const symbol = text(raw.symbol);
  if (!moduleName) fail(panelId, "authority.module is empty");
  if (isRenderer(moduleName)) {
    fail(panelId, `authority ${moduleName} is a renderer, not data`);
  }
  if (!moduleName.startsWith("voicebridge.")
    && !moduleName.startsWith("qubit_runtime.")) {
    fail(panelId, `authority ${moduleName} is outside the backend packages`);
  }
  if (!symbol) fail(panelId, "authority.symbol is empty");
  const route = text(raw.route);
  const routeMethod = text(raw.route_method);
  if (route && !routeMethod) {
    fail(panelId, "authority.route has no route_method");
  }
  return {
    module: moduleName,
    symbol,
    route: route || undefined,
    route_method: routeMethod || undefined,
  };
}

function parseAction(row: Record<string, unknown>, panelId: string): PanelAction {
  const raw = row.action;
  if (!isRecord(raw)) fail(panelId, "action is missing");
  const kind = text(raw.kind);
  const label = text(raw.label);
  if (!label) fail(panelId, "action.label is empty");
  if (kind === "real") {
    const route = text(raw.route);
    const method = text(raw.method);
    const moduleName = text(raw.module);
    const symbol = text(raw.symbol);
    if (route && !method) fail(panelId, "real route action has no method");
    if (moduleName && isRenderer(moduleName)) {
      fail(panelId, `action module ${moduleName} is a renderer`);
    }
    if (!route && !moduleName) {
      fail(panelId, "real action names neither a route nor a module symbol");
    }
    if (moduleName && !symbol) {
      fail(panelId, "real module action has no symbol");
    }
    return {
      kind: "real",
      label,
      route: route || undefined,
      method: method || undefined,
      module: moduleName || undefined,
      symbol: symbol || undefined,
    };
  }
  if (kind === "typed_refusal") {
    const code = text(raw.code);
    const detail = text(raw.detail);
    if (!code || code !== code.toUpperCase() || code.includes(" ")) {
      fail(panelId, `typed refusal code ${code || "<empty>"} is invalid`);
    }
    if (!detail) fail(panelId, "typed refusal has no detail");
    return {
      kind: "typed_refusal",
      label,
      code,
      detail,
      route: text(raw.route) || undefined,
    };
  }
  return fail(panelId, `action.kind ${kind || "<empty>"} is invalid`);
}

function parseStates(row: Record<string, unknown>, panelId: string):
PanelState[] {
  const raw = row.states;
  if (!Array.isArray(raw)) fail(panelId, "states is not an array");
  const values = raw.map((value) => text(value));
  if (values.length !== new Set(values).size) {
    fail(panelId, "states contains a duplicate");
  }
  const unknown = values.filter(
    (value) => !(PANEL_STATES as readonly string[]).includes(value));
  if (unknown.length) fail(panelId, `unknown states: ${unknown.join(", ")}`);
  const missing = PANEL_STATES.filter((state) => !values.includes(state));
  if (missing.length) fail(panelId, `missing states: ${missing.join(", ")}`);
  return values as PanelState[];
}

function parsePanel(value: unknown, index: number): PanelRow {
  if (!isRecord(value)) fail(`#${index}`, "row is not an object");
  const panelId = text(value.panel_id);
  if (!panelId) fail(`#${index}`, "panel_id is empty");
  const title = text(value.title);
  const group = text(value.group);
  if (!title) fail(panelId, "title is empty");
  if (!group) fail(panelId, "group is empty");
  const permissionsRaw = value.permissions;
  if (!isRecord(permissionsRaw)) fail(panelId, "permissions is missing");
  const read = text(permissionsRaw.read);
  const write = text(permissionsRaw.write);
  if (!read) fail(panelId, "read permission is empty");
  if (!write) fail(panelId, "write permission is empty");
  if (!WRITE_SCOPES.has(write)) {
    fail(panelId, `write scope ${write} is not recognised`);
  }
  const deepLink = text(value.deep_link);
  if (!deepLink.startsWith("/")) {
    fail(panelId, "deep_link must start with '/'");
  }
  const layoutRaw = value.layout;
  if (!isRecord(layoutRaw)) fail(panelId, "layout is missing");
  const split = text(layoutRaw.split);
  const stack = text(layoutRaw.stack);
  if (!split) fail(panelId, "layout.split is empty");
  if (!stack) fail(panelId, "layout.stack is empty");
  const sourceCommand = text(value.source_command);
  if (!sourceCommand) fail(panelId, "source_command is empty");
  return {
    panel_id: panelId,
    title,
    group,
    description: text(value.description) || undefined,
    authority: parseAuthority(value, panelId),
    permissions: { read, write },
    states: parseStates(value, panelId),
    action: parseAction(value, panelId),
    deep_link: deepLink,
    layout: { split, stack },
    source_command: sourceCommand,
  };
}

/** Parse and validate one inventory document. Throws `PanelInventoryError`. */
export function parsePanelInventory(value: unknown): PanelInventory {
  if (!isRecord(value)) {
    throw new PanelInventoryError(
      "INVENTORY_INVALID", "inventory is not an object");
  }
  const schema = text(value.schema);
  if (schema !== INVENTORY_SCHEMA) {
    throw new PanelInventoryError(
      "INVENTORY_SCHEMA_INVALID",
      `inventory schema ${schema || "<empty>"} is not ${INVENTORY_SCHEMA}`,
    );
  }
  const rawPanels = value.panels;
  if (!Array.isArray(rawPanels)) {
    throw new PanelInventoryError(
      "INVENTORY_INVALID", "inventory.panels is not an array");
  }
  const panels = rawPanels.map((row, index) => parsePanel(row, index));
  const ids = panels.map((panel) => panel.panel_id);
  if (ids.length !== new Set(ids).size) {
    throw new PanelInventoryError(
      "INVENTORY_INVALID", "inventory contains a duplicate panel id");
  }
  const required = Array.isArray(value.required_panels)
    ? value.required_panels.map((panel) => text(panel)).filter(Boolean)
    : [];
  const missing = required.filter((panel) => !ids.includes(panel));
  if (missing.length) {
    throw new PanelInventoryError(
      "INVENTORY_INVALID",
      `inventory is missing required panels: ${missing.join(", ")}`,
    );
  }
  return {
    schema,
    required_states: Array.isArray(value.required_states)
      ? value.required_states.map((state) => text(state)).filter(Boolean)
      : [...PANEL_STATES],
    required_panels: required,
    panels,
  };
}

export interface FetchInventoryOptions {
  base?: string;
  fetchImpl?: typeof fetch;
  signal?: AbortSignal;
}

/** Fetch the backend inventory and map every failure to a named state. */
export async function fetchPanelInventory(
  opts: FetchInventoryOptions = {},
): Promise<InventoryRead> {
  let body: unknown;
  try {
    body = await businessFetch<unknown>("/panel-inventory", {
      base: opts.base,
      fetchImpl: opts.fetchImpl,
      signal: opts.signal,
    });
  } catch (err) {
    if (err instanceof BusinessApiError) {
      return { kind: "unavailable", code: err.code, detail: err.message };
    }
    const message = err instanceof Error ? err.message : String(err);
    return { kind: "error", message };
  }
  try {
    return { kind: "available", inventory: parsePanelInventory(body) };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { kind: "error", message };
  }
}

/** Build an inventory from explicit rows (hermetic tests / host-provided data). */
export function inventoryFromRows(rows: unknown): PanelInventory {
  return parsePanelInventory({
    schema: INVENTORY_SCHEMA,
    panels: rows,
  });
}

export function stateBadgeLabel(state: PanelState): string {
  switch (state) {
    case "loading": return "Loading";
    case "empty": return "Empty";
    case "error": return "Error";
    case "retry": return "Retryable";
    case "unavailable": return "Unavailable";
    case "typed_refusal": return "Typed refusal";
    default: return String(state);
  }
}

export function actionSummary(action: PanelAction): string {
  if (action.kind === "real") {
    const target = action.route
      ? `${action.method ?? "GET"} ${action.route}`
      : `${action.module ?? ""}.${action.symbol ?? ""}`;
    return `${action.label} — ${target}`;
  }
  return `${action.label} — refused: ${action.code}`;
}
