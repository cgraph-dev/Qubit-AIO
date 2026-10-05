/**
 * Smoke test for the plugin SDK surface additions.
 *
 * Verifies that `exposePluginSDK()` writes the new dialog/toast primitives to
 * `window.__QUBIT_PLUGIN_SDK__` (with the legacy
 * `window.__QUBIT_PLUGIN_SDK_LEGACY__` alias assigned the same object for
 * plugins built against the old globals). Each new key is
 * checked individually so a regression in one helper doesn't mask the others.
 *
 * Companion to the additive PR "feat(plugins): expose Dialog/ConfirmDialog/
 * Toast/useToast/useConfirmDelete on the plugin SDK". See issue #50547.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { exposePluginSDK } from "./registry";

type SdkWindow = {
  __QUBIT_PLUGINS__?: unknown;
  __QUBIT_PLUGIN_SDK__?: {
    components: Record<string, unknown>;
    hooks: Record<string, unknown>;
  };
  __QUBIT_PLUGINS_LEGACY__?: unknown;
  __QUBIT_PLUGIN_SDK_LEGACY__?: {
    components: Record<string, unknown>;
    hooks: Record<string, unknown>;
  };
};

describe("plugin SDK dialog/toast surface", () => {
  beforeEach(() => {
    // Reset window between tests so exposePluginSDK() writes fresh.
    (globalThis as unknown as { window: SdkWindow }).window = {
      __QUBIT_PLUGINS__: undefined,
      __QUBIT_PLUGIN_SDK__: undefined,
      __QUBIT_PLUGINS_LEGACY__: undefined,
      __QUBIT_PLUGIN_SDK_LEGACY__: undefined,
    };
  });

  it("exposes Dialog + subcomponents on components", () => {
    exposePluginSDK();
    const sdk = (globalThis as unknown as { window: SdkWindow }).window
      .__QUBIT_PLUGIN_SDK__!;
    expect(sdk.components.Dialog).toBeDefined();
    expect(sdk.components.DialogContent).toBeDefined();
    expect(sdk.components.DialogHeader).toBeDefined();
    expect(sdk.components.DialogTitle).toBeDefined();
    expect(sdk.components.DialogDescription).toBeDefined();
    expect(sdk.components.DialogFooter).toBeDefined();
    expect(sdk.components.DialogClose).toBeDefined();
    expect(sdk.components.ConfirmDialog).toBeDefined();
    expect(sdk.components.Toast).toBeDefined();
  });

  it("exposes useToast and useConfirmDelete on hooks", () => {
    exposePluginSDK();
    const sdk = (globalThis as unknown as { window: SdkWindow }).window
      .__QUBIT_PLUGIN_SDK__!;
    expect(typeof sdk.hooks.useToast).toBe("function");
    expect(typeof sdk.hooks.useConfirmDelete).toBe("function");
    // Original React hooks still present (no accidental removal).
    expect(typeof sdk.hooks.useState).toBe("function");
    expect(typeof sdk.hooks.useCallback).toBe("function");
  });

  it("keeps the legacy pre-rebrand aliases assigned to the same objects", () => {
    exposePluginSDK();
    const w = (globalThis as unknown as { window: SdkWindow }).window;
    expect(w.__QUBIT_PLUGIN_SDK_LEGACY__).toBe(w.__QUBIT_PLUGIN_SDK__);
    expect(w.__QUBIT_PLUGINS_LEGACY__).toBe(w.__QUBIT_PLUGINS__);
  });
});