import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  agentsPanelUrl,
  fetchAgentPanel,
  groupingProblem,
  readToken,
  seatTypeLabel,
  summaryLine,
  type AgentPanelData,
  type AgentSeat,
} from "./agents-panel";
import { createAgentsPanel } from "./AgentsPanel";
import {
  bodyCellStyle,
  initialsFor,
  portraitCellStyle,
  preloadThemeAtlases,
  themeAtlasUrls,
  type OfficeTheme,
} from "./office-theme";

/** A minimal payload shaped exactly like the real route's answer. */
function payload(over: Partial<AgentPanelData> = {}): AgentPanelData {
  const seats = [
    { slug: "a-one", name: "One", seat_type: "director_portfolio", supervisor: "ceo",
      division: "alpha", room: "boardroom", emoji: "🔍", color: "#111",
      portrait: null, body: null },
    { slug: "a-two", name: "Two", seat_type: "worker", supervisor: "a-one",
      division: "alpha", room: "alpha-desk-a", emoji: "🛠", color: "#222",
      portrait: null, body: null },
    { slug: "x-ceo", name: "Chief of Staff", seat_type: "director_functional",
      supervisor: "ceo", division: "", room: "boardroom", emoji: "🎛", color: "cyan",
      portrait: null, body: null },
  ];
  return {
    ok: true,
    schema: "qubit-office-agents-panel/1",
    counts: {
      headcount: seats.length, divisions: 1, roster_divisions: 1,
      boardroom_seats: 1, rooms: 2, floors: 1,
      seat_types: { director_portfolio: 1, worker: 1, director_functional: 1 },
      per_division: { alpha: 2, "": 1 },
    },
    divisions: [
      { slug: "alpha", label: "Alpha", kind: "product", floor: 1,
        headcount: 2, seats: seats.slice(0, 2) },
      { slug: "", label: "Boardroom (executive tier)", kind: "executive",
        floor: null, headcount: 1, seats: seats.slice(2) },
    ],
    seats,
    ...over,
  };
}

const respond = (status: number, body: unknown): Response =>
  new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  });

describe("the panel's route", () => {
  it("lives under /agent — a bare /api call from the office document is a 404", () => {
    expect(agentsPanelUrl("")).toBe("/agent/api/office/habblaud/agents/panel");
  });

  it("rebases onto a subpath deployment", () => {
    expect(agentsPanelUrl("/qubit/")).toBe(
      "/qubit/agent/api/office/habblaud/agents/panel");
  });

  it("carries the active theme id so the route can resolve its cells", () => {
    expect(agentsPanelUrl("", "demo")).toBe(
      "/agent/api/office/habblaud/agents/panel?theme=demo");
    expect(agentsPanelUrl("/qubit/", "demo")).toBe(
      "/qubit/agent/api/office/habblaud/agents/panel?theme=demo");
  });

  it("keeps the classic request byte-identical", () => {
    expect(agentsPanelUrl("", "classic")).toBe(
      "/agent/api/office/habblaud/agents/panel");
    expect(agentsPanelUrl("", "")).toBe(
      "/agent/api/office/habblaud/agents/panel");
  });
});

describe("the session token", () => {
  it("reads the document's own injected token", () => {
    const win = { __QUBIT_SESSION_TOKEN__: "tok", parent: {} } as unknown as Window;
    expect(readToken(win)).toBe("tok");
  });

  it("falls back to the PARENT document (the embedded app's realm)", () => {
    const win = { parent: { __QUBIT_SESSION_TOKEN__: "parent-tok" } } as unknown as Window;
    expect(readToken(win)).toBe("parent-tok");
  });

  it("is empty when neither document carries one", () => {
    expect(readToken({ parent: {} } as unknown as Window)).toBe("");
  });
});

describe("counts and labels", () => {
  it("summarises the real numbers and nothing more", () => {
    const line = summaryLine(payload().counts);
    expect(line).toBe("3 seats · 1 divisions · 2 rooms · 1 in the boardroom");
  });

  it("names every seat type the plan builds", () => {
    expect(seatTypeLabel("director_functional")).toBe("Functional director");
    expect(seatTypeLabel("director_portfolio")).toBe("Portfolio director");
    expect(seatTypeLabel("worker")).toBe("Worker");
    expect(seatTypeLabel("mystery")).toBe("mystery");
  });
});

describe("a grouping that does not add up is named, never rendered short", () => {
  it("accepts a consistent payload", () => {
    expect(groupingProblem(payload())).toBe("");
  });

  it("refuses a bucket count that disagrees with the seat list", () => {
    const bad = payload();
    bad.divisions = [bad.divisions![0]];
    expect(groupingProblem(bad)).toContain("groups 2 seats but the route served 3");
  });

  it("refuses a headcount that disagrees with the seat list", () => {
    const bad = payload();
    bad.counts = { ...bad.counts!, headcount: 9 };
    expect(groupingProblem(bad)).toContain("headcount (9)");
  });
});

describe("fetching the panel", () => {
  it("sends the token header and parses the real payload", async () => {
    const fetchImpl = vi.fn(async () => respond(200, payload()));
    const state = await fetchAgentPanel({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      win: { __QUBIT_SESSION_TOKEN__: "tok", parent: {} } as unknown as Window,
      base: "",
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/agent/api/office/habblaud/agents/panel");
    expect((init.headers as Record<string, string>)["X-Qubit-Session-Token"]).toBe("tok");
    expect(state.kind).toBe("ready");
    if (state.kind === "ready") {
      expect(state.data.counts?.headcount).toBe(3);
    }
  });

  it("passes the requested theme id to the panel request", async () => {
    const fetchImpl = vi.fn(async () => respond(200, payload()));
    await fetchAgentPanel({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      win: null, base: "", theme: "demo",
    });
    const [url] = fetchImpl.mock.calls[0] as unknown as [string];
    expect(url).toBe("/agent/api/office/habblaud/agents/panel?theme=demo");
  });

  it("names a 401 as a token problem instead of an empty roster", async () => {
    const state = await fetchAgentPanel({
      fetchImpl: (async () => respond(401, { detail: "unauthorized" })) as unknown as typeof fetch,
      win: null, base: "",
    });
    expect(state.kind).toBe("error");
    if (state.kind === "error") expect(state.message).toContain("HTTP 401");
  });

  it("names a 404 as a missing route, not as 'no agents'", async () => {
    const state = await fetchAgentPanel({
      fetchImpl: (async () => respond(404, { detail: "no route" })) as unknown as typeof fetch,
      win: null, base: "",
    });
    expect(state.kind).toBe("error");
    if (state.kind === "error") expect(state.message).toContain("HTTP 404");
  });

  it("carries the server's own refusal detail", async () => {
    const state = await fetchAgentPanel({
      fetchImpl: (async () => respond(503, {
        ok: false, code: "AGENCY_ORG_UNAVAILABLE",
        detail: "the building plan refused this roster: span",
      })) as unknown as typeof fetch,
      win: null, base: "",
    });
    expect(state.kind).toBe("error");
    if (state.kind === "error") {
      expect(state.message).toContain("the building plan refused this roster");
    }
  });

  it("refuses to call an inconsistent payload ready", async () => {
    const bad = payload();
    bad.seats = bad.seats!.slice(0, 1);
    const state = await fetchAgentPanel({
      fetchImpl: (async () => respond(200, bad)) as unknown as typeof fetch,
      win: null, base: "",
    });
    expect(state.kind).toBe("error");
    if (state.kind === "error") expect(state.message).toContain("inconsistent");
  });
});

// ======================================================================
// Theme sprites, initials fallback, and the dialogue agent card
// ======================================================================

function seat(over: Partial<AgentSeat> & { slug: string; name: string }): AgentSeat {
  return {
    seat_type: "worker", supervisor: null, division: "alpha",
    room: "alpha-desk-a", emoji: "🤖", color: "#111",
    portrait: null, body: null,
    ...over,
  };
}

/** A one-division payload whose counts are internally consistent. */
function oneDivision(seats: AgentSeat[]): AgentPanelData {
  return {
    ok: true,
    counts: {
      headcount: seats.length, divisions: 1, roster_divisions: 1,
      boardroom_seats: 0, rooms: 1, floors: 1,
      seat_types: { worker: seats.length }, per_division: { alpha: seats.length },
    },
    divisions: [{
      slug: "alpha", label: "Alpha", kind: "product", floor: 1,
      headcount: seats.length, seats,
    }],
    seats,
  };
}

/** The active payload shape Tasks 1-4 serve, reduced to what the panel reads. */
function activeTheme(over: Partial<OfficeTheme> = {}): OfficeTheme {
  return {
    id: "demo", label: "Demo", version: "1.0.0",
    credit: "LimeZu — https://limezu.itch.io",
    license: "LimeZu licence; redistribution prohibited",
    active: true, templateVersion: 1,
    assetBase: "/plugins/qubit-office/themes/demo",
    atlas: "/plugins/qubit-office/themes/demo/portraits/atlas32.png?v=1.0.0",
    index: "/plugins/qubit-office/themes/demo/portraits/index.json?v=1.0.0",
    bodies: {
      atlas: "/plugins/qubit-office/themes/demo/bodies/atlas32.png?v=1.0.0",
      index: "/plugins/qubit-office/themes/demo/bodies/index.json?v=1.0.0",
      size: 32,
      frame: { w: 32, h: 64 },
    },
    tokens: {},
    slices: {},
    ...over,
  };
}

/**
 * A faithful-enough `Image` double: setting `src` records the request and fires
 * onload (or onerror) on the next macrotask, exactly as a decoded sheet would.
 * It is NEVER rendered — the panel has no <img> — so its request count is the
 * proof the preload is two sheets, not one per seat.
 */
class FakeAtlasImage {
  static sources: string[] = [];
  static fail = false;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private value = "";

  set src(next: string) {
    this.value = next;
    FakeAtlasImage.sources.push(next);
    setTimeout(() => {
      if (FakeAtlasImage.fail) this.onerror?.();
      else this.onload?.();
    }, 0);
  }

  get src(): string {
    return this.value;
  }
}

const originalImage = (globalThis as { Image?: unknown }).Image;

beforeEach(() => {
  FakeAtlasImage.sources = [];
  FakeAtlasImage.fail = false;
  (globalThis as { Image?: unknown }).Image = FakeAtlasImage;
});

afterEach(() => {
  (globalThis as { Image?: unknown }).Image = originalImage;
});

type RecordedElement = { type: unknown; props: Record<string, any> };

/**
 * A tiny React recorder with real state/effect semantics — the same idea as
 * OfficeAppShell.test.ts, extended to flush state updates and async effects so
 * a preload promise can be driven to success or failure.
 */
function createPanelHarness() {
  const tree: RecordedElement[] = [];
  const states: unknown[] = [];
  const effectDeps: Array<unknown[] | undefined> = [];
  const cleanups: Array<(() => void) | undefined> = [];
  const refSlots: Array<{ current: unknown }> = [];
  let hookIndex = 0;
  let effectIndex = 0;
  let refIndex = 0;
  let dirty = false;
  let Component: (props: Record<string, unknown>) => unknown;
  let props: Record<string, unknown> = {};

  const React = {
    createElement(
      type: unknown,
      elementProps: Record<string, unknown> | null,
      ...children: unknown[]
    ) {
      const element = { type, props: { ...(elementProps ?? {}), children } };
      tree.push(element);
      return element;
    },
  };

  const hooks = {
    useState(initial: unknown) {
      const slot = hookIndex++;
      if (!(slot in states)) states[slot] = initial;
      const setState = (next: unknown) => {
        const previous = states[slot];
        states[slot] = typeof next === "function"
          ? (next as (value: unknown) => unknown)(previous)
          : next;
        dirty = true;
      };
      return [states[slot], setState];
    },
    useEffect(fn: () => void | (() => void), deps?: unknown[]) {
      const slot = effectIndex++;
      const previous = effectDeps[slot];
      const same = previous !== undefined && deps !== undefined
        && previous.length === deps.length
        && deps.every((value, i) => value === previous[i]);
      if (same) return;
      cleanups[slot]?.();
      cleanups[slot] = fn() ?? undefined;
      effectDeps[slot] = deps ? deps.slice() : undefined;
    },
    useRef(initial: unknown) {
      const slot = refSlots[refIndex]
        ?? (refSlots[refIndex] = { current: initial });
      refIndex += 1;
      return slot;
    },
  };

  const render = () => {
    hookIndex = 0;
    effectIndex = 0;
    refIndex = 0;
    tree.length = 0;
    Component(props);
  };

  return {
    tree, React, hooks,
    mount(nextComponent: (props: Record<string, unknown>) => unknown,
          nextProps: Record<string, unknown>) {
      Component = nextComponent;
      props = nextProps;
      states.length = 0;
      effectDeps.length = 0;
      cleanups.length = 0;
      refSlots.length = 0;
      dirty = false;
      render();
    },
    rerender(nextProps: Record<string, unknown>) {
      props = nextProps;
      dirty = true;
      this.flush();
    },
    flush(max = 30) {
      let rounds = 0;
      while (dirty && rounds++ < max) {
        dirty = false;
        render();
      }
    },
    async settle() {
      for (let i = 0; i < 15; i += 1) {
        await Promise.resolve();
        await new Promise((resolve) => setTimeout(resolve, 0));
        this.flush();
      }
    },
    // Flush promises only: enough for the real fetch to land, but NOT enough
    // for the fake Image's macrotask to fire — the "before decode" window.
    async microtasks() {
      for (let i = 0; i < 20; i += 1) {
        await Promise.resolve();
        this.flush();
      }
    },
    cleanup() {
      for (const fn of cleanups.splice(0)) fn?.();
    },
  };
}

async function mountPanel(
  theme: OfficeTheme | null | undefined,
  data: AgentPanelData,
  extraProps: Record<string, unknown> = {},
) {
  const harness = createPanelHarness();
  const Panel = createAgentsPanel(harness.React as never, harness.hooks as never);
  const fetchImpl = (async () => respond(200, data)) as unknown as typeof fetch;
  harness.mount(Panel as never, {
    base: "", fetchImpl, theme, ...extraProps,
  });
  await harness.settle();
  return harness;
}

const withClass = (h: ReturnType<typeof createPanelHarness>, name: string) =>
  h.tree.filter((element) => element.props.className === name);

const byTestId = (h: ReturnType<typeof createPanelHarness>, id: string) =>
  h.tree.filter((element) => element.props["data-testid"] === id);

const avatarFor = (
  h: ReturnType<typeof createPanelHarness>,
  slug: string,
  sprite: string,
) => h.tree.find((element) =>
  element.props["data-testid"] === "agents-panel-avatar"
  && element.props["data-seat-slug"] === slug
  && element.props["data-sprite"] === sprite);

describe("theme helpers", () => {
  it("reads both atlas URLs, or nulls for classic", () => {
    expect(themeAtlasUrls(activeTheme())).toEqual({
      portrait: "/plugins/qubit-office/themes/demo/portraits/atlas32.png?v=1.0.0",
      body: "/plugins/qubit-office/themes/demo/bodies/atlas32.png?v=1.0.0",
    });
    expect(themeAtlasUrls(null)).toEqual({ portrait: null, body: null });
    expect(themeAtlasUrls({
      ...activeTheme(), atlas: null, bodies: {
        atlas: null, index: null, size: null, frame: null,
      },
    })).toEqual({ portrait: null, body: null });
  });

  it("positions a portrait cell with the atlas custom properties", () => {
    expect(portraitCellStyle([2, 3], 32)).toEqual({
      "--cell-x": 2,
      "--cell-y": 3,
      backgroundPosition:
        "calc(-1 * var(--cell-x) * 32px) calc(-1 * var(--cell-y) * 32px)",
    });
    expect(portraitCellStyle(null, 32)).toBeUndefined();
  });

  it("positions a body frame with its own width and height, not one square", () => {
    expect(bodyCellStyle([1, 2], { w: 32, h: 64 })).toEqual({
      "--body-x": 1,
      "--body-y": 2,
      backgroundPosition:
        "calc(-1 * var(--body-x) * 32px) calc(-1 * var(--body-y) * 64px)",
    });
    expect(bodyCellStyle([1, 2], null)).toBeUndefined();
  });

  it("derives initials from the first and last word", () => {
    expect(initialsFor("Chief of Staff")).toBe("CS");
    expect(initialsFor("One")).toBe("O");
    expect(initialsFor("  grace   brewster hopper ")).toBe("GH");
    expect(initialsFor("")).toBe("");
    expect(initialsFor("🎛")).toBe("");
    // Scan past an emoji/punctuation prefix instead of dropping the word.
    expect(initialsFor("🤖Bot")).toBe("B");
    expect(initialsFor("«Ada» Lovelace")).toBe("AL");
  });

  it("preloads each atlas exactly once and resolves true only if all decode", async () => {
    const ok = await preloadThemeAtlases(activeTheme(), FakeAtlasImage as never);
    expect(ok).toBe(true);
    expect(FakeAtlasImage.sources).toHaveLength(2);
    expect(FakeAtlasImage.sources).toEqual(expect.arrayContaining([
      activeTheme().atlas,
      activeTheme().bodies.atlas,
    ]));

    FakeAtlasImage.sources = [];
    FakeAtlasImage.fail = true;
    const failed = await preloadThemeAtlases(activeTheme(), FakeAtlasImage as never);
    expect(failed).toBe(false);
    expect(FakeAtlasImage.sources).toHaveLength(2);

    expect(preloadThemeAtlases(null, FakeAtlasImage as never)).toBeNull();
  });
});

describe("atlas portraits in the agents panel", () => {
  it("keeps initials on screen BEFORE the success preload decodes", async () => {
    // The promise-only flush lands the fetch but NOT the fake Image's macrotask:
    // this is exactly the frame where a CSS sprite would be an empty box.
    const harness = createPanelHarness();
    const Panel = createAgentsPanel(
      harness.React as never, harness.hooks as never);
    const fetchImpl = (async () => respond(
      200,
      oneDivision([seat({ slug: "ada", name: "Ada Lovelace", portrait: [2, 1] })]),
    )) as unknown as typeof fetch;
    harness.mount(Panel as never, {
      base: "", fetchImpl, theme: activeTheme(),
    });
    await harness.microtasks();

    const root = harness.tree.find(
      (e) => e.props["data-testid"] === "agents-panel");
    expect(root?.props["data-state"]).toBe("ready");
    expect(root?.props["data-portraits"]).toBe("fallback");
    expect(avatarFor(harness, "ada", "initials")).toBeDefined();
    expect(avatarFor(harness, "ada", "portrait")).toBeUndefined();

    await harness.settle();
    expect(avatarFor(harness, "ada", "portrait")).toBeDefined();
    expect(avatarFor(harness, "ada", "initials")).toBeUndefined();
    harness.cleanup();
  });

  it("sends the host's active theme id with the panel fetch", async () => {
    const harness = createPanelHarness();
    const Panel = createAgentsPanel(
      harness.React as never, harness.hooks as never);
    const seen: string[] = [];
    const fetchImpl = (async (url: string) => {
      seen.push(String(url));
      return respond(200, oneDivision([seat({ slug: "ada", name: "Ada" })]));
    }) as unknown as typeof fetch;
    harness.mount(Panel as never, {
      base: "", fetchImpl, theme: activeTheme(),
    });
    await harness.settle();
    expect(seen).toContain(
      "/agent/api/office/habblaud/agents/panel?theme=demo");
    harness.cleanup();
  });

  it("never paints a new atlas with the previous pack's ready state", async () => {
    const firstTheme = activeTheme();
    const nextTheme = activeTheme({
      atlas: "/plugins/qubit-office/themes/demo2/portraits/atlas32.png?v=2.0.0",
      bodies: {
        atlas: "/plugins/qubit-office/themes/demo2/bodies/atlas32.png?v=2.0.0",
        index: "/plugins/qubit-office/themes/demo2/bodies/index.json?v=2.0.0",
        size: 32,
        frame: { w: 32, h: 64 },
      },
    });
    const seats = oneDivision([
      seat({ slug: "ada", name: "Ada Lovelace", portrait: [2, 1] }),
    ]);
    const fetchImpl = (async () => respond(200, seats)) as unknown as typeof fetch;
    const h = await mountPanel(firstTheme, seats);
    expect(h.tree.find(
      (e) => e.props["data-testid"] === "agents-panel")?.props["data-portraits"])
      .toBe("ready");

    // A theme change must drop readiness IMMEDIATELY (before demo2 decodes).
    h.rerender({ base: "", fetchImpl, theme: nextTheme });
    const afterSwap = h.tree.find(
      (e) => e.props["data-testid"] === "agents-panel");
    expect(afterSwap?.props["data-portraits"]).toBe("fallback");
    expect(avatarFor(h, "ada", "initials")).toBeDefined();
    expect(avatarFor(h, "ada", "portrait")).toBeUndefined();

    await h.settle();
    expect(h.tree.find(
      (e) => e.props["data-testid"] === "agents-panel")?.props["data-portraits"])
      .toBe("ready");
    expect(avatarFor(h, "ada", "portrait")).toBeDefined();
    h.cleanup();
  });

  it("paints a seat's cell as a 32x32 sprite span once the sheets decode", async () => {
    const h = await mountPanel(
      activeTheme(),
      oneDivision([seat({ slug: "ada", name: "Ada Lovelace", portrait: [2, 1] })]),
    );
    const root = h.tree.find((e) => e.props["data-testid"] === "agents-panel");
    expect(root?.props.className).toContain("office-portraits-ready");
    expect(root?.props["data-portraits"]).toBe("ready");

    const sprite = avatarFor(h, "ada", "portrait");
    expect(sprite).toBeDefined();
    expect(sprite?.props.className).toBe("agents-panel__portrait");
    expect(sprite?.props.role).toBe("img");
    expect(sprite?.props["aria-label"]).toBe("Ada Lovelace portrait");
    const style = sprite?.props.style as Record<string, unknown>;
    expect(style["--cell-x"]).toBe(2);
    expect(style["--cell-y"]).toBe(1);
    expect(String(style.backgroundPosition)).toContain("var(--cell-x)");
    expect(String(style.backgroundImage)).toContain("portraits/atlas32.png");

    // The whole roster shares two sheet requests: there is no per-seat <img>.
    expect(h.tree.some((element) => element.type === "img")).toBe(false);
    expect(FakeAtlasImage.sources).toHaveLength(2);
    h.cleanup();
  });

  it("falls back to an initials chip when the pack has no cell for the seat", async () => {
    const h = await mountPanel(
      activeTheme(),
      oneDivision([seat({ slug: "grace", name: "Grace Hopper", portrait: null })]),
    );
    const initials = avatarFor(h, "grace", "initials");
    expect(initials).toBeDefined();
    expect(initials?.props.role).toBe("img");
    expect(initials?.props["aria-label"]).toBe("Grace Hopper initials");
    expect(initials?.props.children).toEqual(["GH"]);
    expect(avatarFor(h, "grace", "portrait")).toBeUndefined();
    h.cleanup();
  });

  it("keeps the initials visible and announced when the sheet fails to load", async () => {
    FakeAtlasImage.fail = true;
    const h = await mountPanel(
      activeTheme(),
      oneDivision([seat({ slug: "alan", name: "Alan Turing", portrait: [0, 0] })]),
    );
    const root = h.tree.find((e) => e.props["data-testid"] === "agents-panel");
    expect(root?.props.className).not.toContain("office-portraits-ready");
    expect(root?.props["data-portraits"]).toBe("fallback");

    const initials = avatarFor(h, "alan", "initials");
    expect(initials).toBeDefined();
    expect(initials?.props["aria-hidden"]).toBeUndefined();
    expect(initials?.props.role).toBe("img");
    expect(avatarFor(h, "alan", "portrait")).toBeUndefined();
    h.cleanup();
  });

  it("orders the fallback chain portrait -> initials -> emoji", async () => {
    const h = await mountPanel(
      activeTheme(),
      oneDivision([
        seat({ slug: "sprite", name: "Sprite Seat", portrait: [3, 2] }),
        seat({ slug: "initials", name: "Initials Seat", portrait: null }),
        // No letters and no cell: the emoji is the only truthful avatar left.
        seat({ slug: "emoji", name: "", emoji: "🙂", portrait: null }),
      ]),
    );
    expect(avatarFor(h, "sprite", "portrait")).toBeDefined();
    expect(avatarFor(h, "sprite", "initials")).toBeUndefined();
    expect(avatarFor(h, "sprite", "emoji")).toBeUndefined();

    expect(avatarFor(h, "initials", "portrait")).toBeUndefined();
    expect(avatarFor(h, "initials", "initials")).toBeDefined();
    expect(avatarFor(h, "initials", "emoji")).toBeUndefined();

    expect(avatarFor(h, "emoji", "portrait")).toBeUndefined();
    expect(avatarFor(h, "emoji", "initials")).toBeUndefined();
    const emoji = avatarFor(h, "emoji", "emoji");
    expect(emoji).toBeDefined();
    expect(emoji?.props.children).toEqual(["🙂"]);
    h.cleanup();
  });

  it("makes the card the only keyboard owner and follows hover/click", async () => {
    const h = await mountPanel(
      activeTheme(),
      oneDivision([
        seat({ slug: "first", name: "First Agent", body: [0, 0], portrait: [0, 0] }),
        seat({ slug: "second", name: "Second Agent", body: [1, 0], portrait: [1, 0] }),
      ]),
    );

    const card = () => byTestId(h, "agents-panel-card")[0];
    expect(card()?.props["data-seat-slug"]).toBe("first");
    expect(card()?.props["aria-live"]).toBe("polite");
    expect(card()?.props.role).toBe("group");
    expect(card()?.props["aria-label"]).toBe("Active agent");
    expect(card()?.props.tabIndex).toBe(0);

    let body = withClass(h, "agents-panel__body")[0];
    expect(body).toBeDefined();
    expect(body?.props.role).toBe("img");
    expect(body?.props["aria-label"]).toBe("First Agent body");
    let bodyStyle = body?.props.style as Record<string, unknown>;
    expect(bodyStyle["--body-x"]).toBe(0);
    expect(bodyStyle["--body-y"]).toBe(0);
    expect(String(bodyStyle.backgroundPosition)).toContain("32px");
    expect(String(bodyStyle.backgroundPosition)).toContain("64px");

    const row = (slug: string) => byTestId(h, "agents-panel-seat")
      .find((element) => element.props["data-seat-slug"] === slug);
    // Rows are NOT tab stops: the card is the only keyboard owner, so a row in
    // a collapsed division cannot trap or hide the roster.
    expect(row("first")?.props.tabIndex).toBeUndefined();
    expect(row("second")?.props.tabIndex).toBeUndefined();
    expect(row("first")?.props.id).toBe("agents-panel-seat-first");
    expect(row("first")?.props["aria-current"]).toBe("true");

    (row("second")?.props.onMouseEnter as () => void)();
    h.flush();
    expect(card()?.props["data-seat-slug"]).toBe("second");
    expect(row("second")?.props["aria-current"]).toBe("true");
    expect(row("first")?.props["aria-current"]).toBeUndefined();
    body = withClass(h, "agents-panel__body")[0];
    expect(body?.props["aria-label"]).toBe("Second Agent body");
    bodyStyle = body?.props.style as Record<string, unknown>;
    expect(bodyStyle["--body-x"]).toBe(1);

    (row("first")?.props.onClick as () => void)();
    h.flush();
    expect(card()?.props["data-seat-slug"]).toBe("first");

    // The card's own key handler drives the active seat too.
    const prevented = vi.fn();
    (card()?.props.onKeyDown as (event: unknown) => void)({
      key: "ArrowDown", preventDefault: prevented,
    });
    h.flush();
    expect(prevented).toHaveBeenCalledTimes(1);
    expect(card()?.props["data-seat-slug"]).toBe("second");
    h.cleanup();
  });

  it("opts the agent card into a declared card 9-slice", async () => {
    const themed = activeTheme({
      slices: { card: { src: "ui/card.png", slice: [8, 8, 8, 8],
                        width: "8px", repeat: "stretch" } },
    });
    const h = await mountPanel(
      themed,
      oneDivision([seat({ slug: "solo", name: "Solo Agent" })]),
    );
    const card = byTestId(h, "agents-panel-card")[0];
    expect(card?.props.className).toContain("office-theme-card");
    expect(card?.props["data-office-slice"]).toBe("card");
    h.cleanup();
  });

  it("opts the card tab into a declared tab 9-slice", async () => {
    const themed = activeTheme({
      slices: { tab: { src: "ui/tab.png", slice: [4, 4, 4, 4],
                       width: "4px", repeat: "stretch" } },
    });
    const h = await mountPanel(
      themed,
      oneDivision([seat({ slug: "solo", name: "Solo Agent" })]),
    );
    const tab = h.tree.find(
      (element) => element.props.className === "agents-panel__cardTab");
    expect(tab?.props["data-office-slice"]).toBe("tab");
    h.cleanup();
  });

  it("leaves the agent card slice-free when the pack declares none", async () => {
    const h = await mountPanel(
      activeTheme(),
      oneDivision([seat({ slug: "solo", name: "Solo Agent" })]),
    );
    const card = byTestId(h, "agents-panel-card")[0];
    expect(card?.props.className).toContain("office-theme-card");
    expect(card?.props["data-office-slice"]).toBeUndefined();
    h.cleanup();
  });

  it("renders the card portrait only (no body) without a theme", async () => {
    const h = await mountPanel(
      null,
      oneDivision([seat({ slug: "solo", name: "Solo Agent", body: [0, 0] })]),
    );
    const card = byTestId(h, "agents-panel-card")[0];
    expect(card?.props["data-seat-slug"]).toBe("solo");
    expect(withClass(h, "agents-panel__body")).toHaveLength(0);
    expect(avatarFor(h, "solo", "initials")).toBeDefined();
    h.cleanup();
  });
});

// ======================================================================
// The TALK action: one canonical conversation per roster seat
// ======================================================================

describe("the agents panel's Talk action", () => {
  const seats = [
    seat({ slug: "ada", name: "Ada Lovelace" }),
    seat({ slug: "grace", name: "Grace Hopper" }),
  ];

  it("offers Talk for the active seat on the keyboard-owned card", async () => {
    const onTalk = vi.fn();
    const h = await mountPanel(activeTheme(), oneDivision(seats), { onTalk });
    const talk = h.tree.find(
      (e) => e.props["data-testid"] === "agents-panel-talk");
    expect(talk).toBeDefined();
    expect(talk?.props["aria-label"]).toBe("Talk with Ada Lovelace");
    talk?.props.onClick();
    expect(onTalk).toHaveBeenCalledTimes(1);
    expect(onTalk.mock.calls[0][0].slug).toBe("ada");
    h.cleanup();
  });

  it("offers a mouse Talk shortcut per row without a second tab stop", async () => {
    const onTalk = vi.fn();
    const onOpenAgent = vi.fn();
    const h = await mountPanel(activeTheme(), oneDivision(seats), {
      onTalk, onOpenAgent,
    });
    const rowTalks = h.tree.filter(
      (e) => e.props["data-testid"] === "agents-panel-talk-seat");
    expect(rowTalks).toHaveLength(2);
    expect(rowTalks[1].props["data-seat-slug"]).toBe("grace");
    expect(rowTalks[1].props.tabIndex).toBe(-1);
    const stopPropagation = vi.fn();
    rowTalks[1].props.onClick({ stopPropagation });
    expect(stopPropagation).toHaveBeenCalledTimes(1);
    expect(onTalk).toHaveBeenCalledWith(expect.objectContaining({ slug: "grace" }));
    expect(onOpenAgent).not.toHaveBeenCalled();
    h.cleanup();
  });

  it("opts the card and tab into the pack's Modern UI frames", async () => {
    const themed = activeTheme({
      ui: { slices: {
        panel: {
          src: "/plugins/qubit-office/themes/demo/ui/panel.png",
          slice: [8, 8, 8, 8], width: "8px", repeat: "stretch",
        },
        button: {
          src: "/plugins/qubit-office/themes/demo/ui/button.png",
          slice: [8, 8, 8, 8], width: "6px", repeat: "stretch",
        },
      } },
    });
    const h = await mountPanel(themed, oneDivision([seats[0]]), {
      onTalk: vi.fn(),
    });
    const card = h.tree.find(
      (e) => e.props["data-testid"] === "agents-panel-card");
    expect(card?.props["data-office-ui-slice"]).toBe("card");
    const tab = h.tree.find(
      (e) => String(e.props.className).includes("agents-panel__cardTab"));
    expect(tab?.props["data-office-ui-slice"]).toBe("tab");
    h.cleanup();
  });
});
