/* A tiny React recorder with real state/effect semantics for the Business
 * panels. Mirrors the established office test harness so a component can be
 * driven under plain node without jsdom. */
type RecordedElement = { type: unknown; props: Record<string, any> };

export function createHarness() {
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
    flush(max = 50) {
      let rounds = 0;
      while (dirty && rounds++ < max) {
        dirty = false;
        render();
      }
    },
    async settle() {
      for (let i = 0; i < 30; i += 1) {
        await Promise.resolve();
        await new Promise((resolve) => setTimeout(resolve, 0));
        this.flush();
      }
    },
    cleanup() {
      for (const fn of cleanups.splice(0)) fn?.();
    },
  };
}

export function byTestId(h: ReturnType<typeof createHarness>, id: string) {
  return h.tree.filter((el) => el.props["data-testid"] === id);
}

export function allByTestId(h: ReturnType<typeof createHarness>, id: string) {
  return h.tree.filter((el) => el.props["data-testid"] === id);
}

export function textOf(node: RecordedElement | undefined): string {
  if (!node) return "";
  const kids = node.props.children;
  const flat = (value: unknown): string => {
    if (value === null || value === undefined) return "";
    if (Array.isArray(value)) return value.map(flat).join("");
    if (typeof value === "object" && "props" in (value as Record<string, unknown>)) {
      return flat((value as RecordedElement).props.children);
    }
    return String(value);
  };
  return flat(kids);
}

export function respond(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  });
}

export type FetchCall = { url: string; method: string; body: any };

export function recordingFetch(
  handler: (call: FetchCall) => Response | Promise<Response>,
) {
  const calls: FetchCall[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const raw = typeof input === "string"
      ? input
      : input instanceof URL ? input.toString() : input.url;
    const request = new Request(new URL(raw, "http://localhost"), init);
    const body = request.method === "POST" && init?.body
      ? JSON.parse(String(init.body)) : null;
    const call = { url: request.url, method: request.method, body };
    calls.push(call);
    return handler(call);
  }) as unknown as typeof fetch;
  return { calls, impl };
}
