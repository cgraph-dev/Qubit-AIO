/** @vitest-environment jsdom */
import { afterEach, describe, expect, it } from "vitest";
import {
  applyOfficeUiSlices,
  officeUiSliceAttrs,
  officeUiSliceCss,
  officeUiSliceName,
  type OfficeTheme,
} from "./office-theme";

function theme(over: Partial<OfficeTheme> = {}): OfficeTheme {
  return {
    id: "limezu",
    label: "LimeZu",
    version: "1.0.0",
    credit: "LimeZu — https://limezu.itch.io",
    license: "LimeZu licence",
    active: true,
    assetBase: "/plugins/qubit-office/themes/limezu",
    atlas: null,
    index: null,
    bodies: { atlas: null, index: null, size: null, frame: null },
    tokens: {},
    slices: {},
    ui: {
      slices: {
        panel: {
          src: "/plugins/qubit-office/themes/limezu/ui/panel.png?v=1.0.0",
          slice: [8, 8, 8, 8], width: "8px", repeat: "stretch",
        },
        button: {
          src: "/plugins/qubit-office/themes/limezu/ui/button.png?v=1.0.0",
          slice: [8, 8, 8, 8], width: "6px", repeat: "stretch",
        },
        dialog: {
          src: "/plugins/qubit-office/themes/limezu/ui/dialog.png?v=1.0.0",
          slice: [8, 8, 8, 8], width: "8px", repeat: "stretch",
        },
      },
    },
    ...over,
  };
}

const classic = (): OfficeTheme => theme({
  id: "classic", label: "Classic", active: false, ui: { slices: {} },
});

afterEach(() => {
  document.head.innerHTML = "";
  document.documentElement.removeAttribute("data-office-theme");
});

describe("Modern UI slice resolution", () => {
  it("uses the declared role first", () => {
    const t = theme();
    expect(officeUiSliceName(t, "panel")).toBe("panel");
    expect(officeUiSliceName(t, "dock")).toBe("panel");
    expect(officeUiSliceName(t, "card")).toBe("panel");
    expect(officeUiSliceName(t, "dialog")).toBe("dialog");
    expect(officeUiSliceName(t, "button")).toBe("button");
  });

  it("falls back in the documented order for absent input/tab art", () => {
    const t = theme();
    // No input/tab slices in this pack: input -> dialog, tab -> button.
    expect(officeUiSliceName(t, "input")).toBe("dialog");
    expect(officeUiSliceName(t, "tab")).toBe("button");
  });

  it("prefers purpose-built input/tab art when a pack declares it", () => {
    const t = theme();
    t.ui = { slices: {
      ...(t.ui?.slices || {}),
      input: {
        src: "/plugins/qubit-office/themes/limezu/ui/input.png?v=1.0.0",
        slice: [4, 4, 4, 4], width: "4px", repeat: "stretch",
      },
      tab: {
        src: "/plugins/qubit-office/themes/limezu/ui/tab.png?v=1.0.0",
        slice: [4, 4, 4, 4], width: "4px", repeat: "stretch",
      },
    } };
    expect(officeUiSliceName(t, "input")).toBe("input");
    expect(officeUiSliceName(t, "tab")).toBe("tab");
  });

  it("is absent for classic and for a payload with no ui block", () => {
    expect(officeUiSliceName(classic(), "dock")).toBeUndefined();
    expect(officeUiSliceName(theme({ ui: undefined }), "dock")).toBeUndefined();
  });

  it("emits attributes only when a slice resolves", () => {
    expect(officeUiSliceAttrs(theme(), "button", true)).toEqual({
      "data-office-ui-slice": "button",
      "data-office-ui-filled": "true",
    });
    expect(officeUiSliceAttrs(theme(), "panel", true)).toEqual({
      "data-office-ui-slice": "panel",
      "data-office-ui-filled": "true",
    });
    expect(officeUiSliceAttrs(classic(), "button", true)).toEqual({});
  });
});

describe("Modern UI slice CSS", () => {
  it("scopes real border-image rules to the office root", () => {
    const css = officeUiSliceCss(theme());
    expect(css).toContain(
      '.office-game[data-office-theme="limezu"] [data-office-ui-slice="dock"]');
    expect(css).toContain(
      'border-image-source: url("/plugins/qubit-office/themes/limezu/ui/panel.png?v=1.0.0");');
    expect(css).toContain("border-image-slice: 8 8 8 8;");
    expect(css).toContain("border-width: 8px;");
    expect(css).toContain("image-rendering: pixelated;");
    expect(css).toContain(
      '[data-office-ui-slice="dialog"][data-office-ui-filled="true"]');
    expect(css).toContain("border-image-slice: 8 8 8 8 fill;");
    expect(css).toContain("color: #2a1a12;");
  });

  it("uses the button frame for the button role and the tab fallback", () => {
    const css = officeUiSliceCss(theme());
    expect(css).toContain(
      '[data-office-ui-slice="button"] {\n'
      + '  border-image-source: url("/plugins/qubit-office/themes/limezu/ui/button.png?v=1.0.0");');
    expect(css).toContain(
      '[data-office-ui-slice="tab"] {\n'
      + '  border-image-source: url("/plugins/qubit-office/themes/limezu/ui/button.png?v=1.0.0");');
  });

  it("emits nothing for classic or an empty ui block", () => {
    expect(officeUiSliceCss(classic())).toBe("");
    expect(officeUiSliceCss(theme({ ui: { slices: {} } }))).toBe("");
  });

  it("drops a malformed descriptor instead of emitting unsafe CSS", () => {
    const broken = theme();
    broken.ui = { slices: {
      panel: {
        src: "/plugins/qubit-office/themes/limezu/ui/panel.png",
        slice: [8, 8] as never, width: "8px", repeat: "stretch",
      },
      button: {
        src: "javascript:alert(1)",
        slice: [8, 8, 8, 8], width: "8px", repeat: "stretch",
      },
    } };
    const css = officeUiSliceCss(broken);
    expect(css).toBe("");
  });
});

describe("applying the Modern UI stylesheet", () => {
  it("installs exactly one scoped style and removes it on dispose", () => {
    const dispose = applyOfficeUiSlices(document, theme());
    const styles = document.head.querySelectorAll("style[data-office-ui-slices]");
    expect(styles).toHaveLength(1);
    expect(styles[0].getAttribute("data-office-ui-slices")).toBe("limezu");
    expect(styles[0].textContent).toContain("[data-office-ui-slice=\"dock\"]");
    dispose();
    expect(document.head.querySelectorAll("style[data-office-ui-slices]"))
      .toHaveLength(0);
  });

  it("installs nothing for classic and is idempotent across applications", () => {
    const first = applyOfficeUiSlices(document, theme());
    const second = applyOfficeUiSlices(document, theme());
    expect(document.head.querySelectorAll("style[data-office-ui-slices]"))
      .toHaveLength(1);
    second();
    first();
    expect(document.head.querySelectorAll("style[data-office-ui-slices]"))
      .toHaveLength(0);
    applyOfficeUiSlices(document, classic());
    expect(document.head.querySelectorAll("style[data-office-ui-slices]"))
      .toHaveLength(0);
  });
});
