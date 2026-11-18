/**
 * Skills review queue (`/admin/skills-review`).
 *
 * Structure mirrors the ported settings surfaces in this app
 * (`.settings-page` > `.settings-section` > `.settings-group` > `.settings-row`),
 * following the lifecycle tabs + proposal queue + inspect detail + revision
 * dialog of the skill workshop board, rebranded to Qubit voice.
 *
 * The verified-skill proposals surface the main Skills page intentionally
 * does not cover: the pending queue (list/inspect rows), the owner-gated
 * verdicts (Apply / Reject / Quarantine / Revise / Update-staged /
 * Request-revision), the shared-journal events drawer, and the curator
 * pin/unpin/restore primitives. Upload, automated evaluation,
 * history-scan, and remote security verdicts are policy-refused backend
 * operations: they render disabled with the exact prerequisite.
 */
import { useCallback, useEffect, useState } from "react";
import {
  DISABLED_SKILLS_ACTIONS,
  applyProposal,
  curatorPin,
  curatorRestore,
  curatorUnpin,
  inspectProposal,
  listProposalEvents,
  listProposals,
  proposalIdOf,
  proposalsHistoryStatus,
  quarantineProposal,
  rejectProposal,
  requestProposalRevision,
  reviseProposal,
  updateStagedProposal,
  type ProposalEvent,
  type SkillProposal,
} from "@/lib/ocFamilyAdminApi";
import { refusalOrError } from "@/lib/policy-refusal";
import { Button, Input, Tabs, emitFx } from "@/sakuga/ui";

/** Button press IS the confirmation (same pattern as the product pages). */
const CONFIRM_YES = () => true;

/**
 * Lifecycle tabs mirror the workshop board tabs. "all" maps to the
 * empty filter (unfiltered queue).
 */
const STATUS_TABS = [
  { id: "all", label: "All", status: "" },
  { id: "pending", label: "Pending", status: "pending" },
  { id: "applied", label: "Applied", status: "applied" },
  { id: "rejected", label: "Rejected", status: "rejected" },
  { id: "quarantined", label: "Quarantined", status: "quarantined" },
  { id: "stale", label: "Stale", status: "stale" },
] as const;

function statusChipClass(status: unknown): string {
  if (status === "applied") return "text-success";
  if (status === "pending") return "text-warning";
  if (status === "rejected" || status === "quarantined" || status === "stale") {
    return "text-destructive";
  }
  return "text-muted-foreground";
}

function errText(e: unknown, fallback: string): string {
  return refusalOrError(e, `${fallback}: ${e instanceof Error ? e.message : String(e)}`);
}



export default function SkillsReviewPage() {
  const [statusFilter, setStatusFilter] = useState("");
  const [proposals, setProposals] = useState<SkillProposal[]>([]);
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<SkillProposal | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const [eventsOpen, setEventsOpen] = useState(false);
  const [events, setEvents] = useState<ProposalEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [journal, setJournal] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [revisePayload, setRevisePayload] = useState("{}");
  const [revisionSession, setRevisionSession] = useState("qubit");
  const [revisionText, setRevisionText] = useState("");
  const [curatorSkill, setCuratorSkill] = useState("");

  const refresh = useCallback(async (status: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await listProposals(status || undefined);
      setProposals(res.proposals ?? []);
      setCount(res.count ?? (res.proposals ?? []).length);
    } catch (e) {
      setError(errText(e, "Proposals queue failed to load"));
      setProposals([]);
      setCount(0);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh("");
    proposalsHistoryStatus()
      .then((r) => setJournal(JSON.stringify(r.journal ?? r)))
      .catch(() => setJournal(null));
  }, [refresh]);

  const handleFilter = () => void refresh(statusFilter.trim());

  const handleTab = (status: string) => {
    setStatusFilter(status);
    void refresh(status);
  };

  const activeTab = (status: string): boolean =>
    (statusFilter.trim() || "") === status;

  const handleInspect = async (id: string) => {
    setInspecting(true);
    setError(null);
    try {
      const res = await inspectProposal(id);
      setSelectedId(id);
      setSelected(res.proposal);
    } catch (e) {
      setError(errText(e, `Inspect ${id} failed`));
    } finally {
      setInspecting(false);
    }
  };

  const runVerdict = async (
    verb: string,
    fn: () => Promise<unknown>,
  ) => {
    setBusy(verb);
    setNotice(null);
    setError(null);
    try {
      await fn();
      setNotice(`${verb} settled ✓`);
      emitFx("success");
      await refresh(statusFilter.trim());
      if (selectedId) await handleInspect(selectedId);
    } catch (e) {
      setError(errText(e, `${verb} failed`));
      emitFx("error");
    } finally {
      setBusy(null);
    }
  };

  const openEvents = async (proposalId?: string) => {
    setEventsOpen(true);
    setEventsLoading(true);
    try {
      const res = await listProposalEvents({
        proposalId: proposalId || undefined,
        limit: 100,
      });
      setEvents(res.events ?? []);
    } catch (e) {
      setError(errText(e, "Events journal failed to load"));
      setEvents([]);
    } finally {
      setEventsLoading(false);
    }
  };

  const parsePayload = (raw: string): Record<string, unknown> | null => {
    try {
      const v: unknown = JSON.parse(raw.trim() || "{}");
      if (v && typeof v === "object" && !Array.isArray(v)) {
        return v as Record<string, unknown>;
      }
      return null;
    } catch {
      return null;
    }
  };

  return (
    <div className="settings-page settings-page--wide" data-testid="skills-review-page">
      <p className="settings-page__intro">
        Pending verified-skill proposals: inspect, then Apply / Reject / Quarantine.
        Reads via the content half; verdicts via the mutations half (owner-gated,
        button press confirms).
      </p>

      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      {notice && <p className="text-xs text-success">{notice}</p>}

      {/* ── Queue: lifecycle tabs + filter row + rows ── */}
      <section className="settings-section">
        <div className="settings-section__header">
          <div className="settings-section__copy">
            <h2 className="settings-section__heading">
              Proposals <span className="settings-count">{loading ? "…" : count}</span>
            </h2>
            <p className="settings-section__desc">
              Filter the queue by lifecycle status. Inspect a row to act on it.
            </p>
          </div>
          <div className="settings-section__actions">
            <Button
              data-testid="sr-refresh"
              disabled={loading}
              onClick={() => void refresh(statusFilter.trim())}
            >
              {loading ? "Loading…" : "Refresh"}
            </Button>
          </div>
        </div>

        <div className="settings-group">
          {/* Lifecycle tabs */}
          <div className="settings-row settings-row--stacked" role="tablist" aria-label="Proposal status">
            <div className="settings-row__text">
              <div className="settings-row__title">Status</div>
            </div>
            <div className="settings-row__control">
              <Tabs
                label="Proposal status"
                options={STATUS_TABS.map((tab) => ({ value: tab.status, label: tab.label }))}
                selected={Math.max(0, STATUS_TABS.findIndex((tab) => activeTab(tab.status)))}
                onChange={(detail) => void handleTab(detail.value)}
                data-testid="sr-tabs"
              />
            </div>
          </div>

          <div className="settings-row">
            <div className="settings-row__text">
              <label htmlFor="sr-status" className="settings-row__title">
                Status filter
              </label>
              <p className="settings-row__desc">e.g. pending (empty = all)</p>
            </div>
            <div className="settings-row__control">
              <Input
                id="sr-status"
                data-testid="sr-status-filter"
                label="Status filter"
                placeholder="e.g. pending (empty = all)"
                value={statusFilter}
                onChange={setStatusFilter}
                className="qsk-field"
              />
              <Button data-testid="sr-filter-apply" onClick={handleFilter}>
                Filter
              </Button>
              <span className="text-xs text-muted-foreground" data-testid="sr-count">
                {loading ? "Loading…" : `${count} shown`}
              </span>
              {journal && (
                <span className="text-xs text-muted-foreground" title={journal}>
                  journal verified
                </span>
              )}
            </div>
          </div>

          {loading ? (
            <div className="settings-empty">Loading proposals…</div>
          ) : proposals.length === 0 ? (
            <div className="settings-empty" data-testid="sr-empty">
              No proposals in the queue.
            </div>
          ) : (
            <ul className="flex flex-col" data-testid="sr-list">
              {proposals.map((p) => {
                const id = proposalIdOf(p);
                return (
                  <li
                    key={id || JSON.stringify(p).slice(0, 32)}
                    data-testid={`sr-row-${id}`}
                    className="settings-row"
                  >
                    <div className="settings-row__text">
                      <div className="settings-row__title font-mono">
                        {id || "(unidentified)"}
                      </div>
                      {typeof p.status === "string" && (
                        <p className={`settings-row__desc ${statusChipClass(p.status)}`}>
                          {p.status}
                        </p>
                      )}
                    </div>
                    <div className="settings-row__control">
                      <Button
                        variant="secondary"
                        data-testid={`sr-inspect-${id}`}
                        disabled={inspecting || !id}
                        onClick={() => void handleInspect(id)}
                      >
                        Inspect
                      </Button>
                      <Button
                        data-testid={`sr-apply-${id}`}
                        disabled={busy !== null || !id}
                        onClick={() =>
                          void runVerdict(`apply ${id}`, () =>
                            applyProposal(id, reason.trim() || undefined, CONFIRM_YES),
                          )
                        }
                      >
                        Apply
                      </Button>
                      <Button
                        variant="secondary"
                        data-testid={`sr-reject-${id}`}
                        disabled={busy !== null || !id}
                        onClick={() =>
                          void runVerdict(`reject ${id}`, () =>
                            rejectProposal(id, reason.trim() || undefined, CONFIRM_YES),
                          )
                        }
                      >
                        Reject
                      </Button>
                      <Button
                        variant="secondary"
                        data-testid={`sr-quarantine-${id}`}
                        disabled={busy !== null || !id}
                        onClick={() =>
                          void runVerdict(`quarantine ${id}`, () =>
                            quarantineProposal(id, CONFIRM_YES),
                          )
                        }
                      >
                        Quarantine
                      </Button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}

          <div className="settings-row">
            <div className="settings-row__text">
              <label htmlFor="sr-reason" className="settings-row__title">
                Verdict reason
              </label>
              <p className="settings-row__desc">Optional; recorded on the verdict.</p>
            </div>
            <div className="settings-row__control">
              <Input
                id="sr-reason"
                data-testid="sr-reason"
                label="Verdict reason"
                placeholder="recorded on the verdict"
                value={reason}
                onChange={setReason}
                className="qsk-field"
              />
            </div>
          </div>
        </div>
      </section>

      {/* ── Inspect detail ── */}
      {selected && selectedId && (
        <section className="settings-section" data-testid="sr-detail">
          <div className="settings-section__header">
            <div className="settings-section__copy">
              <h2 className="settings-section__heading flex items-center gap-2">
                Inspect: <code className="font-mono">{selectedId}</code>
                {typeof selected.status === "string" && (
                  <span className={`text-xs ${statusChipClass(selected.status)}`}>
                    {selected.status}
                  </span>
                )}
              </h2>
            </div>
            <div className="settings-section__actions">
              <Button
                variant="secondary"
                data-testid="sr-events-open"
                onClick={() => void openEvents(selectedId)}
              >
                Events
              </Button>
              <Button
                variant="secondary"
                data-testid="sr-detail-close"
                onClick={() => {
                  setSelected(null);
                  setSelectedId(null);
                }}
              >
                Close
              </Button>
            </div>
          </div>

          <div className="settings-group">
            <div className="settings-row settings-row--stacked">
              <div className="settings-row__text">
                <div className="settings-row__title">Proposal</div>
              </div>
              <div className="settings-row__control">
                <pre className="max-h-64 w-full overflow-auto border border-border bg-background/40 p-2 text-left font-mono text-xs">
                  {JSON.stringify(selected, null, 2)}
                </pre>
              </div>
            </div>

            <div className="settings-row settings-row--stacked">
              <div className="settings-row__text">
                <label htmlFor="sr-payload" className="settings-row__title">
                  Revision payload
                </label>
                <p className="settings-row__desc">
                  JSON object — used by Revise and Update-staged.
                </p>
              </div>
              <div className="settings-row__control">
                <textarea
                  id="sr-payload"
                  data-testid="sr-payload"
                  className="min-h-[80px] w-full border border-border bg-background px-2 py-1 font-mono text-xs"
                  value={revisePayload}
                  onChange={(e) => setRevisePayload(e.target.value)}
                />
              </div>
            </div>

            <div className="settings-row settings-row--actions">
              <div className="settings-row__text">
                <div className="settings-row__title">Revision writes</div>
              </div>
              <div className="settings-row__control">
                <Button
                  data-testid="sr-revise"
                  disabled={busy !== null}
                  onClick={() => {
                    const payload = parsePayload(revisePayload);
                    if (!payload) {
                      setError("Revise needs a JSON object payload");
                      return;
                    }
                    void runVerdict(`revise ${selectedId}`, () =>
                      reviseProposal(selectedId, payload, CONFIRM_YES),
                    );
                  }}
                >
                  Revise
                </Button>
                <Button
                  variant="secondary"
                  data-testid="sr-update-staged"
                  disabled={busy !== null}
                  onClick={() => {
                    const payload = parsePayload(revisePayload);
                    if (!payload) {
                      setError("Update-staged needs a JSON object payload");
                      return;
                    }
                    void runVerdict(`update-staged ${selectedId}`, () =>
                      updateStagedProposal(selectedId, payload, CONFIRM_YES),
                    );
                  }}
                >
                  Update-staged
                </Button>
              </div>
            </div>

            <div className="settings-row">
              <div className="settings-row__text">
                <label htmlFor="sr-rev-session" className="settings-row__title">
                  Request revision
                </label>
                <p className="settings-row__desc">Instructions are required.</p>
              </div>
              <div className="settings-row__control">
                <Input
                  id="sr-rev-session"
                  data-testid="sr-revision-session"
                  label="Revision session"
                  className="qsk-field font-mono"
                  value={revisionSession}
                  onChange={setRevisionSession}
                />
                <Input
                  id="sr-rev-text"
                  data-testid="sr-revision-text"
                  label="Revision instructions"
                  className="qsk-field"
                  placeholder="revision instructions (required)"
                  value={revisionText}
                  onChange={setRevisionText}
                />
                <Button
                  variant="secondary"
                  data-testid="sr-request-revision"
                  disabled={busy !== null || !revisionText.trim()}
                  onClick={() =>
                    void runVerdict(`request-revision ${selectedId}`, () =>
                      requestProposalRevision(
                        selectedId,
                        revisionSession.trim() || "qubit",
                        revisionText.trim(),
                        CONFIRM_YES,
                      ),
                    )
                  }
                >
                  Request-revision
                </Button>
              </div>
            </div>
          </div>
        </section>
      )}

      {/* ── Events drawer ── */}
      {eventsOpen && (
        <section
          className="settings-section"
          data-testid="sr-events-drawer"
          role="complementary"
          aria-label="Proposal events"
        >
          <div className="settings-section__header">
            <div className="settings-section__copy">
              <h2 className="settings-section__heading">Events journal</h2>
            </div>
            <div className="settings-section__actions">
              <Button
                variant="secondary"
                data-testid="sr-events-close"
                onClick={() => setEventsOpen(false)}
              >
                Close
              </Button>
            </div>
          </div>
          <div className="settings-group">
            {eventsLoading ? (
              <div className="settings-empty">Loading events…</div>
            ) : events.length === 0 ? (
              <div className="settings-empty">No events recorded.</div>
            ) : (
              <ul className="flex max-h-64 flex-col overflow-auto">
                {events.map((ev, i) => (
                  <li
                    key={`${ev.sequence}-${i}`}
                    className="border-b border-border px-2 py-1 font-mono text-xs"
                  >
                    #{ev.sequence} {ev.action} — {JSON.stringify(ev.payload)}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}

      {/* ── Curator pin / unpin / restore (confirm) ── */}
      <section className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Curator</h2>
          <p className="settings-section__desc">
            Owner-gated pin primitives for the verified-skill collection. Button press
            confirms.
          </p>
        </div>
        <div className="settings-group">
          <div className="settings-row">
            <div className="settings-row__text">
              <label htmlFor="sr-curator-skill" className="settings-row__title">
                Skill id
              </label>
            </div>
            <div className="settings-row__control">
              <Input
                id="sr-curator-skill"
                data-testid="sr-curator-skill"
                label="Skill id"
                className="qsk-field font-mono"
                value={curatorSkill}
                onChange={setCuratorSkill}
              />
              <Button
                data-testid="sr-curator-pin"
                disabled={busy !== null || !curatorSkill.trim()}
                onClick={() =>
                  void runVerdict(`curator pin ${curatorSkill.trim()}`, () =>
                    curatorPin(curatorSkill.trim(), CONFIRM_YES),
                  )
                }
              >
                Pin
              </Button>
              <Button
                variant="secondary"
                data-testid="sr-curator-unpin"
                disabled={busy !== null || !curatorSkill.trim()}
                onClick={() =>
                  void runVerdict(`curator unpin ${curatorSkill.trim()}`, () =>
                    curatorUnpin(curatorSkill.trim(), CONFIRM_YES),
                  )
                }
              >
                Unpin
              </Button>
              <Button
                variant="secondary"
                data-testid="sr-curator-restore"
                disabled={busy !== null || !curatorSkill.trim()}
                onClick={() =>
                  void runVerdict(`curator restore ${curatorSkill.trim()}`, () =>
                    curatorRestore(curatorSkill.trim(), CONFIRM_YES),
                  )
                }
              >
                Restore
              </Button>
            </div>
          </div>
        </div>
      </section>

      {/* ── Policy-refused operations: disabled with the exact prerequisite ── */}
      <section className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">
            Unavailable by policy{" "}
            <span className="settings-count">{DISABLED_SKILLS_ACTIONS.length}</span>
          </h2>
          <p className="settings-section__desc">
            Qubit runs no upload store, no automated evaluator, no history scanner, and
            no remote verdict registry. These stay disabled; the reason names the exact
            prerequisite.
          </p>
        </div>
        <div className="settings-group">
          {DISABLED_SKILLS_ACTIONS.map((d) => (
            <div
              key={d.action}
              data-testid={`sr-disabled-${d.action}`}
              className="settings-row settings-row--stacked"
              data-state="refused"
            >
              <div className="settings-row__text">
                <div className="settings-row__title font-mono">{d.action}</div>
                <p
                  className="settings-row__desc"
                  data-testid={`sr-disabled-reason-${d.action}`}
                >
                  Prerequisite: {d.reason}. Counterpart: {d.counterpart}.
                </p>
              </div>
              <div className="settings-row__control">
                <Button disabled title={d.reason} aria-label={`${d.action} unavailable: ${d.reason}`}>
                  Disabled
                </Button>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
