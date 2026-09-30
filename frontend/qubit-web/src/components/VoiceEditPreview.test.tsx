// @vitest-environment jsdom
/** VoiceEditPreview — the before/after diff + action card. */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoiceEditPreview } from "./VoiceEditPreview";
import type { TransformPreview } from "@/lib/voiceEdit";

let container: HTMLDivElement;
let root: Root;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

function preview(over: Partial<TransformPreview> = {}): TransformPreview {
  return {
    ok: true,
    request_id: "r1",
    original: "hello world",
    proposed: "hello concise world",
    detected: { action: "shorten", style: "concise", language: "", audience: "" },
    model: "qubit-brain4",
    provider: "ollama",
    locality: "local",
    privacy_mode: "local",
    injection_suspected: false,
    facts_dropped: [],
    elapsed_ms: 120,
    target_app: "code",
    ...over,
  };
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
});

afterEach(() => {
  act(() => root?.unmount());
  container.remove();
});

describe("VoiceEditPreview", () => {
  it("renders original, proposed, detected command, model and privacy", async () => {
    await render(<VoiceEditPreview preview={preview()} />);
    expect(container.querySelector('[data-testid="voice-edit-original"]')?.textContent).toContain(
      "hello world",
    );
    const proposed = container.querySelector('[data-testid="voice-edit-proposed"]');
    expect(proposed?.textContent).toContain("concise");
    expect(container.querySelector('[data-testid="voice-edit-detected"]')?.textContent).toContain(
      "shorten",
    );
    expect(container.querySelector('[data-testid="voice-edit-model"]')?.textContent).toContain(
      "qubit-brain4",
    );
    expect(container.querySelector('[data-testid="voice-edit-privacy"]')?.textContent).toContain(
      "local",
    );
  });

  it("fires Apply, Copy, Retry, Cancel, Revert callbacks", async () => {
    const onApply = vi.fn();
    const onCopy = vi.fn();
    const onRetry = vi.fn();
    const onCancel = vi.fn();
    const onRevert = vi.fn();
    await render(
      <VoiceEditPreview
        preview={preview()}
        onApply={onApply}
        onCopy={onCopy}
        onRetry={onRetry}
        onCancel={onCancel}
        onRevert={onRevert}
      />,
    );
    const click = async (id: string) => {
      const el = container.querySelector(`[data-testid="${id}"]`) as HTMLButtonElement;
      await act(async () => el.click());
    };
    await click("voice-edit-apply");
    await click("voice-edit-copy");
    await click("voice-edit-retry");
    await click("voice-edit-cancel");
    await click("voice-edit-revert");
    expect(onApply).toHaveBeenCalledOnce();
    expect(onCopy).toHaveBeenCalledOnce();
    expect(onRetry).toHaveBeenCalledOnce();
    expect(onCancel).toHaveBeenCalledOnce();
    expect(onRevert).toHaveBeenCalledOnce();
  });

  it("edits the proposed text and reports the edit", async () => {
    const onEdit = vi.fn();
    await render(<VoiceEditPreview preview={preview()} onEdit={onEdit} />);
    const editBtn = container.querySelector(
      '[data-testid="voice-edit-edit"]',
    ) as HTMLButtonElement;
    await act(async () => editBtn.click());
    const box = container.querySelector(
      '[data-testid="voice-edit-edit-box"]',
    ) as HTMLTextAreaElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        "value",
      )?.set;
      setter?.call(box, "manually edited text");
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const save = Array.from(container.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("Save edit"),
    ) as HTMLButtonElement;
    await act(async () => save.click());
    expect(onEdit).toHaveBeenCalledWith("manually edited text");
  });

  it("warns about instruction-like text and dropped identifiers", async () => {
    await render(
      <VoiceEditPreview
        preview={preview({
          injection_suspected: true,
          facts_dropped: ["https://example.com", "30"],
        })}
      />,
    );
    expect(
      container.querySelector('[data-testid="voice-edit-injection-warning"]'),
    ).toBeTruthy();
    expect(container.querySelector('[data-testid="voice-edit-facts-warning"]')).toBeTruthy();
  });

  it("warns when the model returns a clarifying question", async () => {
    await render(
      <VoiceEditPreview preview={preview({ clarification_returned: true })} />,
    );
    expect(
      container.querySelector('[data-testid="voice-edit-clarification-warning"]'),
    ).toBeTruthy();
  });

  it("renders long text, code and markdown unchanged", async () => {
    const long = "line\n".repeat(200) + "`code` # heading";
    await render(<VoiceEditPreview preview={preview({ original: long, proposed: long })} />);
    expect(container.textContent).toContain("heading");
  });

  it("disables Apply while busy", async () => {
    await render(<VoiceEditPreview preview={preview()} busy />);
    const apply = container.querySelector(
      '[data-testid="voice-edit-apply"]',
    ) as HTMLButtonElement;
    expect(apply.disabled).toBe(true);
  });
});
