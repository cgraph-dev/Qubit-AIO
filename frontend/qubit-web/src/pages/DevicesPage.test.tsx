// @vitest-environment jsdom
/**
 * /devices — setup status, setup-code mint form, pairings incl. direct-message
 * threads, confirm-gated approve/reject/remove, confirm-gated token
 * rotate/revoke, enroll, and the identity card. Secret values are never
 * displayed, even when the payload carries them; unmanageable rows assert
 * disabled-with-reason, never firing.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const PAIRINGS = {
  pairings: [
    {
      id: "pair-1",
      label: "owner phone",
      kind: "dm",
      state: "pending",
      manageable: true,
      secret_set: true,
    },
    {
      id: "pair-2",
      label: "desk display",
      kind: "companion",
      state: "active",
      manageable: false,
      manage_reason: "Token operations are owned by the device holder.",
      secret_set: false,
    },
  ],
};

function jsonResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

function stagedResponse(detail: string) {
  const body = {
    state: "NEEDS_CONFIRMATION",
    code: "CONFIRM_REQUIRED",
    detail,
    confirmation_id: "cid-9",
  };
  return {
    ok: false,
    status: 409,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

const postCalls: string[] = [];
function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.includes("/devices/setup-status")) {
        return jsonResponse({ enabled: true, pending_codes: 1, paired_devices: 1 });
      }
      if (u.includes("/devices/pairings")) {
        return jsonResponse(PAIRINGS);
      }
      if (u.includes("/devices/identity")) {
        return jsonResponse({
          identity: { device_id: "pair-1", label: "owner phone", scopes: ["chat"] },
        });
      }
      if ((init?.method ?? "GET") === "POST") {
        postCalls.push(u);
        if (u.includes("confirmation_id=cid-9")) {
          if (u.includes("setup-code-mint")) {
            // Even when the backend echoes a code value, the page must not
            // render it: only metadata plus the presence flag are shown.
            return jsonResponse({
              id: "code-1",
              label: "owner phone",
              scopes: ["pair"],
              code_present: true,
              code: "SECRET-SETUP-CODE-123",
            });
          }
          return jsonResponse({ ok: true, id: "pair-1", state: "approved" });
        }
        return stagedResponse("Managing this device needs confirmation.");
      }
      return jsonResponse({});
    }),
  );
}

async function waitFor(cond: () => boolean, timeoutMs = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) {
      throw new Error("waitFor: condition never became true");
    }
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
}

async function renderPage(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

async function renderDevicesPage() {
  const { default: DevicesPage } = await import("./DevicesPage");
  await renderPage(<DevicesPage />);
  await waitFor(() => Boolean(container.querySelector('[aria-label="Device pairings"]')));
}

function dialogButton(text: string): HTMLButtonElement | null {
  return (
    [...document.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === text,
    ) ?? null
  );
}

function setInput(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )!.set!;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  postCalls.length = 0;
  stubFetch();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("DevicesPage status + pairings", () => {
  it("renders setup status and the pairing roster incl. direct-message threads", async () => {
    await renderDevicesPage();
    expect(container.textContent).toContain("1 pending codes");
    expect(container.textContent).toContain("owner phone");
    expect(container.textContent).toContain("channel: dm");
    expect(container.textContent).toContain("secret: stored");
  });

  it("renders unmanageable rows disabled with their reason and never fires", async () => {
    await renderDevicesPage();
    const refused = container.querySelector('[data-state="refused"]');
    expect(refused).not.toBeNull();
    expect(refused?.getAttribute("aria-disabled")).toBe("true");
    expect(refused?.getAttribute("title")).toContain("device holder");
    const before = postCalls.length;
    await act(async () => {
      (refused as HTMLButtonElement).click();
    });
    expect(postCalls.length).toBe(before);
  });

  it("shows the identity card for a pairing", async () => {
    await renderDevicesPage();
    const identity = container.querySelector(
      '[aria-label="Show identity for pair-1"]',
    ) as HTMLButtonElement;
    await act(async () => identity.click());
    await waitFor(() => (container.textContent ?? "").includes("scopes: chat"));
  });
});

describe("DevicesPage confirm-gated approve (staged-then-settle)", () => {
  it("stages on first POST, then settles after Confirm", async () => {
    await renderDevicesPage();
    const approve = container.querySelector(
      '[aria-label="Approve pairing pair-1"]',
    ) as HTMLButtonElement;
    await act(async () => approve.click());
    await waitFor(() => Boolean(dialogButton("Confirm")));
    expect(postCalls.length).toBe(1);
    await act(async () => dialogButton("Confirm")!.click());
    await waitFor(() => (container.textContent ?? "").includes("Approve completed."));
    expect(postCalls.length).toBe(2);
  });
});

describe("DevicesPage secrets", () => {
  it("mints a setup code without ever displaying the secret value", async () => {
    await renderDevicesPage();
    await act(async () => {
      setInput(container.querySelector('[aria-label="Setup code label"]') as HTMLInputElement, "owner phone");
      setInput(
        container.querySelector('[aria-label="Setup code scopes"]') as HTMLInputElement,
        "pair",
      );
    });
    const mint = container.querySelector(
      '[aria-label="Mint setup code"]',
    ) as HTMLButtonElement;
    await act(async () => mint.click());
    await waitFor(() => Boolean(dialogButton("Confirm")));
    await act(async () => dialogButton("Confirm")!.click());
    await waitFor(() => (container.textContent ?? "").includes("Setup code minted."));
    expect(container.textContent).toContain("code stored");
    expect(container.textContent).not.toContain("SECRET-SETUP-CODE-123");
  });
});
