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
 *
 * Styling carries the shipped upstream console vocabulary
 * (`src/styles/upstream/upstream-devices.css`, `upstream-chrome.css`): the
 * `device-entry` grid per pairing, `device-capability` chips (incl. the
 * `--disabled` and `--scope` modifiers), `settings-row` / settings-table rows
 * for the form and status rows, and `btn` / `callout` for the actions and the
 * inline notices. The `qb-upstream` scope (upstream-compat.css) supplies the
 * theme vars those sheets read.
 *
 * Disabled-with-reason: capabilities this dashboard cannot perform for a
 * given pairing are rendered as `device-capability--disabled` chips with a
 * visible reason, and the manage action is a refused button carrying the same
 * reason as its title — never a silent gap.
 */
import { useCallback, useEffect, useState } from "react";
import {
  Check,
  Copy as CopyIcon,
  KeyRound,
  Link2,
  MonitorSmartphone,
  Radio,
  RefreshCw,
  ShieldCheck,
  Trash2,
} from "lucide-react";

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

/** The management capabilities the upstream console surfaces per device. */
interface DeviceCapability {
  key: string;
  label: string;
  wired: boolean;
  reason?: string;
}

export default function DevicesPage() {
  const [status, setStatus] = useState<DeviceSetupStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [pairings, setPairings] = useState<DevicePairing[]>([]);
  const [pairingsState, setPairingsState] = useState<"loading" | "ready" | "error">("loading");
  const [pairingsError, setPairingsError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [noticeKind, setNoticeKind] = useState<"info" | "success">("info");
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
        setNoticeKind("success");
        setNotice(`${label} completed.`);
        load();
      } catch (err: unknown) {
        setNoticeKind("info");
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
        setNoticeKind("success");
        setNotice("Setup code minted.");
        load();
      })
      .catch((err: unknown) => {
        setNoticeKind("info");
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
        setNoticeKind("info");
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

  /**
   * Capabilities for one pairing. The management capabilities are wired only
   * when the backend reports the pairing as manageable; otherwise they render
   * disabled with the backend-supplied reason.
   */
  const capabilitiesFor = (
    p: DevicePairing,
    wired: Array<{ key: string; label: string }>,
  ): DeviceCapability[] =>
    wired.map((c) => ({
      ...c,
      wired: p.manageable,
      reason: p.manageable ? undefined : p.manage_reason ?? "This pairing cannot be managed from the dashboard.",
    }));

  const renderCapabilities = (caps: DeviceCapability[], scope: string) => (
    <ul className="device-capabilities" aria-label={`${scope} capabilities`}>
      {caps.map((c) => {
        const enabled = c.wired;
        return (
          <li
            key={c.key}
            className={cn(
              "device-capability",
              !enabled && "device-capability--disabled",
            )}
            data-capability={c.key}
            data-state={enabled ? "available" : "disabled"}
            title={
              enabled
                ? `${c.label} is available for ${scope}.`
                : c.reason ?? `${c.label} is not wired for ${scope}.`
            }
            aria-disabled={enabled ? undefined : "true"}
          >
            <span className="device-capability__icon" aria-hidden="true">
              <Check />
            </span>
            <span>{c.label}</span>
          </li>
        );
      })}
    </ul>
  );

  const renderPairingRow = (p: DevicePairing) => {
    const caps = capabilitiesFor(p, [
      { key: "approve", label: "approve" },
      { key: "reject", label: "reject" },
      { key: "remove", label: "remove" },
      { key: "token-rotate", label: "token rotate" },
      { key: "token-revoke", label: "token revoke" },
      { key: "identity", label: "identity" },
    ]);
    return (
      <li key={p.id} className="device-entry">
        <span className="device-entry__tile" aria-hidden="true">
          <span className="device-entry__tile-icon">
            {p.kind === "dm" ? <Radio /> : <MonitorSmartphone />}
          </span>
        </span>

        <div className="device-entry__body">
          <div className="device-entry__heading">
            <span className="settings-row__title">{p.label}</span>
            <span className="device-entry__status" data-state={p.state}>
              {p.state}
            </span>
            <span className="device-capability device-capability--scope">
              <span className="device-capability__icon" aria-hidden="true">
                <Link2 />
              </span>
              <span>{p.kind}</span>
            </span>
            <span className="device-capability">
              <span className="device-capability__icon" aria-hidden="true">
                <KeyRound />
              </span>
              <span>secret: {p.secret_set ? "stored" : "not stored"}</span>
            </span>
          </div>

          <div className="settings-row__desc">
            <span className="settings-row__value settings-row__value--mono">id: </span>
            <span className="font-mono">{p.id}</span>
          </div>
          <div className="settings-row__desc">
            channel: {p.kind} · state: {p.state}
          </div>
          <div className="settings-row__desc">
            secret: {p.secret_set ? "stored" : "not stored"}
          </div>

          {renderCapabilities(caps, p.id)}
        </div>

        {p.manageable ? (
          <div className="device-entry__token-actions settings-row__control">
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() =>
                mutate("Approve", `approve:${p.id}`, (confirm) =>
                  approveDevicePairing(p.id, confirm),
                )
              }
              disabled={busy === `approve:${p.id}`}
              aria-label={`Approve pairing ${p.id}`}
            >
              {busy === `approve:${p.id}` ? <Spinner /> : "approve"}
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() =>
                mutate("Reject", `reject:${p.id}`, (confirm) =>
                  rejectDevicePairing(p.id, confirm),
                )
              }
              disabled={busy === `reject:${p.id}`}
              aria-label={`Reject pairing ${p.id}`}
            >
              {busy === `reject:${p.id}` ? <Spinner /> : "reject"}
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() =>
                mutate("Remove", `remove:${p.id}`, (confirm) =>
                  removeDevicePairing(p.id, confirm),
                )
              }
              disabled={busy === `remove:${p.id}`}
              aria-label={`Remove pairing ${p.id}`}
            >
              {busy === `remove:${p.id}` ? <Spinner /> : <Trash2 />}
              {busy === `remove:${p.id}` ? null : <span>remove</span>}
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() =>
                mutate("Token rotate", `rotate:${p.id}`, (confirm) =>
                  rotateDeviceToken(p.id, confirm),
                )
              }
              disabled={busy === `rotate:${p.id}`}
              aria-label={`Rotate token for ${p.id}`}
            >
              {busy === `rotate:${p.id}` ? <Spinner /> : "rotate token"}
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() =>
                mutate("Token revoke", `revoke:${p.id}`, (confirm) =>
                  revokeDeviceToken(p.id, confirm),
                )
              }
              disabled={busy === `revoke:${p.id}`}
              aria-label={`Revoke token for ${p.id}`}
            >
              {busy === `revoke:${p.id}` ? <Spinner /> : "revoke token"}
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => showIdentity(p.id)}
              aria-label={`Show identity for ${p.id}`}
            >
              identity
            </button>
          </div>
        ) : (
          <div className="device-entry__token-actions settings-row__control">
            <button
              type="button"
              disabled
              data-state="refused"
              title={p.manage_reason ?? "This pairing cannot be managed from the dashboard."}
              aria-label={`Manage pairing ${p.id} unavailable`}
              aria-disabled="true"
              className="btn btn--ghost btn--sm"
            >
              manage unavailable
            </button>
            <span className="settings-row__desc">
              {p.manage_reason ?? "This pairing cannot be managed from the dashboard."}
            </span>
          </div>
        )}
      </li>
    );
  };

  const pending = pairings.filter((p) => p.state === "pending");
  const paired = pairings.filter((p) => p.state !== "pending");

  return (
    <div
      className={cn(
        "qb-upstream settings-page settings-page--wide",
      )}
    >
      <section aria-label="Device setup status" className="settings-section">
        <div className="settings-section__header">
          <div className="settings-section__copy">
            <h2 className="settings-section__heading">Devices</h2>
            <p className="settings-section__desc">
              Setup codes, pairings, tokens, enrollment, and identity.
            </p>
          </div>
          <div className="settings-section__actions">
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={load}
              disabled={pairingsState === "loading"}
              aria-label="Refresh devices"
            >
              {pairingsState === "loading" ? <Spinner /> : <RefreshCw />}
              {pairingsState === "loading" ? null : <span>refresh</span>}
            </button>
          </div>
        </div>
        <div className="settings-group">
          <div className="settings-row">
            <div className="settings-row__text">
              <span className="settings-row__title">Setup status</span>
              {statusError ? null : status ? (
                <span className="settings-row__desc">
                  {status.enabled ? "enabled" : "disabled"} · {status.pending_codes} pending codes ·{" "}
                  {status.paired_devices} paired devices
                </span>
              ) : (
                <span className="settings-row__desc flex items-center gap-2">
                  <Spinner className="shrink-0" /> Loading status…
                </span>
              )}
            </div>
            {statusError ? (
              <div role="alert" className="callout danger">
                {statusError}
              </div>
            ) : null}
          </div>
        </div>
      </section>

      <section
        aria-label="Mint setup code form"
        className="settings-section"
      >
        <div className="settings-group">
          <div className="settings-row settings-row--stacked">
            <div className="settings-row__text">
              <span className="settings-row__title">Mint setup code</span>
              <span className="settings-row__desc">
                Label, scopes, and expiry for the next setup code.
              </span>
            </div>
            <div className="settings-row__control">
              <input
                type="text"
                value={mintLabel}
                onChange={(e) => setMintLabel(e.target.value)}
                placeholder="label"
                aria-label="Setup code label"
                className="settings-input"
              />
              <input
                type="text"
                value={mintScopes}
                onChange={(e) => setMintScopes(e.target.value)}
                placeholder="scopes, comma separated"
                aria-label="Setup code scopes"
                className="settings-input"
              />
              <input
                type="number"
                min={1}
                value={mintTtl}
                onChange={(e) => setMintTtl(e.target.value)}
                placeholder="TTL seconds"
                aria-label="Setup code TTL seconds"
                className="settings-input"
              />
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={mint}
                disabled={busy === "mint"}
                aria-label="Mint setup code"
              >
                {busy === "mint" ? <Spinner /> : <CopyIcon />}
                {busy === "mint" ? null : <span>mint</span>}
              </button>
            </div>
          </div>
          {minted ? (
            <div className="callout callout--dismissible" role="status">
              <span>
                Minted {minted.id} ({minted.label}) scopes {minted.scopes.join(", ")}
                {minted.expires_at ? ` expires ${minted.expires_at}` : ""} · code stored
                server-side, never displayed here.
              </span>
            </div>
          ) : null}
        </div>
      </section>

      <section aria-label="Device pairings card" className="settings-section">
        <div className="settings-section__header">
          <div className="settings-section__copy">
            <h2 className="settings-section__heading">Devices</h2>
            <p className="settings-section__desc">Pairing requests + role tokens.</p>
          </div>
        </div>
        {pairingsState === "error" && pairingsError ? (
          <div role="alert" className="callout danger">
            {pairingsError}
          </div>
        ) : null}

        <div className="settings-group" role="group" aria-label="Device pairings">
          {pairingsState === "loading" ? (
            <p className="settings-row__desc flex items-center gap-2">
              <Spinner className="shrink-0" /> Loading pairings…
            </p>
          ) : pairingsState === "error" ? (
            <div role="alert" className="callout danger">
              {pairingsError}
            </div>
          ) : pairings.length === 0 ? (
            <div className="callout" role="status">
              No paired devices.
            </div>
          ) : (
            <>
              {pending.length > 0 ? (
                <div className="device-group__dups">
                  <p className="settings-row__desc">Pending</p>
                  <ul className="device-entry__tokens" aria-label="Pending device pairings">
                    {pending.map(renderPairingRow)}
                  </ul>
                </div>
              ) : null}
              {paired.length > 0 ? (
                <div className="device-group__dups">
                  <p className="settings-row__desc">Paired</p>
                  <ul className="device-entry__tokens" aria-label="Paired devices">
                    {paired.map(renderPairingRow)}
                  </ul>
                </div>
              ) : null}
            </>
          )}
        </div>
      </section>

      <section aria-label="Enroll device form" className="settings-section">
        <div className="settings-group">
          <div className="settings-row settings-row--stacked">
            <div className="settings-row__text">
              <span className="settings-row__title">Enroll device</span>
              <span className="settings-row__desc">
                Redeem a setup code id with an optional label.
              </span>
            </div>
            <div className="settings-row__control">
              <input
                type="text"
                value={enrollCodeId}
                onChange={(e) => setEnrollCodeId(e.target.value)}
                placeholder="setup code id"
                aria-label="Enroll setup code id"
                className="settings-input font-mono"
              />
              <input
                type="text"
                value={enrollLabel}
                onChange={(e) => setEnrollLabel(e.target.value)}
                placeholder="label (optional)"
                aria-label="Enroll device label"
                className="settings-input"
              />
              <button
                type="button"
                className="btn btn--ghost btn--sm"
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
                {busy === "enroll" ? <Spinner /> : <ShieldCheck />}
                {busy === "enroll" ? null : <span>enroll</span>}
              </button>
            </div>
          </div>
        </div>
      </section>

      {identityFor ? (
        <section aria-label="Device identity" className="device-entry">
          <span className="device-entry__tile" aria-hidden="true">
            <span className="device-entry__tile-icon">
              <ShieldCheck />
            </span>
          </span>
          <div className="device-entry__body">
            <h3 className="device-entry__heading font-mono">{identityFor}</h3>
            {identity ? (
              <dl className="device-entry__facts">
                <dt className="settings-row__desc">label</dt>
                <dd className="settings-row__value">{identity.label}</dd>
                {identity.enrolled_at ? (
                  <>
                    <dt className="settings-row__desc">enrolled</dt>
                    <dd className="settings-row__value">{identity.enrolled_at}</dd>
                  </>
                ) : null}
                <dt className="settings-row__desc">scopes</dt>
                <dd className="settings-row__value">
                  <span>scopes: {identity.scopes.join(", ")}</span>
                  <ul className="device-entry__scopes" aria-label="Identity scopes">
                    {identity.scopes.map((scope) => (
                      <li key={scope} className="device-capability device-capability--scope">
                        <span className="device-capability__icon" aria-hidden="true">
                          <KeyRound />
                        </span>
                        <span>{scope}</span>
                      </li>
                    ))}
                  </ul>
                </dd>
              </dl>
            ) : (
              <p className="settings-row__desc flex items-center gap-2">
                <Spinner className="shrink-0" /> Loading identity…
              </p>
            )}
          </div>
        </section>
      ) : null}

      {notice ? (
        <div
          role="status"
          className={cn("callout callout--dismissible", noticeKind === "success" && "success")}
        >
          {notice}
        </div>
      ) : null}

      {pendingConfirm ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Confirm device action"
          className="callout callout--action warn"
        >
          <p className="settings-row__desc">{pendingConfirm.detail}</p>
          <div className="flex gap-2">
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => settleConfirm(true)}
              aria-label="Confirm"
            >
              Confirm
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => settleConfirm(false)}
              aria-label="Cancel"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
