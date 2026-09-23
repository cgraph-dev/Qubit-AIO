// @vitest-environment jsdom
/**
 * `gatewayLine` must reflect the REAL Qubit supervisor vocabulary, not the
 * vendored gateway labels. `voicebridge/gateway_supervisor.status()` emits its
 * `state` ("OK"/"DEGRADED"/"UNAVAILABLE"/"NOT_INSTALLED") while the service is
 * active, and the systemd `ActiveState` ("active"/"activating"/"inactive"/
 * "failed"/…) while it is not. The strip previously mapped only
 * running/starting/startup_failed/stopped, so every non-running state collapsed
 * to "Off". These tests pin the state-driven mapping (browser-verified against
 * the live `gateway_state: "OK"` payload).
 */
import { describe, expect, it } from "vitest";
import { gatewayLine } from "@/components/SidebarStatusStrip";
import { en } from "@/i18n/en";
import type { StatusResponse } from "@/lib/api";

function status(partial: Partial<StatusResponse>): StatusResponse {
  return {
    active_sessions: 0,
    config_path: "",
    config_version: 1,
    env_path: "",
    gateway_exit_reason: null,
    gateway_health_url: null,
    gateway_pid: null,
    gateway_platforms: {},
    gateway_running: false,
    gateway_state: null,
    gateway_updated_at: null,
    qubit_home: "",
    latest_config_version: 1,
    release_date: "",
    version: "",
    ...partial,
  };
}

describe("gatewayLine", () => {
  it("maps the real supervisor running state (OK) to Running", () => {
    const gw = gatewayLine(
      status({ gateway_running: true, gateway_state: "OK" }),
      en,
    );
    expect(gw.label).toBe(en.app.gatewayStrip.running);
  });

  it("maps systemd inactive to Stopped, not Off", () => {
    const gw = gatewayLine(
      status({ gateway_running: false, gateway_state: "inactive" }),
      en,
    );
    expect(gw.label).toBe(en.app.gatewayStrip.stopped);
  });

  it("maps systemd failed to Start failed", () => {
    const gw = gatewayLine(
      status({ gateway_running: false, gateway_state: "failed" }),
      en,
    );
    expect(gw.label).toBe(en.app.gatewayStrip.failed);
  });

  it("maps systemd activating to Starting", () => {
    const gw = gatewayLine(
      status({ gateway_running: false, gateway_state: "activating" }),
      en,
    );
    expect(gw.label).toBe(en.app.gatewayStrip.starting);
  });

  it("falls back to gateway_running, then Off, for unknown states", () => {
    expect(
      gatewayLine(
        status({ gateway_running: true, gateway_state: "something-else" }),
        en,
      ).label,
    ).toBe(en.app.gatewayStrip.running);
    expect(
      gatewayLine(status({ gateway_running: false, gateway_state: null }), en)
        .label,
    ).toBe(en.app.gatewayStrip.off);
  });
});
