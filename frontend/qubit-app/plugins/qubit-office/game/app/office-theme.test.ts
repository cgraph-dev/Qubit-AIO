/** @vitest-environment jsdom */
import { afterEach, describe, expect, it } from "vitest";
import {
  applyOfficeUiSlices,
  officeUiEffect,
  officeUiFont,
  officeUiFrame,
  officeUiIcon,
  officeUiOrnament,
  officeUiSliceAttrs,
  officeUiSliceCss,
  officeUiSliceInk,
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

// ======================================================================
// Licensed icons / font / effects (additive UI pack blocks)
// ======================================================================

function licensedTheme(over: Partial<OfficeTheme> = {}): OfficeTheme {
  const base = theme();
  return {
    ...base,
    ui: {
      slices: base.ui?.slices,
      icons: {
        talk: {
          src: "/plugins/qubit-office/themes/limezu/ui/packs/icons/talk.png",
          width: 32, height: 32, pack: "medieval",
          credit: "Raven", license: "test", licenseOk: true,
        },
        blocked: {
          src: "/plugins/qubit-office/themes/limezu/ui/packs/icons/blocked.png",
          width: 32, height: 32, license: "test", licenseOk: false,
        },
      },
      frames: {
        avatar: {
          src: "/plugins/qubit-office/themes/limezu/ui/packs/frames/avatar.png",
          width: 80, height: 80, license: "test", licenseOk: true,
        },
      },
      ornament: {
        src: "/plugins/qubit-office/themes/limezu/ui/packs/ornament.png",
        width: 192, height: 64, license: "test", licenseOk: true,
      },
      font: {
        src: "/plugins/qubit-office/themes/limezu/ui/packs/fonts/ui-font.png",
        cell: 32, cols: 8, rows: 8, glyphs: "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
        pack: "fonts", license: "test", licenseOk: true,
      },
      effects: {
        success: {
          src: "/plugins/qubit-office/themes/limezu/ui/packs/effects/success.png",
          frames: 60, fps: 15, width: 40, height: 40,
          framesPerRow: 60, license: "test", licenseOk: true,
        },
        blocked: {
          src: "/plugins/qubit-office/themes/limezu/ui/packs/effects/blocked.png",
          frames: 10, fps: 10, width: 16, height: 16, licenseOk: false,
        },
      },
      packs: [{
        id: "darkages", label: "Dark Ages", author: "Hypnobius",
        credit: "Dark Ages — Hypnobius", license: "test", licenseOk: true,
      }],
    },
    ...over,
  };
}

describe("licensed UI assets", () => {
  it("selects an allowed icon and drops a blocked/missing one", () => {
    const t = licensedTheme();
    expect(officeUiIcon(t, "talk")?.src).toContain("icons/talk.png");
    expect(officeUiIcon(t, "blocked")).toBeUndefined();
    expect(officeUiIcon(t, "missing")).toBeUndefined();
    expect(officeUiIcon(classic(), "talk")).toBeUndefined();
  });

  it("selects frames/ornament/font/effect only when allowed", () => {
    const t = licensedTheme();
    expect(officeUiFrame(t, "avatar")?.width).toBe(80);
    expect(officeUiOrnament(t)?.width).toBe(192);
    expect(officeUiFont(t)?.cell).toBe(32);
    expect(officeUiEffect(t, "success")?.frames).toBe(60);
    expect(officeUiEffect(t, "blocked")).toBeUndefined();
    expect(officeUiEffect(t, "missing")).toBeUndefined();
    const blocked = licensedTheme();
    if (blocked.ui) {
      blocked.ui.font = { ...blocked.ui.font!, licenseOk: false };
      blocked.ui.ornament = { ...blocked.ui.ornament!, licenseOk: false };
    }
    expect(officeUiFont(blocked)).toBeUndefined();
    expect(officeUiOrnament(blocked)).toBeUndefined();
  });

  it("emits slice selectors for the new roles and the ink-aware colour", () => {
    const t = licensedTheme();
    const css = officeUiSliceCss(t);
    expect(css).toContain(
      '[data-office-ui-slice="button-secondary"]');
    expect(css).toContain('[data-office-ui-slice="icon-button"]');
    expect(css).toContain('[data-office-ui-slice="hud"]');
    expect(officeUiSliceAttrs(t, "button-secondary", true))
      .toMatchObject({
        "data-office-ui-slice": "button-secondary",
        "data-office-ui-filled": "true",
      });
  });

  it("uses the declared light ink for dark slice centres", () => {
    const t = licensedTheme();
    const dialog = t.ui?.slices?.dialog;
    if (!dialog) throw new Error("fixture missing dialog");
    dialog.ink = "light";
    const css = officeUiSliceCss(t);
    expect(css).toContain("color: #f6ecdd;");
    expect(officeUiSliceInk(t, "dialog")).toBe("light");
    expect(officeUiSliceAttrs(t, "dialog", true)["data-office-ui-ink"])
      .toBe("light");
  });

  it("defaults to dark ink for a legacy payload without ink metadata", () => {
    const t = theme();
    expect(officeUiSliceInk(t, "dialog")).toBe("dark");
  });
});
