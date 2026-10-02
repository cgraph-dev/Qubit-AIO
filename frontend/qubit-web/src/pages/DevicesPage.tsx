/**
 * Devices page (route /devices). Setup-code mint form (label + scopes + TTL),
 * setup status, pairing roster including direct-message threads, confirm-gated
 * approve/reject/remove, confirm-gated token rotate/revoke, enroll plus the
 * device identity card. Secret values are never displayed: presence is shown
 * as stored/not-stored badges plus metadata only.
 *
 * Layout mirrors the upstream nodes-devices card hierarchy (status card +
 * pairing-requests card with Pending/Paired groups + token rows + enroll
 * card + identity card), translated into Qubit voice. Every backend call,
 * confirm-gating, and disabled-with-reason row is wired exactly as before.
 */
import { useCallback, useEffect, useState } from "react";

import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";

import {
  approveDevicePairing,
  enrollDevice,
  getDeviceIdentity,
  getDeviceSetupStatus,
  listDevicePairings,
  mintDeviceSetupCode,
  rejectDevicePairing,
  removeDevicePairing,
  revokeDeviceToken,
  rotateDeviceToken,
  type ConfirmPrompt,
  type DeviceIdentity,
  type DevicePairing,
  type DeviceSetupStatus,
  type SetupCodeMintResponse,
} from "@/lib/ocFamilyAgentsApi";
import { refusalOrError } from "@/lib/policy-refusal";
import { cn } from "@/lib/utils";

interface PendingConfirm {
  detail: string;
  resolve: (value: boolean) => void;
}

export default function DevicesPage() {
  const [status, setStatus] = useState<DeviceSetupStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [pairings, setPairings] = useState<DevicePairing[]>([]);
  const [pairingsState, setPairingsState] = useState<"loading" | "ready" | "error">("loading");
  const [pairingsError, setPairingsError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirm | null>(null);

  const [mintLabel, setMintLabel] = useState("");
  const [mintScopes, setMintScopes] = useState("");
  const [mintTtl, setMintTtl] = useState("3600");
  const [minted, setMinted] = useState<SetupCodeMintResponse | null>(null);
  const [enrollCodeId, setEnrollCodeId] = useState("");
  const [enrollLabel, setEnrollLabel] = useState("");
  const [identity, setIdentity] = useState<DeviceIdentity | null>(null);
  const [identityFor, setIdentityFor] = useState<string | null>(null);

  const confirmPrompt = useCallback(
    (detailText: string) =>
      new Promise<boolean>((resolve) => {
        setPendingConfirm({ detail: detailText, resolve });
      }),
    [],
  );

  const load = useCallback(() => {
    setPairingsState("loading");
    setPairingsError(null);
    setStatusError(null);
    getDeviceSetupStatus()
      .then(setStatus)
      .catch((err: unknown) => {
        setStatusError(refusalOrError(err, "Setup status failed."));
      });
    listDevicePairings()
      .then((res) => {
        setPairings(res.pairings ?? []);
        setPairingsState("ready");
      })
      .catch((err: unknown) => {
        setPairingsError(refusalOrError(err, "Pairing list failed."));
        setPairingsState("error");
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const mutate = useCallback(
    async (label: string, key: string, fn: (confirm: ConfirmPrompt) => Promise<unknown>) => {
      setBusy(key);
      setNotice(null);
      try {
        await fn(confirmPrompt);
        setNotice(`${label} completed.`);
        load();
      } catch (err: unknown) {
        setNotice(refusalOrError(err, `${label} failed.`));
      } finally {
        setBusy(null);
      }
    },
    [confirmPrompt, load],
  );

  const mint = useCallback(() => {
    const scopes = mintScopes
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
    const ttl = Number.parseInt(mintTtl, 10);
    if (!mintLabel.trim() || scopes.length === 0 || !Number.isFinite(ttl) || ttl <= 0) return;
    setBusy("mint");
    setNotice(null);
    setMinted(null);
    mintDeviceSetupCode(
      { label: mintLabel.trim(), scopes, ttl_seconds: ttl },
      confirmPrompt,
    )
      .then((res) => {
        // The code value itself is never rendered: only id, label, scopes and
        // expiry are shown. Presence is reported via code_present.
        setMinted(res);
        setNotice("Setup code minted.");
        load();
      })
      .catch((err: unknown) => {
        setNotice(refusalOrError(err, "Setup code mint failed."));
      })
      .finally(() => setBusy(null));
  }, [confirmPrompt, load, mintLabel, mintScopes, mintTtl]);

  const showIdentity = useCallback((deviceId: string) => {
    setIdentityFor(deviceId);
    setIdentity(null);
    getDeviceIdentity(deviceId)
      .then((res) => setIdentity(res.identity))
      .catch((err: unknown) => {
        setNotice(refusalOrError(err, "Identity read failed."));
      });
  }, []);

  const settleConfirm = useCallback(
    (value: boolean) => {
      const pending = pendingConfirm;
      setPendingConfirm(null);
      pending?.resolve(value);
    },
    [pendingConfirm],
  );

  const renderPairingRow = (p: DevicePairing) => (
    <li
      key={p.id}
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border border-border bg-background-base/60 px-3 py-2 text-xs"
    >
      <div className="min-w-0 flex-1">
        <div className="font-mono text-text-secondary">{p.id}</div>
        <div className="text-text-tertiary">{p.label}</div>
        <div className="mt-0.5 text-text-tertiary">
          channel: {p.kind} · state: {p.state}
        </div>
        <div className="text-text-tertiary">
          secret: {p.secret_set ? "stored" : "not stored"}
        </div>
      </div>
      {p.manageable ? (
        <span className="ml-auto flex flex-wrap items-center gap-1">
          <Button
            ghost
            size="sm"
            onClick={() =>
              mutate("Approve", `approve:${p.id}`, (confirm) =>
                approveDevicePairing(p.id, confirm),
              )
            }
            disabled={busy === `approve:${p.id}`}
            aria-label={`Approve pairing ${p.id}`}
          >
            {busy === `approve:${p.id}` ? <Spinner /> : "approve"}
          </Button>
          <Button
            ghost
            size="sm"
            onClick={() =>
              mutate("Reject", `reject:${p.id}`, (confirm) =>
                rejectDevicePairing(p.id, confirm),
              )
            }
            disabled={busy === `reject:${p.id}`}
            aria-label={`Reject pairing ${p.id}`}
          >
            {busy === `reject:${p.id}` ? <Spinner /> : "reject"}
          </Button>
          <Button
            ghost
            size="sm"
            onClick={() =>
              mutate("Remove", `remove:${p.id}`, (confirm) =>
                removeDevicePairing(p.id, confirm),
              )
            }
            disabled={busy === `remove:${p.id}`}
            aria-label={`Remove pairing ${p.id}`}
          >
            {busy === `remove:${p.id}` ? <Spinner /> : "remove"}
          </Button>
          <Button
            ghost
            size="sm"
            onClick={() =>
              mutate("Token rotate", `rotate:${p.id}`, (confirm) =>
                rotateDeviceToken(p.id, confirm),
              )
            }
            disabled={busy === `rotate:${p.id}`}
            aria-label={`Rotate token for ${p.id}`}
          >
            {busy === `rotate:${p.id}` ? <Spinner /> : "rotate token"}
          </Button>
          <Button
            ghost
            size="sm"
            onClick={() =>
              mutate("Token revoke", `revoke:${p.id}`, (confirm) =>
                revokeDeviceToken(p.id, confirm),
              )
            }
            disabled={busy === `revoke:${p.id}`}
            aria-label={`Revoke token for ${p.id}`}
          >
            {busy === `revoke:${p.id}` ? <Spinner /> : "revoke token"}
          </Button>
          <Button
            ghost
            size="sm"
            onClick={() => showIdentity(p.id)}
            aria-label={`Show identity for ${p.id}`}
          >
            identity
          </Button>
        </span>
      ) : (
        <button
          type="button"
          disabled
          data-state="refused"
          title={p.manage_reason ?? "This pairing cannot be managed from the dashboard."}
          aria-label={`Manage pairing ${p.id} unavailable`}
          aria-disabled="true"
          className="ml-auto cursor-not-allowed border border-border px-2 py-1 text-xs text-text-tertiary opacity-60"
        >
          manage unavailable
        </button>
      )}
      {p.manageable ? null : (
        <span className="w-full text-text-tertiary">
          {p.manage_reason ?? "This pairing cannot be managed from the dashboard."}
        </span>
      )}
    </li>
  );

  const pending = pairings.filter((p) => p.state === "pending");
  const paired = pairings.filter((p) => p.state !== "pending");

  return (
    <div className={cn("flex min-h-0 w-full min-w-0 flex-1 flex-col gap-3 pt-1 sm:pt-2")}>
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-medium tracking-[0.08em]">Devices</h2>
          <p className="text-xs text-text-tertiary">
            Setup codes, pairings, tokens, enrollment, and identity.
          </p>
        </div>
        <Button
          ghost
          size="sm"
          onClick={load}
          disabled={pairingsState === "loading"}
          aria-label="Refresh devices"
        >
          {pairingsState === "loading" ? <Spinner /> : "refresh"}
        </Button>
      </div>

      <section
        aria-label="Device setup status"
        className="border border-border bg-background-base/60 px-3 py-2 text-xs text-text-tertiary"
      >
        <h3 className="font-medium text-text-secondary">Setup status</h3>
        {statusError ? (
          <p role="alert" className="text-destructive">
            {statusError}
          </p>
        ) : status ? (
          <p>
            {status.enabled ? "enabled" : "disabled"} · {status.pending_codes} pending codes ·{" "}
            {status.paired_devices} paired devices
          </p>
        ) : (
          <p className="flex items-center gap-2">
            <Spinner className="shrink-0" /> Loading status…
          </p>
        )}
      </section>

      <section
        aria-label="Mint setup code form"
        className="border border-border bg-background-base/60 px-3 py-2"
      >
        <h3 className="text-xs font-medium text-text-secondary">Mint setup code</h3>
        <p className="text-[0.6875rem] text-text-tertiary">
          Label, scopes, and expiry for the next setup code.
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={mintLabel}
            onChange={(e) => setMintLabel(e.target.value)}
            placeholder="label"
            aria-label="Setup code label"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 text-xs"
          />
          <input
            type="text"
            value={mintScopes}
            onChange={(e) => setMintScopes(e.target.value)}
            placeholder="scopes, comma separated"
            aria-label="Setup code scopes"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 text-xs"
          />
          <input
            type="number"
            min={1}
            value={mintTtl}
            onChange={(e) => setMintTtl(e.target.value)}
            placeholder="TTL seconds"
            aria-label="Setup code TTL seconds"
            className="w-32 border border-border bg-background-base px-2 py-1 text-xs"
          />
          <Button
            ghost
            size="sm"
            onClick={mint}
            disabled={busy === "mint"}
            aria-label="Mint setup code"
          >
            {busy === "mint" ? <Spinner /> : "mint"}
          </Button>
        </div>
        {minted ? (
          <p className="mt-1 text-xs text-text-tertiary">
            Minted {minted.id} ({minted.label}) scopes {minted.scopes.join(", ")}
            {minted.expires_at ? ` expires ${minted.expires_at}` : ""} · code stored
            server-side, never displayed here.
          </p>
        ) : null}
      </section>

      <section
        aria-label="Device pairings card"
        className="border border-border bg-background-base/60 px-3 py-2"
      >
        <div className="flex items-center justify-between gap-2">
          <div>
            <h3 className="text-xs font-medium text-text-secondary">Devices</h3>
            <p className="text-[0.6875rem] text-text-tertiary">
              Pairing requests + role tokens.
            </p>
          </div>
          {pairingsState === "error" && pairingsError ? (
            <p role="alert" className="text-xs text-destructive">
              {pairingsError}
            </p>
          ) : null}
        </div>

        <div className="mt-2" role="group" aria-label="Device pairings">
          {pairingsState === "loading" ? (
            <p className="flex items-center gap-2 text-sm text-text-tertiary">
              <Spinner className="shrink-0" /> Loading pairings…
            </p>
          ) : pairingsState === "error" ? (
            <p role="alert" className="text-sm text-destructive">
              {pairingsError}
            </p>
          ) : pairings.length === 0 ? (
            <p className="text-sm text-text-tertiary">No paired devices.</p>
          ) : (
            <>
              {pending.length > 0 ? (
                <div className="mt-1">
                  <p className="mb-1 text-[0.6875rem] text-text-tertiary">Pending</p>
                  <ul className="flex flex-col gap-1" aria-label="Pending device pairings">
                    {pending.map(renderPairingRow)}
                  </ul>
                </div>
              ) : null}
              {paired.length > 0 ? (
                <div className="mt-2">
                  <p className="mb-1 text-[0.6875rem] text-text-tertiary">Paired</p>
                  <ul className="flex flex-col gap-1" aria-label="Paired devices">
                    {paired.map(renderPairingRow)}
                  </ul>
                </div>
              ) : null}
            </>
          )}
        </div>
      </section>

      <section
        aria-label="Enroll device form"
        className="border border-border bg-background-base/60 px-3 py-2"
      >
        <h3 className="text-xs font-medium text-text-secondary">Enroll device</h3>
        <p className="text-[0.6875rem] text-text-tertiary">
          Redeem a setup code id with an optional label.
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={enrollCodeId}
            onChange={(e) => setEnrollCodeId(e.target.value)}
            placeholder="setup code id"
            aria-label="Enroll setup code id"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 font-mono text-xs"
          />
          <input
            type="text"
            value={enrollLabel}
            onChange={(e) => setEnrollLabel(e.target.value)}
            placeholder="label (optional)"
            aria-label="Enroll device label"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 text-xs"
          />
          <Button
            ghost
            size="sm"
            onClick={() =>
              mutate("Enroll", "enroll", (confirm) =>
                enrollDevice(
                  { setup_code_id: enrollCodeId.trim(), label: enrollLabel.trim() || undefined },
                  confirm,
                ),
              )
            }
            disabled={busy === "enroll" || !enrollCodeId.trim()}
            aria-label="Enroll device"
          >
            {busy === "enroll" ? <Spinner /> : "enroll"}
          </Button>
        </div>
      </section>

      {identityFor ? (
        <section
          aria-label="Device identity"
          className="border border-border bg-background-base/60 px-3 py-2 text-xs text-text-tertiary"
        >
          <h3 className="font-mono text-text-secondary">{identityFor}</h3>
          {identity ? (
            <div className="mt-1">
              <p>label: {identity.label}</p>
              {identity.enrolled_at ? <p>enrolled: {identity.enrolled_at}</p> : null}
              <p>scopes: {identity.scopes.join(", ")}</p>
            </div>
          ) : (
            <p className="mt-1 flex items-center gap-2">
              <Spinner className="shrink-0" /> Loading identity…
            </p>
          )}
        </section>
      ) : null}

      {notice ? (
        <p role="status" className="text-xs text-text-secondary">
          {notice}
        </p>
      ) : null}

      {pendingConfirm ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Confirm device action"
          className="border border-border bg-background-base px-3 py-2 text-xs"
        >
          <p className="text-text-secondary">{pendingConfirm.detail}</p>
          <div className="mt-2 flex gap-2">
            <Button ghost size="sm" onClick={() => settleConfirm(true)} aria-label="Confirm">
              Confirm
            </Button>
            <Button ghost size="sm" onClick={() => settleConfirm(false)} aria-label="Cancel">
              Cancel
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
