/**
 * Qubit memory backends (`/qubit-memory`) — SEPARATE page, not part of
 * `/plugins` (owner separation rule).
 *
 * Ports the upstream memory extensions (memory-lancedb, memory-wiki,
 * active-memory — read-only source) as local-only store wiring behind the
 * existing memory-provider typed gate. The gate persists `qubit-local` only
 * (`MEMORY_LOCAL_ONLY` for anything else), so this page is status plus
 * disabled rows: the local backend gets a live status/read probe and a
 * confirm-gated re-affirm select; everything needing hosted infra or secrets
 * renders disabled-with-reason, never a fake connect button.
 *
 * NOTE: the `/qubit-memory` route registration lives in the app shell, which
 * is out of scope for this change (new files only) — this module exports the
 * page; shell wiring is a bridge item.
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

  return (
    <main data-testid="memory-backends-page" aria-label="Qubit memory backends">
      <h1 data-testid="memory-backends-title">Qubit memory backends</h1>
      <p data-testid="memory-backends-intro">
        Local-only memory stores behind the memory-provider gate. The gate
        persists <code>qubit-local</code> only ({MEMORY_LOCAL_ONLY_REFUSAL.code}
        ); every other backend below is status-only and disabled with its
        reason.
      </p>

      <section data-testid="memory-status-card" aria-label="Current provider">
        <h2>Current provider</h2>
        {loadState === "loading" && <p data-testid="memory-status-loading">Loading memory status…</p>}
        {loadState === "failed" && (
          <p data-testid="memory-status-error" role="alert">
            Memory status unavailable: {loadError}{" "}
            <button type="button" data-testid="memory-status-retry" onClick={() => void refresh()}>
              Retry
            </button>
          </p>
        )}
        {loadState === "ready" && status && (
          <dl>
            <div>
              <dt>Active</dt>
              <dd data-testid="memory-active-provider">{status.active}</dd>
            </div>
            <div>
              <dt>State</dt>
              <dd data-testid="memory-store-state">{status.state || status.storeState || "unknown"}</dd>
            </div>
            <div>
              <dt>Enabled</dt>
              <dd data-testid="memory-enabled">{status.enabled ? "yes" : "no"}</dd>
            </div>
            <div>
              <dt>Notes</dt>
              <dd data-testid="memory-builtin-counts">
                {status.builtinFiles.memory} memory / {status.builtinFiles.user} user records
              </dd>
            </div>
            {probe && (
              <div>
                <dt>Read probe</dt>
                <dd data-testid="memory-read-probe">
                  {probe.provider} · {probe.state || "ok"} · tiers {probe.tiers.join(", ") || "—"}
                </dd>
              </div>
            )}
          </dl>
        )}
      </section>

      <section data-testid="memory-backend-list" aria-label="Backends">
        <h2>Backends</h2>
        <ul>
          {MEMORY_BACKENDS.map((backend) => (
            <li key={backend.id} data-testid={`memory-backend-${backend.id}`}>
              <h3 data-testid={`memory-backend-${backend.id}-title`}>{backend.title}</h3>
              <p data-testid={`memory-backend-${backend.id}-blurb`}>{backend.blurb}</p>
              <p data-testid={`memory-backend-${backend.id}-probe`}>
                {backend.probeKind === "live" ? backend.probeDetail : `Probe unavailable — ${backend.probeDetail}`}
              </p>
              {backend.selectable ? (
                <button
                  type="button"
                  data-testid={`memory-backend-${backend.id}-select`}
                  disabled={selecting || loadState !== "ready"}
                  onClick={() => void onSelectLocal()}
                >
                  {selecting ? "Confirming…" : "Use Qubit local memory"}
                </button>
              ) : (
                <button
                  type="button"
                  data-testid={`memory-backend-${backend.id}-select`}
                  disabled
                  title={backend.disabledReason}
                  aria-disabled="true"
                >
                  Unavailable — disabled with reason
                </button>
              )}
              {!backend.selectable && (
                <p data-testid={`memory-backend-${backend.id}-reason`} role="note">
                  {backend.disabledReason}
                </p>
              )}
            </li>
          ))}
        </ul>
      </section>

      {actionMessage && (
        <p data-testid="memory-action-message" role="status">
          {actionMessage}
        </p>
      )}
    </main>
  );
}
