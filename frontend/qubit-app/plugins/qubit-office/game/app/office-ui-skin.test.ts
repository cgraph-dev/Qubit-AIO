// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { installOfficeUiSkin, officeUiSkinRules } from "./office-ui-skin";

const CLASSIC = { active: false, ui: { slices: {} } };
const ACTIVE = {
  active: true,
  ui: {
    slices: {
      panel: { src: "/theme/ui/panel.png", slice: [16, 16, 16, 16] },
      button: { src: "/theme/ui/button.png", slice: [8, 8, 8, 8] },
    },
  },
};

function css(payload?: unknown): string {
  return officeUiSkinRules(payload as never).join("\n");
}

function blocks(source: string): Array<{ selector: string; body: string }> {
  const out: Array<{ selector: string; body: string }> = [];
  for (const match of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    out.push({ selector: match[1].trim(), body: match[2] });
  }
  return out;
}

describe("office UI skin (2Five)", () => {
  it("injects the baseline in classic (no bail on payload.active)", () => {
    const rules = officeUiSkinRules(CLASSIC);
    expect(rules.length).toBeGreaterThan(0);
    expect(rules.join("\n")).toContain("html body .ui-root");
    expect(rules.join("\n")).toContain(".ui-topbar");
    // Absent payload behaves identically: the skin is the Qubit chrome.
    expect(officeUiSkinRules(null).join("\n")).toBe(css(CLASSIC));
  });

  it("scopes every selector to the iframe document", () => {
    const source = css(CLASSIC);
    expect(source).not.toMatch(/(^|\n)\s*\.ui-/);
    expect(source).not.toMatch(/\}\s*\.ui-/);
    // No cross-document fragment reference; Stage 4 geometry is a data URI.
    expect(source).not.toContain('url("#');
    expect(source).not.toContain("url(#");
    expect(source).toContain("data:image/svg+xml");
  });

  it("never styles controls and never references Sakuga", () => {
    const source = css(CLASSIC).toLowerCase();
    for (const forbidden of [
      ".ui-switch", "ui-switch__knob", "input[type=\"checkbox\"]",
      "input[type=checkbox]", ".ui-perm__check", "sakuga-", ".qsk-", "--sk-",
    ]) {
      expect(source).not.toContain(forbidden);
    }
  });

  it("adds zero transition/animation/transform motion to controls", () => {
    const source = css(CLASSIC);
    // No keyframes and no animated/transitioned declarations at all: the only
    // allowed motion values are the `none` resets.
    expect(source).not.toContain("@keyframes");
    for (const { body } of blocks(source)) {
      for (const decl of body.split(";")) {
        const [prop, value] = decl.split(":").map((part) => part.trim());
        if (prop === "transition" || prop === "animation") {
          expect(value, `${prop} must be none`).toBe("none");
        }
      }
    }
    // Buttons get a motion reset and NOT a paint rule.
    // `.ui-btn-group` is a container, not a button control.
    const buttonBlocks = blocks(source).filter(({ selector }) =>
      /\.ui-btn(?![\w-])|\.ui-icon-btn(?![\w-])/.test(selector));
    expect(buttonBlocks.length).toBeGreaterThan(0);
    for (const { body } of buttonBlocks) {
      const props = body.split(";").map((d) => d.split(":")[0].trim())
        .filter(Boolean);
      expect(new Set(props)).toEqual(new Set(["transition", "animation"]));
    }
  });

  it("stops the press transforms the vendored controls declare", () => {
    const source = css(CLASSIC);
    expect(source).toMatch(/\.ui-counter:active[^{]*\{[^}]*transform:\s*none/);
    expect(source).toMatch(/\.ui-deliv-card:active[^{]*\{[^}]*transform:\s*none/);
  });

  it("lets an active theme add a panel slice but never a button slice", () => {
    const source = css(ACTIVE);
    expect(source).toContain("border-image-source:url(\"/theme/ui/panel.png\")");
    expect(source).not.toContain("/theme/ui/button.png");
    // Unsafe slice URLs are refused.
    const unsafe = css({ active: true, ui: { slices: {
      panel: { src: "javascript:alert(1)", slice: [1, 1, 1, 1] },
    } } });
    expect(unsafe).not.toContain("javascript:");
  });

  it("installs, keeps itself after late stylesheets, and cleans up", async () => {
    document.head.innerHTML = "";
    const win = window as unknown as Window & {
      __QUBIT_OFFICE_THEME__: typeof CLASSIC;
    };
    win.__QUBIT_OFFICE_THEME__ = CLASSIC;
    const undo = installOfficeUiSkin(win);
    const style = document.getElementById("qubit-office-ui-skin");
    expect(style).not.toBeNull();
    expect(style?.textContent).toContain("html body .ui-root");

    // A vendored stylesheet appended after load must not outrank the skin:
    // the head observer re-appends it as the last child.
    const late = document.createElement("link");
    late.rel = "stylesheet";
    late.href = "/plugins/qubit-office/habblaud/bundle/app.css";
    document.head.appendChild(late);
    await new Promise((resolve) => setTimeout(resolve, 0));
    const children = Array.from(document.head.children);
    expect(children[children.length - 1]?.id).toBe("qubit-office-ui-skin");

    undo();
    expect(document.getElementById("qubit-office-ui-skin")).toBeNull();
  });
});
