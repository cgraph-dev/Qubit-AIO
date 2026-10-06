/**
 * Qubit memory backends (`/qubit-memory`) — SEPARATE page, not part of
 * `/plugins` (owner separation rule).
 *
 * Local-only store wiring behind the existing memory-provider typed gate. The
 * gate persists `qubit-local` only (`MEMORY_LOCAL_ONLY` for anything else), so
 * this page is status plus disabled rows: the local backend gets a live
 * status/read probe and a confirm-gated re-affirm select; everything needing
 * hosted infra or secrets renders disabled-with-reason, never a fake connect
 * button.
 *
 * Backend handlers (registered in `voicebridge/family_memory.py`, verified
 * live): GET /api/memory (status), GET /api/memory/providers/qubit-local
 * (read probe), PUT /api/memory/provider (confirm-gated select, local-only).
 *
 * Built on the shipped openclaw render-helper contract
 * (`@/components/console-ui`): a `SettingsPage` root holding a
 * `SettingsPageHeader`, a summary tile strip, and `SettingsSection` groups.
 * `.settings-row__control` is emitted by the helper ONLY when a control is
 * passed — a control-free row never renders an empty wrapper.
 */

import { useCallback, useEffect, useState } from "react";
import {
  MEMORY_BACKENDS,
  MEMORY_LOCAL_ONLY_REFUSAL,
  getMemoryStatus,
  getProviderStatus,
  normalizeMemoryStatus,
  normalizeProviderStatus,
  refusalOrError,
  setMemoryProvider,
  type MemoryStatusPayload,
  type ProviderStatusPayload,
} from "@/lib/ocFamilyMemoryApi";
import {
  SettingsEmpty,
  SettingsLoadingSkeleton,
  SettingsPage,
  SettingsPageHeader,
  SettingsRow,
  SettingsSection,
  SettingsStatus,
  SettingsSummary,
  SettingsValue,
} from "@/components/console-ui";

type LoadState = "loading" | "ready" | "failed";

export default function MemoryBackendsPage() {
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [status, setStatus] = useState<MemoryStatusPayload | null>(null);
  const [probe, setProbe] = useState<ProviderStatusPayload | null>(null);
  const [loadError, setLoadError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [selecting, setSelecting] = useState(false);

  const refresh = useCallback(async () => {
    setLoadState("loading");
    setLoadError("");
    try {
      const raw = await getMemoryStatus();
      const next = normalizeMemoryStatus(raw);
      if (!next) throw new Error("unexpected memory status shape");
      setStatus(next);
      try {
        const rawProbe = await getProviderStatus("qubit-local");
        setProbe(normalizeProviderStatus(rawProbe));
      } catch {
        // The read probe is best-effort: the status card above is the
        // authority and must still render when the probe fails.
        setProbe(null);
      }
      setLoadState("ready");
    } catch (e) {
      setStatus(null);
      setProbe(null);
      setLoadError(e instanceof Error ? e.message : String(e));
      setLoadState("failed");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onSelectLocal = useCallback(async () => {
    setSelecting(true);
    setActionMessage("");
    try {
      await setMemoryProvider("qubit-local");
      setActionMessage("Qubit local memory confirmed as the active provider.");
      await refresh();
    } catch (e) {
      setActionMessage(refusalOrError(e, "Selecting Qubit local memory failed."));
    } finally {
      setSelecting(false);
    }
  }, [refresh]);

  // Source of truth for the live store: the provider-DETAIL read
  // (`GET /api/memory/providers/qubit-local`) carries `active`, `store_state`,
  // `enabled`, and `tiers`. The LIST read (`GET /api/memory`) returns
  // `active: ""` and none of those fields, so every one of these values is
  // taken from the detail read first and only falls back to the list.
  //
  // `activeProvider` must NEVER fall back to `providers[0].name`: the list's
  // first row is an unrelated hosted backend (e.g. "byterover"), which would
  // render a provider the gate is not actually serving.
  const activeProvider = probe?.active || probe?.provider || status?.active || "";
  const activeProviderLabel = activeProvider || "not reported by this endpoint";

  // `probe` is authoritative; `status` only fills in when the probe failed.
  // Absent everywhere = "not reported", spelled out with a reason rather than
  // a bare placeholder.
  const storeState = probe?.storeState || probe?.state || status?.storeState || status?.state || "";
  const storeEnabled = probe ? probe.enabled : status?.enabled;
  const storeEnabledLabel =
    storeEnabled === undefined
      ? "not reported by this backend"
      : storeEnabled
        ? "yes"
        : "no";

  const summaryItems = status
    ? [
        { label: "Active provider", value: activeProviderLabel },
        {
          label: "Store state",
          value: storeState || "not reported by this backend",
        },
        { label: "Enabled", value: storeEnabledLabel },
        { label: "Memory records", value: status.builtinFiles.memory },
        { label: "User records", value: status.builtinFiles.user },
      ]
    : [];

  return (
    <SettingsPage wide>
      <div data-testid="memory-backends-page" aria-label="Qubit memory backends" className="contents">
        <SettingsPageHeader
          title={<span data-testid="memory-backends-title">Qubit memory backends</span>}
          description={
            <span data-testid="memory-backends-intro">
              Local-only memory stores behind the memory-provider gate. The gate persists{" "}
              <code>qubit-local</code> only ({MEMORY_LOCAL_ONLY_REFUSAL.code}); every other
              backend below is status-only and disabled with its reason.
            </span>
          }
          actions={
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => void refresh()}
              disabled={loadState === "loading"}
              aria-label="Refresh memory status"
            >
              Refresh
            </button>
          }
        />

        {status ? <SettingsSummary items={summaryItems} /> : null}

        <SettingsSection
          title="Current provider"
          description="Local store status read live from GET /api/memory."
        >
          {loadState === "loading" ? (
            <SettingsLoadingSkeleton rows={3} />
          ) : loadState === "failed" ? (
            <SettingsRow
              role="alert"
              title="Memory status unavailable"
              description={<span data-testid="memory-status-error">{loadError}</span>}
              control={
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  data-testid="memory-status-retry"
                  onClick={() => void refresh()}
                >
                  Retry
                </button>
              }
            />
          ) : status ? (
            <>
              <SettingsRow
                title="Active provider"
                description="The store the gate currently serves (from the provider-detail read)."
                control={
                  <SettingsValue mono>
                    <span data-testid="memory-active-provider">{activeProviderLabel}</span>
                  </SettingsValue>
                }
              />
              <SettingsRow
                title="Store state"
                description="Backing store health."
                control={
                  <SettingsValue>
                    <span data-testid="memory-store-state">
                      {storeState || "not reported by this backend"}
                    </span>
                  </SettingsValue>
                }
              />
              <SettingsRow
                title="Enabled"
                description="Auto-injection into prompt context (provider-detail read)."
                control={
                  <SettingsValue>
                    <span data-testid="memory-enabled">{storeEnabledLabel}</span>
                  </SettingsValue>
                }
              />
              <SettingsRow
                title="Records"
                description="Built-in local store counts."
                control={
                  <SettingsValue>
                    <span data-testid="memory-builtin-counts">
                      {status.builtinFiles.memory} memory / {status.builtinFiles.user} user records
                    </span>
                  </SettingsValue>
                }
              />
              {status.storePath ? (
                <SettingsRow
                  title="Store path"
                  control={
                    <SettingsValue mono>
                      <span>{status.storePath}</span>
                    </SettingsValue>
                  }
                />
              ) : null}
              <SettingsRow
                title="Read probe"
                description="Live status read via GET /api/memory/providers/qubit-local."
                control={
                  probe ? (
                    <SettingsValue>
                      <span data-testid="memory-read-probe">
                        {probe.provider} · {probe.state || probe.storeState || "state not reported"} · tiers{" "}
                        {probe.tiers.join(", ") || "—"}
                      </span>
                    </SettingsValue>
                  ) : (
                    <SettingsValue>probe unavailable (best-effort read)</SettingsValue>
                  )
                }
              />
            </>
          ) : null}
        </SettingsSection>

        <SettingsSection
          title="Backends"
          count={MEMORY_BACKENDS.length}
          description="Only the local store is selectable; hosted backends are disabled with their reason."
        >
          <div className="settings-subrows" role="group">
            {MEMORY_BACKENDS.map((backend) => (
              <SettingsRow
                key={backend.id}
                testId={`memory-backend-${backend.id}`}
                title={
                  <span data-testid={`memory-backend-${backend.id}-title`}>
                    {backend.title}
                  </span>
                }
                description={
                  <>
                    <span
                      className="settings-row__desc"
                      data-testid={`memory-backend-${backend.id}-blurb`}
                    >
                      {backend.blurb}
                    </span>
                    <span
                      className="settings-row__desc"
                      data-testid={`memory-backend-${backend.id}-probe`}
                    >
                      {backend.probeKind === "live"
                        ? backend.probeDetail
                        : `Probe unavailable — ${backend.probeDetail}`}
                    </span>
                    {!backend.selectable ? (
                      <span
                        className="settings-row__desc"
                        role="note"
                        data-testid={`memory-backend-${backend.id}-reason`}
                      >
                        {backend.disabledReason}
                      </span>
                    ) : null}
                  </>
                }
                control={
                  backend.selectable ? (
                    <button
                      type="button"
                      className={`btn btn--ghost btn--sm${selecting ? " active" : ""}`}
                      data-testid={`memory-backend-${backend.id}-select`}
                      disabled={selecting || loadState !== "ready"}
                      onClick={() => void onSelectLocal()}
                    >
                      {selecting ? "Confirming…" : "Use Qubit local memory"}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      data-testid={`memory-backend-${backend.id}-select`}
                      disabled
                      title={backend.disabledReason}
                      aria-disabled="true"
                    >
                      Unavailable — disabled with reason
                    </button>
                  )
                }
              />
            ))}
          </div>
          {MEMORY_BACKENDS.filter((b) => !b.selectable).length === 0 ? (
            <SettingsEmpty title="No hosted backends configured">
              Every backend is selectable.
            </SettingsEmpty>
          ) : null}
        </SettingsSection>

        {actionMessage ? (
          <p
            role="status"
            className="settings-status settings-status--accent"
            data-testid="memory-action-message"
          >
            {actionMessage}
          </p>
        ) : null}

        <SettingsSection
          title="Gate policy"
          description="The memory-provider gate is local-only by policy; these rows quote the exact refusal."
        >
          <SettingsRow
            title={<span className="settings-row__value settings-row__value--mono">{MEMORY_LOCAL_ONLY_REFUSAL.method}</span>}
            description={MEMORY_LOCAL_ONLY_REFUSAL.detail}
            control={
              <SettingsStatus tone="warn" title={MEMORY_LOCAL_ONLY_REFUSAL.detail}>
                {MEMORY_LOCAL_ONLY_REFUSAL.code}
              </SettingsStatus>
            }
          />
          <SettingsRow
            title="Counterpart"
            description="Where the capability actually lives on Qubit."
            control={<SettingsValue>{MEMORY_LOCAL_ONLY_REFUSAL.counterpart}</SettingsValue>}
          />
        </SettingsSection>
      </div>
    </SettingsPage>
  );
}
