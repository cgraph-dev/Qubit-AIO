// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import { AutoField } from "./AutoField";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("AutoField nested scalar persistence", () => {
  it("serializes numeric and boolean edits without converting them to strings, and reloads them", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    let record = { retries: 3, enabled: true, name: "007" };
    const render = async () => {
      await act(async () => root.render(
        <AutoField schemaKey="agent.options" schema={{}} value={record} onChange={(value) => { record = value as typeof record; }} />,
      ));
    };
    try {
      await render();
      const number = host.querySelector<HTMLInputElement>('input[type="number"]')!;
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(number, "7");
        number.dispatchEvent(new Event("input", { bubbles: true }));
      });
      expect(record.retries).toBe(7);
      await render();
      await act(async () => host.querySelector<HTMLButtonElement>('[role="switch"]')!.click());
      expect(record.enabled).toBe(false);
      // This is the actual JSON request/response boundary used by config PUT/GET.
      record = JSON.parse(JSON.stringify(record));
      expect(record).toEqual({ retries: 7, enabled: false, name: "007" });
      await render();
      expect(host.querySelector<HTMLInputElement>('input[type="number"]')!.value).toBe("7");
      expect(host.querySelector('[role="switch"]')!.getAttribute("aria-checked")).toBe("false");
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });
});
