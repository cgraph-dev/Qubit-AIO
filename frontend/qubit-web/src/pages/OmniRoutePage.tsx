/**
 * OmniRoute page (route /omniroute). Local AI-gateway console.
 *
 * OmniRoute serves `X-Frame-Options: DENY` (+ `frame-ancestors 'none'`), so
 * the dashboard cannot be embedded here — this page shows gateway status,
 * the Qubit wiring (profiles + fallback chain), and opens the dashboard in
 * a new tab. No secret values are ever rendered: the gateway API key lives
 * in the Qubit runtime `.env` as `OMNIROUTE_API_KEY` and is only referenced.
 */
import { useEffect, useState } from "react";

import { copyTextToClipboard } from "@/lib/clipboard";

import {
  SettingsPage,
  SettingsPageHeader,
  SettingsRow,
  SettingsSection,
  SettingsValue,
} from "@/components/console-ui";

const GATEWAY_DASHBOARD = "http://127.0.0.1:20128";
const GATEWAY_API_BASE = "http://127.0.0.1:20128/v1";

const QUBIT_PROFILES = [
  "omniroute-auto",
  "omniroute-coding",
  "omniroute-fast",
  "omniroute-cheap",
];

type Health = "checking" | "running" | "unreachable";

async function probeGateway(timeoutMs = 5000): Promise<Health> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${GATEWAY_DASHBOARD}/api/health`, {
      signal: ctrl.signal,
    });
    return res.ok ? "running" : "unreachable";
  } catch {
    return "unreachable";
  } finally {
    clearTimeout(timer);
  }
}

function copyText(value: string) {
  // The one shared helper (lib/clipboard.ts): the guard forbids direct
  // navigator.clipboard writes outside it.
  void copyTextToClipboard(value);
}

export default function OmniRoutePage() {
  const [health, setHealth] = useState<Health>("checking");

  useEffect(() => {
    let live = true;
    probeGateway().then((next) => {
      if (live) setHealth(next);
    });
    return () => {
      live = false;
    };
  }, []);

  const statusText =
    health === "checking"
      ? "Probing the gateway…"
      : health === "running"
        ? "Running — the gateway answered /api/health."
        : "Unreachable — start it with `omniroute serve --daemon --no-open`.";

  return (
    <SettingsPage>
      <SettingsPageHeader
        title="OmniRoute Gateway"
        description="Local AI gateway: Qubit fails over to omniroute/auto when a primary provider rate-limits. The dashboard opens in a new tab (OmniRoute refuses framing)."
        actions={
          <a
            href={GATEWAY_DASHBOARD}
            target="_blank"
            rel="noreferrer"
            className="btn btn--ghost btn--sm"
          >
            Open dashboard
          </a>
        }
      />

      <SettingsSection title="Gateway status" testId="omniroute-status">
        <SettingsRow
          title="Server"
          description={statusText}
          testId="omniroute-status-row"
        />
        <SettingsRow
          title="API base URL"
          description={
            <SettingsValue mono>{GATEWAY_API_BASE}</SettingsValue>
          }
          control={
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => copyText(GATEWAY_API_BASE)}
            >
              Copy
            </button>
          }
        />
        <SettingsRow
          title="Credential"
          description={
            <span>
              Qubit profile key ref{" "}
              <SettingsValue mono>env:OMNIROUTE_API_KEY</SettingsValue>{" "}
              (value stays in the runtime .env, never shown here).
            </span>
          }
        />
      </SettingsSection>

      <SettingsSection
        title="Qubit wiring"
        description="Profiles selectable in the Models list; every external profile falls back to omniroute-auto, and the agent fallback chain carries omniroute/auto after the primary."
      >
        <SettingsRow
          title="Profiles"
          description={
            <SettingsValue mono>{QUBIT_PROFILES.join(" · ")}</SettingsValue>
          }
        />
        <SettingsRow
          title="Failover"
          description="Rate-limit / overload / connection error → omniroute/auto (quota-aware internal fallback), primary auto-restores after cooldown."
        />
      </SettingsSection>

      <SettingsSection
        title="Next step"
        description="The gateway has no providers connected until you add them."
      >
        <SettingsRow
          title="Connect providers"
          description="Add your provider accounts under Providers in the OmniRoute dashboard so auto has a healthy route to serve."
          control={
            <a
              href={`${GATEWAY_DASHBOARD}/dashboard/providers`}
              target="_blank"
              rel="noreferrer"
              className="btn btn--ghost btn--sm"
            >
              Open Providers
            </a>
          }
        />
      </SettingsSection>
    </SettingsPage>
  );
}
