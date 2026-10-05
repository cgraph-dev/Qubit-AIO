// @vitest-environment jsdom
/**
 * Markdown — JSON tool-payload contract.
 *
 * Qubit Bot renders tool results as assistant bubbles. Those payloads arrive
 * as a single-line JSON string with escaped newlines (`{"output": "a\nb"}`),
 * which rendered as an unreadable prose wall (the live defect). They must
 * become a pretty-printed code block instead — without hijacking ordinary
 * prose that merely contains braces.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function render(content: string) {
  act(() => {
    root.render(<Markdown content={content} />);
  });
  return host;
}

describe("Markdown — JSON tool payloads", () => {
  it("renders a whole-line JSON object as a code block, not prose", () => {
    const payload = JSON.stringify({
      output: "Test Files 103 passed (103)\nTests 692 passed (692)",
      exit_code: 0,
      error: null,
    });
    const el = render(payload);

    const pre = el.querySelector("pre code");
    expect(pre).not.toBeNull();
    // Pretty-printed: the escaped \n became a real line break.
    expect(pre!.textContent).toContain('"output": "Test Files 103 passed (103)');
    expect(pre!.textContent).toContain("\n");
    expect(pre!.textContent).toContain('"exit_code": 0');
  });

  it("renders a whole-line JSON array as a code block", () => {
    const el = render(JSON.stringify([{ id: 1 }, { id: 2 }]));
    expect(el.querySelector("pre code")).not.toBeNull();
  });

  it("pretty-prints a json fence", () => {
    const inner = JSON.stringify({ a: 1, b: [2, 3] });
    const el = render("```json\n" + inner + "\n```");
    const pre = el.querySelector("pre code");
    expect(pre).not.toBeNull();
    expect(pre!.textContent).toContain('"a": 1');
  });

  it("leaves ordinary prose alone (no false positive on braces)", () => {
    const el = render("Use set {a, b} notation. This is not JSON.");
    expect(el.querySelector("pre")).toBeNull();
    expect(el.textContent).toContain("Use set {a, b} notation.");
  });

  it("leaves a brace-only non-JSON string as prose", () => {
    const el = render("{ not valid json }");
    expect(el.querySelector("pre")).toBeNull();
  });

  it("leaves a JSON scalar alone", () => {
    const el = render("42");
    expect(el.querySelector("pre")).toBeNull();
  });

  it("still renders headings, lists and inline code", () => {
    const el = render("## Title\n\n- one\n- two\n\nuse `code`");
    expect(el.querySelector("h2")?.textContent).toBe("Title");
    expect(el.querySelectorAll("li")).toHaveLength(2);
    expect(el.querySelector("p code")?.textContent).toBe("code");
  });
});
