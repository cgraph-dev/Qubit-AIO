/** @vitest-environment jsdom */
import * as React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createTalkPanel } from "./TalkPanel";
import type { AgentSeat } from "./agents-panel";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const SEAT: AgentSeat = {
  slug: "lead-quality",
  name: "Lead Quality",
  seat_type: "worker",
  supervisor: "chief-of-staff",
  division: "quality",
  room: "quality-desk-a",
  emoji: "🔍",
  color: "#111",
  portrait: null,
  body: null,
};

const respond = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  });

const TOKEN_KEY = "__QUBIT_SESSION_TOKEN__" as const;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (window as unknown as Record<string, unknown>)[TOKEN_KEY] = "tok";
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => { root.unmount(); });
  container.remove();
  delete (window as unknown as Record<string, unknown>)[TOKEN_KEY];
});

const TalkPanel = createTalkPanel(React as never, React as never);

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function render(fetchImpl: typeof fetch, onClose = vi.fn()) {
  await act(async () => {
    root.render(React.createElement(TalkPanel as never, {
      seat: SEAT, base: "", fetchImpl, onClose,
    }));
  });
  await flush();
  return onClose;
}

function q<T extends Element>(selector: string): T {
  const node = container.querySelector<T>(selector);
  if (!node) throw new Error(`missing ${selector}`);
  return node;
}

function qa<T extends Element>(selector: string): T[] {
  return Array.from(container.querySelectorAll<T>(selector));
}

describe("TalkPanel empty and real transcript states", () => {
  it("shows the typed empty state when no canonical session exists", async () => {
    const f = vi.fn(async () => respond(404, { ok: false }));
    await render(f as unknown as typeof fetch);
    expect(q("[data-testid='talk-panel']").getAttribute("data-state"))
      .toBe("empty");
    expect(q("[data-testid='talk-empty']").textContent)
      .toContain("no conversation in the canonical chat store");
    expect(q("[data-testid='talk-start']")).toBeDefined();
    expect(qa("[data-testid='talk-message']")).toHaveLength(0);
    const input = q<HTMLTextAreaElement>("[data-testid='talk-input']");
    expect(input.disabled).toBe(true);
    expect(q<HTMLButtonElement>("[data-testid='talk-send']").disabled)
      .toBe(true);
    expect(String(f.mock.calls[0][0])).toContain(
      "/agent/api/qubit/chat/sessions/office-talk-lead-quality");
  });

  it("renders the real transcript rows and the seat identity", async () => {
    const messages = [
      { role: "user", content: "Audit the last deploy." },
      { role: "tool", content: "internal tool row" },
      { role: "assistant", content: "The deploy is green." },
    ];
    const f = vi.fn(async () => respond(200, {
      session: { id: "office-talk-lead-quality", name: "Office talk · Lead Quality" },
      messages,
    }));
    await render(f as unknown as typeof fetch);
    expect(q("[data-testid='talk-panel']").getAttribute("data-state"))
      .toBe("ready");
    expect(q("[data-testid='talk-name']").textContent).toBe("Lead Quality");
    expect(q("[data-testid='talk-role']").textContent).toContain("Worker");
    expect(q("[data-testid='talk-role']").textContent).toContain("quality-desk-a");
    const rows = qa("[data-testid='talk-message']");
    expect(rows).toHaveLength(2);
    expect(rows[0].getAttribute("data-role")).toBe("user");
    expect(rows[0].textContent).toContain("Audit the last deploy.");
    expect(rows[1].getAttribute("data-role")).toBe("assistant");
    expect(rows[1].textContent).toContain("The deploy is green.");
    // The tool row is not presented as a conversation message.
    expect(container.textContent).not.toContain("internal tool row");
    expect(q<HTMLTextAreaElement>("[data-testid='talk-input']").disabled)
      .toBe(false);
  });

  it("shows a typed empty transcript for a live session with no exchange", async () => {
    const f = vi.fn(async () => respond(200, {
      session: { id: "office-talk-lead-quality" },
      messages: [{ role: "tool", content: "only a tool row" }],
    }));
    await render(f as unknown as typeof fetch);
    expect(q("[data-testid='talk-empty-transcript']")).toBeDefined();
    expect(qa("[data-testid='talk-message']")).toHaveLength(0);
    expect(q<HTMLTextAreaElement>("[data-testid='talk-input']").disabled)
      .toBe(false);
  });

  it("is a typed unavailable state when the token is refused", async () => {
    const f = vi.fn(async () => respond(401, {}));
    await render(f as unknown as typeof fetch);
    expect(q("[data-testid='talk-panel']").getAttribute("data-state"))
      .toBe("unavailable");
    expect(q("[data-testid='talk-unavailable']").textContent)
      .toContain("HTTP 401");
    expect(q("[data-testid='talk-composer-note']").textContent)
      .toContain("Composer disabled: TOKEN_REFUSED");
    expect(q<HTMLTextAreaElement>("[data-testid='talk-input']").disabled)
      .toBe(true);
    expect(q<HTMLButtonElement>("[data-testid='talk-send']").disabled)
      .toBe(true);
  });

  it("does not call the store when the page has no session token", async () => {
    delete (window as unknown as Record<string, unknown>)[TOKEN_KEY];
    const f = vi.fn(async () => respond(200, {}));
    await render(f as unknown as typeof fetch);
    expect(f).not.toHaveBeenCalled();
    expect(q("[data-testid='talk-panel']").getAttribute("data-code"))
      .toBe("NO_SESSION_TOKEN");
    expect(q<HTMLTextAreaElement>("[data-testid='talk-input']").disabled)
      .toBe(true);
  });
});

describe("TalkPanel send seam", () => {
  it("sends the exact turn and renders the reloaded canonical transcript", async () => {
    let messages = [
      { role: "user", content: "Audit the last deploy." },
      { role: "assistant", content: "The deploy is green." },
    ];
    const posts: Array<{ url: string; body: unknown }> = [];
    const f = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        posts.push({ url: String(url), body: JSON.parse(String(init.body)) });
        messages = [
          ...messages,
          { role: "user", content: "And the tests?" },
          { role: "assistant", content: "All 12 passed." },
        ];
        return respond(200, { ok: true, reply: "All 12 passed." });
      }
      return respond(200, {
        session: { id: "office-talk-lead-quality" }, messages,
      });
    });
    await render(f as unknown as typeof fetch);
    const input = q<HTMLTextAreaElement>("[data-testid='talk-input']");
    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype, "value")?.set;
    await act(async () => {
      setter?.call(input, "And the tests?");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(q<HTMLButtonElement>("[data-testid='talk-send']").disabled)
      .toBe(false);
    await act(async () => {
      q<HTMLButtonElement>("[data-testid='talk-send']").click();
    });
    await flush();
    expect(posts).toHaveLength(1);
    expect(posts[0].url).toBe("/agent/api/qubit/chat");
    expect(posts[0].body).toEqual({
      text: "And the tests?", session: "office-talk-lead-quality",
    });
    const rows = qa("[data-testid='talk-message']");
    expect(rows).toHaveLength(4);
    expect(rows[3].textContent).toContain("All 12 passed.");
    expect(q<HTMLTextAreaElement>("[data-testid='talk-input']").value)
      .toBe("");
  });

  it("surfaces a typed refusal and does not fabricate a reply", async () => {
    const messages = [
      { role: "user", content: "Audit the last deploy." },
      { role: "assistant", content: "The deploy is green." },
    ];
    const f = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return respond(502, {
          ok: false, code: "MODEL_NOT_ALLOWED",
          detail: "the active profile refuses this model",
        });
      }
      return respond(200, {
        session: { id: "office-talk-lead-quality" }, messages,
      });
    });
    await render(f as unknown as typeof fetch);
    const input = q<HTMLTextAreaElement>("[data-testid='talk-input']");
    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype, "value")?.set;
    await act(async () => {
      setter?.call(input, "Do the thing");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      q<HTMLButtonElement>("[data-testid='talk-send']").click();
    });
    await flush();
    expect(q("[data-testid='talk-error']").textContent)
      .toContain("[MODEL_NOT_ALLOWED]");
    expect(qa("[data-testid='talk-message']")).toHaveLength(2);
    expect(container.textContent).not.toContain("All 12 passed.");
  });

  it("creates the conversation before enabling the composer", async () => {
    const calls: string[] = [];
    let created = false;
    const f = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method || "GET"} ${url}`);
      if (init?.method === "POST") {
        created = true;
        return respond(200, { ok: true, session: { id: "office-talk-lead-quality" } });
      }
      if (!created) return respond(404, { ok: false });
      return respond(200, {
        session: { id: "office-talk-lead-quality" },
        messages: [{ role: "user", content: "First real turn." }],
      });
    });
    await render(f as unknown as typeof fetch);
    await act(async () => {
      q<HTMLButtonElement>("[data-testid='talk-start']").click();
    });
    await flush();
    expect(calls).toContain("POST /agent/api/qubit/chat/sessions");
    expect(q<HTMLTextAreaElement>("[data-testid='talk-input']").disabled)
      .toBe(false);
    expect(qa("[data-testid='talk-message']")).toHaveLength(1);
  });
});

describe("TalkPanel accessibility and close behaviour", () => {
  it("is labelled, closable, and closes on Escape", async () => {
    const f = vi.fn(async () => respond(200, {
      session: { id: "office-talk-lead-quality" },
      messages: [{ role: "user", content: "hello" }],
    }));
    const onClose = await render(f as unknown as typeof fetch);
    const panel = q("[data-testid='talk-panel']");
    expect(panel.getAttribute("aria-label")).toBe("Talk with Lead Quality");
    expect(q("[data-testid='talk-transcript']").getAttribute("aria-label"))
      .toBe("Conversation with Lead Quality");
    await act(async () => {
      q<HTMLButtonElement>("[data-testid='talk-close']").click();
    });
    expect(onClose).toHaveBeenCalledTimes(1);
    await act(async () => {
      panel.dispatchEvent(new KeyboardEvent("keydown", {
        key: "Escape", bubbles: true,
      }));
    });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("keeps the Modern UI slice attributes only when the pack declares them", async () => {
    const f = vi.fn(async () => respond(200, {
      session: { id: "office-talk-lead-quality" }, messages: [],
    }));
    const theme = {
      id: "limezu", label: "LimeZu", version: "1.0.0",
      credit: "", license: "", active: true, assetBase: "",
      atlas: null, index: null,
      bodies: { atlas: null, index: null, size: null, frame: null },
      tokens: {}, slices: {},
      ui: { slices: {
        panel: { src: "/panel.png", slice: [8, 8, 8, 8], width: "8px", repeat: "stretch" },
        button: { src: "/button.png", slice: [8, 8, 8, 8], width: "6px", repeat: "stretch" },
        dialog: { src: "/dialog.png", slice: [8, 8, 8, 8], width: "8px", repeat: "stretch" },
      } },
    } as never;
    await act(async () => {
      root.render(React.createElement(TalkPanel as never, {
        seat: SEAT, base: "", fetchImpl: f as unknown as typeof fetch,
        onClose: vi.fn(), theme,
      }));
    });
    await flush();
    const input = q<HTMLTextAreaElement>("[data-testid='talk-input']");
    expect(input.getAttribute("data-office-ui-slice")).toBe("panel");
    expect(input.getAttribute("data-office-ui-filled")).toBe("true");
    expect(q("[data-testid='talk-send']").getAttribute("data-office-ui-slice"))
      .toBe("panel");
    expect(q("[data-testid='talk-close']").getAttribute("data-office-ui-slice"))
      .toBe("panel");
  });
});
