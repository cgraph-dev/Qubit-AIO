// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { installOfficeUiSkin, officeUiSkinRules } from "./office-ui-skin";

const payload = {
  active: true,
  ui: {
    slices: {
      panel: { src: "/theme/ui/panel.png", slice: [24, 24, 24, 24], width: "10px" },
      button: { src: "/theme/ui/button.png", slice: [16, 16, 16, 16], width: "4px" },
    },
    icons: {
      agents: { src: "/theme/ui/icons/agents.png" },
      overview: { src: "/theme/ui/icons/map.png" },
    },
  },
};

describe("office UI skin", () => {
  it("builds scoped panel/button rules and refuses unsafe URLs", () => {
    const rules = officeUiSkinRules(payload);
    expect(rules.join("\n")).toContain(".ui-root .ui-panel");
    expect(rules.join("\n")).toContain("border-image-source:url(\"/theme/ui/panel.png\")");
    expect(rules.join("\n")).toContain(".ui-root .ui-btn");
    const unsafe = officeUiSkinRules({ active: true, ui: { slices: {
      panel: { src: "javascript:alert(1)", slice: [1, 1, 1, 1], width: "1px" },
    } } });
    expect(unsafe.join("\n")).not.toContain("javascript:");
  });

  it("skins existing and injected icon buttons, then restores them", () => {
    document.body.innerHTML = `
      <button id="agents" data-testid="office-menu-agents"><span><svg></svg></span></button>
      <button id="overview" title="Visão geral (O)"><svg></svg></button>`;
    const win = window as unknown as Window & { __QUBIT_OFFICE_THEME__: typeof payload };
    win.__QUBIT_OFFICE_THEME__ = payload;
    const undo = installOfficeUiSkin(win);
    const agents = document.getElementById("agents") as HTMLElement;
    expect(agents.style.backgroundImage).toContain("/theme/ui/icons/agents.png");
    expect(agents.querySelector("svg")?.style.display).toBe("none");
    expect((document.getElementById("overview") as HTMLElement).style.backgroundImage)
      .toContain("/theme/ui/icons/map.png");
    expect(document.getElementById("qubit-office-ui-skin")).not.toBeNull();
    undo();
    expect(agents.style.backgroundImage).toBe("");
    expect(document.getElementById("qubit-office-ui-skin")).toBeNull();
  });
});
