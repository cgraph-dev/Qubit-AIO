/**
 * Operator admin (`/admin/users`).
 *
 * Mirrors the upstream nodes view (summary stats header, Devices card with
 * pending/paired groups and refresh, list rows with status chips) and the
 * upstream agents view (card-per-panel layout with a header action),
 * rebranded to Qubit voice.
 *
 * Qubit single-operator adapter — Qubit has no multi-user module. The one
 * loopback operator identity is read-only; dashboard font/theme prefs are
 * the real persisted preference store; credentials stay references-only
 * (link/unlink confirm-gated); model accounts resolve through the
 * canonical profile registry with the `qubit auth login` manual handoff.
 * Display name, role, avatar, email, GitHub, and auth-start are
 * policy-refused: disabled with the exact prerequisite.
 */
import { useCallback, useEffect, useState } from "react";
import {
  AUTH_LOGIN_HANDOFF,
  DISABLED_USERS_ACTIONS,
  authConnectCatalog,
  getOperatorPrefs,
  getOperatorSelf,
  linkAuthProfile,
  listAuthLinks,
  listMentionable,
  listModelAccounts,
  listOperators,
  selectModelAccount,
  setOperatorPrefs,
  unlinkAuthProfile,
  type AuthConnectCatalogRow,
  type AuthLinkRow,
  type ModelAccountRow,
  type OperatorPrefs,
  type OperatorProfile,
} from "@/lib/ocFamilyAdminApi";
import { refusalOrError } from "@/lib/policy-refusal";

/** Button press IS the confirmation (same pattern as the product pages). */
const CONFIRM_YES = () => true;

function errText(e: unknown, fallback: string): string {
  return refusalOrError(e, `${fallback}: ${e instanceof Error ? e.message : String(e)}`);
}

export default function UsersAdminPage() {
  const [self, setSelf] = useState<OperatorProfile | null>(null);
  const [roster, setRoster] = useState<OperatorProfile[]>([]);
  const [mentionable, setMentionable] = useState<OperatorProfile[]>([]);
  const [prefs, setPrefs] = useState<OperatorPrefs>({});
  const [font, setFont] = useState("");
  const [theme, setTheme] = useState("");
  const [unsupported, setUnsupported] = useState<string[]>([]);
  const [links, setLinks] = useState<AuthLinkRow[]>([]);
  const [accounts, setAccounts] = useState<ModelAccountRow[]>([]);
  const [activeAccount, setActiveAccount] = useState("");
  const [catalog, setCatalog] = useState<AuthConnectCatalogRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [linkProvider, setLinkProvider] = useState("");
  const [linkKeyRef, setLinkKeyRef] = useState("");
  const [linkLabel, setLinkLabel] = useState("");
  const [unlinkProvider, setUnlinkProvider] = useState("");
  const [unlinkIndex, setUnlinkIndex] = useState("0");
  const [selectSession, setSelectSession] = useState("");
  const [selectAccount, setSelectAccount] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const rows = await Promise.allSettled([
      getOperatorSelf(),
      listOperators(),
      listMentionable(),
      getOperatorPrefs(),
      listAuthLinks(),
      listModelAccounts(),
      authConnectCatalog(),
    ]);
    const [s, l, m, p, a, ma, c] = rows;
    if (s.status === "fulfilled") setSelf(s.value.profile);
    if (l.status === "fulfilled") setRoster(l.value.profiles ?? []);
    if (m.status === "fulfilled") setMentionable(m.value.mentionable ?? []);
    if (p.status === "fulfilled") {
      setPrefs(p.value.entries ?? {});
      setFont(String(p.value.entries?.font ?? ""));
      setTheme(String(p.value.entries?.theme ?? ""));
    }
    if (a.status === "fulfilled") setLinks(a.value.links ?? []);
    if (ma.status === "fulfilled") {
      setAccounts(ma.value.accounts ?? []);
      setActiveAccount(String(ma.value.active ?? ""));
    }
    if (c.status === "fulfilled") setCatalog(c.value.providers ?? []);
    const failed = rows.find((r) => r.status === "rejected");
    if (failed && failed.status === "rejected") {
      setError(errText(failed.reason, "Operator admin failed to load"));
    } else {
      setError(null);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const refreshPrefsLinks = async () => {
    try {
      const [p, a] = await Promise.all([getOperatorPrefs(), listAuthLinks()]);
      setPrefs(p.entries ?? {});
      setFont(String(p.entries?.font ?? ""));
      setTheme(String(p.entries?.theme ?? ""));
      setLinks(a.links ?? []);
    } catch {
      /* non-fatal: the mutation toast already reported the outcome */
    }
  };

  const runOp = async (verb: string, fn: () => Promise<unknown>, after?: () => Promise<void>) => {
    setBusy(verb);
    setNotice(null);
    setError(null);
    try {
      await fn();
      setNotice(`${verb} settled ✓`);
      if (after) await after();
    } catch (e) {
      setError(errText(e, `${verb} failed`));
    } finally {
      setBusy(null);
    }
  };

  const handlePrefsSet = () =>
    runOp("prefs.set", async () => {
      const res = await setOperatorPrefs({
        ...(font.trim() ? { font: font.trim() } : {}),
        ...(theme.trim() ? { theme: theme.trim() } : {}),
      });
      setPrefs({ ...(res.applied ?? {}) });
      setUnsupported(res.unsupported ?? []);
    });

  const accountIdOf = (a: ModelAccountRow): string =>
    String(a.profile_id ?? a.id ?? "");

  return (
    <div className="flex flex-col gap-4" data-testid="users-admin-page">
      {/* ── Card header + summary stats (upstream nodes summary header) ── */}
      <header className="flex flex-wrap items-start justify-between gap-2 rounded-none border border-border p-4">
        <div>
          <h1 className="text-base font-semibold">Operator admin</h1>
          <p className="text-xs text-muted-foreground" data-testid="users-adapter-label">
            Qubit single-operator adapter (no upstream users module): one
            loopback operator, persisted dashboard prefs, reference-only
            credentials.
          </p>
        </div>
        <button
          type="button"
          data-testid="users-refresh"
          className="h-8 border border-border px-3 text-xs uppercase"
          disabled={loading}
          onClick={() => void load()}
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </header>

      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      {notice && <p className="text-xs text-success">{notice}</p>}

      {/* ── Summary stats (real counts from the loaded stores) ── */}
      <section
        className="flex flex-wrap gap-4 rounded-none border border-border p-4"
        data-testid="users-stats"
        aria-label="Operator summary"
      >
        <div>
          <div className="text-xs uppercase text-muted-foreground">Operators</div>
          <div className="font-mono text-sm" data-testid="users-stat-operators">
            {roster.length}
          </div>
        </div>
        <div>
          <div className="text-xs uppercase text-muted-foreground">Mentionable</div>
          <div className="font-mono text-sm" data-testid="users-stat-mentionable">
            {mentionable.length}
          </div>
        </div>
        <div>
          <div className="text-xs uppercase text-muted-foreground">Credential refs</div>
          <div className="font-mono text-sm" data-testid="users-stat-links">
            {links.length}
          </div>
        </div>
        <div>
          <div className="text-xs uppercase text-muted-foreground">Model accounts</div>
          <div className="font-mono text-sm" data-testid="users-stat-accounts">
            {accounts.length}
          </div>
        </div>
      </section>

      {/* ── Operator identity (read-only; upstream Devices-card groups) ── */}
      <section className="flex flex-col gap-2 rounded-none border border-border p-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h2 className="text-sm font-medium">Operator (read-only)</h2>
            <p className="text-xs text-muted-foreground">
              Paired operator identity and who can be mentioned.
            </p>
          </div>
        </div>
        {loading ? (
          <p className="text-xs text-muted-foreground">Loading…</p>
        ) : (
          <div data-testid="users-operator-card" className="flex flex-col gap-1 text-xs">
            <p>
              Display name:{" "}
              <span data-testid="users-display-name">
                {String(self?.displayName ?? "Qubit Operator")}
              </span>
            </p>
            <p>
              Role: <span data-testid="users-role">{String(self?.role ?? "operator")}</span>
            </p>
            <p>
              Provider/mode: {String(self?.provider ?? "loopback")} /{" "}
              {String(self?.mode ?? "loopback-token")}
            </p>
            <p>
              Roster: {roster.length} operator{roster.length === 1 ? "" : "s"} ·{" "}
              Mentionable: {mentionable.length}
            </p>
            {roster.length > 0 && (
              <ul data-testid="users-roster" className="flex flex-col gap-1">
                {roster.map((op, i) => (
                  <li
                    key={String(op.id ?? i)}
                    className="border border-border px-2 py-1 font-mono text-xs"
                  >
                    {String(op.displayName ?? op.id ?? `operator-${i}`)}
                    <span className="text-muted-foreground">
                      {" "}
                      · {String(op.role ?? "operator")} · paired
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      {/* ── Prefs get/set (font/theme) ── */}
      <section className="flex flex-col gap-2 rounded-none border border-border p-4">
        <h2 className="text-sm font-medium">Preferences (font / theme)</h2>
        <p className="text-xs text-muted-foreground" data-testid="users-prefs-current">
          Current: font {String(prefs.font ?? "system")}, theme{" "}
          {String(prefs.theme ?? "qubit")}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="ua-font" className="text-xs text-muted-foreground">
            Font
          </label>
          <input
            id="ua-font"
            data-testid="users-font"
            className="h-8 border border-border bg-background px-2 text-xs"
            value={font}
            onChange={(e) => setFont(e.target.value)}
          />
          <label htmlFor="ua-theme" className="text-xs text-muted-foreground">
            Theme
          </label>
          <input
            id="ua-theme"
            data-testid="users-theme"
            className="h-8 border border-border bg-background px-2 text-xs"
            value={theme}
            onChange={(e) => setTheme(e.target.value)}
          />
          <button
            type="button"
            data-testid="users-prefs-set"
            className="border border-border px-2 py-1 text-xs"
            disabled={busy !== null}
            onClick={() => void handlePrefsSet()}
          >
            Save prefs
          </button>
        </div>
        {unsupported.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Unsupported keys (not stored): {unsupported.join(", ")}
          </p>
        )}
      </section>

      {/* ── Credentials: auth links, link/unlink, model accounts, catalog ── */}
      <section className="flex flex-col gap-2 rounded-none border border-border p-4">
        <h2 className="text-sm font-medium">Credentials (references only)</h2>
        {links.length === 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="users-auth-links-empty">
            No credential references linked.
          </p>
        ) : (
          <ul data-testid="users-auth-links" className="flex flex-col gap-1">
            {links.map((l, i) => (
              <li key={i} className="border border-border px-2 py-1 font-mono text-xs">
                {JSON.stringify(l)}
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <input
            aria-label="Provider"
            data-testid="users-link-provider"
            className="h-8 border border-border bg-background px-2 text-xs"
            placeholder="provider"
            value={linkProvider}
            onChange={(e) => setLinkProvider(e.target.value)}
          />
          <input
            aria-label="Credential reference"
            data-testid="users-link-keyref"
            className="h-8 border border-border bg-background px-2 font-mono text-xs"
            placeholder="env:NAME or file:/path"
            value={linkKeyRef}
            onChange={(e) => setLinkKeyRef(e.target.value)}
          />
          <input
            aria-label="Label"
            data-testid="users-link-label"
            className="h-8 border border-border bg-background px-2 text-xs"
            placeholder="label (optional)"
            value={linkLabel}
            onChange={(e) => setLinkLabel(e.target.value)}
          />
          <button
            type="button"
            data-testid="users-link"
            className="border border-border px-2 py-1 text-xs"
            disabled={busy !== null || !linkProvider.trim() || !linkKeyRef.trim()}
            onClick={() =>
              void runOp(
                `link ${linkProvider.trim()}`,
                () =>
                  linkAuthProfile(
                    linkProvider.trim(),
                    linkKeyRef.trim(),
                    linkLabel.trim() || undefined,
                    CONFIRM_YES,
                  ),
                refreshPrefsLinks,
              )
            }
          >
            Link
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            aria-label="Unlink provider"
            data-testid="users-unlink-provider"
            className="h-8 border border-border bg-background px-2 text-xs"
            placeholder="provider"
            value={unlinkProvider}
            onChange={(e) => setUnlinkProvider(e.target.value)}
          />
          <input
            aria-label="Entry index"
            data-testid="users-unlink-index"
            className="h-8 w-20 border border-border bg-background px-2 font-mono text-xs"
            value={unlinkIndex}
            onChange={(e) => setUnlinkIndex(e.target.value)}
          />
          <button
            type="button"
            data-testid="users-unlink"
            className="border border-border px-2 py-1 text-xs"
            disabled={busy !== null || !unlinkProvider.trim()}
            onClick={() =>
              void runOp(
                `unlink ${unlinkProvider.trim()}`,
                () =>
                  unlinkAuthProfile(
                    unlinkProvider.trim(),
                    Number(unlinkIndex) || 0,
                    CONFIRM_YES,
                  ),
                refreshPrefsLinks,
              )
            }
          >
            Unlink
          </button>
        </div>

        <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Model accounts
        </h3>
        {accounts.length === 0 ? (
          <p className="text-xs text-muted-foreground">No model accounts registered.</p>
        ) : (
          <ul data-testid="users-model-accounts" className="flex flex-col gap-1">
            {accounts.map((a, i) => (
              <li key={i} className="border border-border px-2 py-1 font-mono text-xs">
                {accountIdOf(a) || JSON.stringify(a)}
                {activeAccount && accountIdOf(a) === activeAccount ? " (active)" : ""}
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <input
            aria-label="Session"
            data-testid="users-select-session"
            className="h-8 border border-border bg-background px-2 text-xs"
            placeholder="session"
            value={selectSession}
            onChange={(e) => setSelectSession(e.target.value)}
          />
          <input
            aria-label="Account id"
            data-testid="users-select-account"
            className="h-8 border border-border bg-background px-2 font-mono text-xs"
            placeholder="profile / account id"
            value={selectAccount}
            onChange={(e) => setSelectAccount(e.target.value)}
          />
          <button
            type="button"
            data-testid="users-select"
            className="border border-border px-2 py-1 text-xs"
            disabled={busy !== null || !selectSession.trim() || !selectAccount.trim()}
            onClick={() =>
              void runOp(`select ${selectAccount.trim()}`, () =>
                selectModelAccount(
                  selectSession.trim(),
                  selectAccount.trim(),
                  CONFIRM_YES,
                ),
              )
            }
          >
            Select account
          </button>
        </div>

        <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Auth-connect catalog
        </h3>
        {catalog.length === 0 ? (
          <p className="text-xs text-muted-foreground">No providers configured.</p>
        ) : (
          <ul data-testid="users-catalog" className="flex flex-col gap-1">
            {catalog.map((c) => (
              <li key={c.provider} className="border border-border px-2 py-1 font-mono text-xs">
                {c.provider}
                {c.keyRef ? ` — ${c.keyRef}` : ""}
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-muted-foreground" data-testid="users-auth-handoff">
          To connect a provider, run in the operator&apos;s own shell:{" "}
          <code className="font-mono">{AUTH_LOGIN_HANDOFF}</code> — Qubit never
          performs the provider dance itself.
        </p>
      </section>

      {/* ── Policy-refused operations ── */}
      <section className="flex flex-col gap-2 rounded-none border border-border p-4">
        <h2 className="text-sm font-medium">Unavailable by policy</h2>
        <p className="text-xs text-muted-foreground">
          Single-operator product: no multi-user store, no avatar/media
          store, no email identity service, no external OAuth service.
        </p>
        <ul className="flex flex-col gap-2">
          {DISABLED_USERS_ACTIONS.map((d) => (
            <li
              key={d.action}
              data-testid={`ua-disabled-${d.action}`}
              className="flex flex-col gap-1 border border-border px-2 py-1.5 opacity-70"
            >
              <div className="flex flex-wrap items-center gap-2">
                <code className="font-mono text-xs">{d.action}</code>
                <button
                  type="button"
                  disabled
                  title={d.reason}
                  aria-disabled="true"
                  className="cursor-not-allowed border border-border px-2 py-1 text-xs"
                >
                  Disabled
                </button>
              </div>
              <p className="text-xs text-muted-foreground" data-testid={`ua-disabled-reason-${d.action}`}>
                Prerequisite: {d.reason}. Counterpart: {d.counterpart}.
              </p>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
