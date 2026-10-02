/**
 * Skills review queue (`/admin/skills-review`).
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

/** Button press IS the confirmation (same pattern as the product pages). */
const CONFIRM_YES = () => true;

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
      await refresh(statusFilter.trim());
      if (selectedId) await handleInspect(selectedId);
    } catch (e) {
      setError(errText(e, `${verb} failed`));
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
    <div className="flex flex-col gap-4" data-testid="skills-review-page">
      <header>
        <h1 className="text-base font-semibold">Skills review queue</h1>
        <p className="text-xs text-muted-foreground">
          Pending verified-skill proposals: inspect, then Apply / Reject /
          Quarantine. Reads via the content half; verdicts via the
          mutations half (owner-gated, button press confirms).
        </p>
      </header>

      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      {notice && <p className="text-xs text-success">{notice}</p>}

      {/* ── Queue filter + list ── */}
      <section className="flex flex-col gap-2 rounded-none border border-border p-4">
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="sr-status" className="text-xs text-muted-foreground">
            Status filter
          </label>
          <input
            id="sr-status"
            data-testid="sr-status-filter"
            className="h-8 border border-border bg-background px-2 text-xs"
            placeholder="e.g. pending (empty = all)"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          />
          <button
            type="button"
            data-testid="sr-filter-apply"
            className="h-8 border border-border px-3 text-xs uppercase"
            onClick={handleFilter}
          >
            Filter
          </button>
          <span className="text-xs text-muted-foreground" data-testid="sr-count">
            {loading ? "Loading…" : `${count} proposal${count === 1 ? "" : "s"}`}
          </span>
          {journal && (
            <span className="text-xs text-muted-foreground" title={journal}>
              journal verified
            </span>
          )}
        </div>

        {loading ? (
          <p className="text-xs text-muted-foreground">Loading proposals…</p>
        ) : proposals.length === 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="sr-empty">
            No proposals in the queue.
          </p>
        ) : (
          <ul className="flex flex-col gap-1" data-testid="sr-list">
            {proposals.map((p) => {
              const id = proposalIdOf(p);
              return (
                <li
                  key={id || JSON.stringify(p).slice(0, 32)}
                  data-testid={`sr-row-${id}`}
                  className="flex flex-wrap items-center gap-2 border border-border px-2 py-1.5"
                >
                  <code className="font-mono text-xs">{id || "(unidentified)"}</code>
                  {typeof p.status === "string" && (
                    <span className="text-xs text-muted-foreground">{p.status}</span>
                  )}
                  <span className="ml-auto flex flex-wrap gap-1">
                    <button
                      type="button"
                      data-testid={`sr-inspect-${id}`}
                      className="border border-border px-2 py-1 text-xs"
                      disabled={inspecting || !id}
                      onClick={() => void handleInspect(id)}
                    >
                      Inspect
                    </button>
                    <button
                      type="button"
                      data-testid={`sr-apply-${id}`}
                      className="border border-border px-2 py-1 text-xs"
                      disabled={busy !== null || !id}
                      onClick={() =>
                        void runVerdict(`apply ${id}`, () =>
                          applyProposal(id, reason.trim() || undefined, CONFIRM_YES),
                        )
                      }
                    >
                      Apply
                    </button>
                    <button
                      type="button"
                      data-testid={`sr-reject-${id}`}
                      className="border border-border px-2 py-1 text-xs"
                      disabled={busy !== null || !id}
                      onClick={() =>
                        void runVerdict(`reject ${id}`, () =>
                          rejectProposal(id, reason.trim() || undefined, CONFIRM_YES),
                        )
                      }
                    >
                      Reject
                    </button>
                    <button
                      type="button"
                      data-testid={`sr-quarantine-${id}`}
                      className="border border-border px-2 py-1 text-xs"
                      disabled={busy !== null || !id}
                      onClick={() =>
                        void runVerdict(`quarantine ${id}`, () =>
                          quarantineProposal(id, CONFIRM_YES),
                        )
                      }
                    >
                      Quarantine
                    </button>
                  </span>
                </li>
              );
            })}
          </ul>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="sr-reason" className="text-xs text-muted-foreground">
            Verdict reason (optional)
          </label>
          <input
            id="sr-reason"
            data-testid="sr-reason"
            className="h-8 flex-1 border border-border bg-background px-2 text-xs"
            placeholder="recorded on the verdict"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
      </section>

      {/* ── Inspect detail + revise / update-staged / request-revision ── */}
      {selected && selectedId && (
        <section
          className="flex flex-col gap-2 rounded-none border border-border p-4"
          data-testid="sr-detail"
        >
          <h2 className="text-sm font-medium">
            Inspect: <code className="font-mono">{selectedId}</code>
          </h2>
          <pre className="max-h-64 overflow-auto border border-border bg-background/40 p-2 font-mono text-xs">
            {JSON.stringify(selected, null, 2)}
          </pre>
          <div className="flex flex-wrap gap-1">
            <button
              type="button"
              data-testid="sr-events-open"
              className="border border-border px-2 py-1 text-xs"
              onClick={() => void openEvents(selectedId)}
            >
              Events
            </button>
            <button
              type="button"
              data-testid="sr-detail-close"
              className="border border-border px-2 py-1 text-xs"
              onClick={() => {
                setSelected(null);
                setSelectedId(null);
              }}
            >
              Close
            </button>
          </div>

          <label htmlFor="sr-payload" className="text-xs text-muted-foreground">
            Revision payload (JSON object — used by Revise and Update-staged)
          </label>
          <textarea
            id="sr-payload"
            data-testid="sr-payload"
            className="min-h-[80px] border border-border bg-background px-2 py-1 font-mono text-xs"
            value={revisePayload}
            onChange={(e) => setRevisePayload(e.target.value)}
          />
          <div className="flex flex-wrap gap-1">
            <button
              type="button"
              data-testid="sr-revise"
              className="border border-border px-2 py-1 text-xs"
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
            </button>
            <button
              type="button"
              data-testid="sr-update-staged"
              className="border border-border px-2 py-1 text-xs"
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
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor="sr-rev-session" className="text-xs text-muted-foreground">
              Session
            </label>
            <input
              id="sr-rev-session"
              data-testid="sr-revision-session"
              className="h-8 border border-border bg-background px-2 text-xs"
              value={revisionSession}
              onChange={(e) => setRevisionSession(e.target.value)}
            />
            <input
              id="sr-rev-text"
              data-testid="sr-revision-text"
              className="h-8 flex-1 border border-border bg-background px-2 text-xs"
              placeholder="revision instructions (required)"
              value={revisionText}
              onChange={(e) => setRevisionText(e.target.value)}
            />
            <button
              type="button"
              data-testid="sr-request-revision"
              className="border border-border px-2 py-1 text-xs"
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
            </button>
          </div>
        </section>
      )}

      {/* ── Events drawer ── */}
      {eventsOpen && (
        <section
          className="flex flex-col gap-2 rounded-none border border-border p-4"
          data-testid="sr-events-drawer"
          role="complementary"
          aria-label="Proposal events"
        >
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium">Events journal</h2>
            <button
              type="button"
              data-testid="sr-events-close"
              className="border border-border px-2 py-1 text-xs"
              onClick={() => setEventsOpen(false)}
            >
              Close
            </button>
          </div>
          {eventsLoading ? (
            <p className="text-xs text-muted-foreground">Loading events…</p>
          ) : events.length === 0 ? (
            <p className="text-xs text-muted-foreground">No events recorded.</p>
          ) : (
            <ul className="flex max-h-64 flex-col gap-1 overflow-auto">
              {events.map((ev, i) => (
                <li
                  key={`${ev.sequence}-${i}`}
                  className="border border-border px-2 py-1 font-mono text-xs"
                >
                  #{ev.sequence} {ev.action} — {JSON.stringify(ev.payload)}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {/* ── Curator pin / unpin / restore (confirm) ── */}
      <section className="flex flex-col gap-2 rounded-none border border-border p-4">
        <h2 className="text-sm font-medium">Curator</h2>
        <p className="text-xs text-muted-foreground">
          Owner-gated pin primitives for the verified-skill collection. Button
          press confirms.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="sr-curator-skill" className="text-xs text-muted-foreground">
            Skill id
          </label>
          <input
            id="sr-curator-skill"
            data-testid="sr-curator-skill"
            className="h-8 border border-border bg-background px-2 font-mono text-xs"
            value={curatorSkill}
            onChange={(e) => setCuratorSkill(e.target.value)}
          />
          <button
            type="button"
            data-testid="sr-curator-pin"
            className="border border-border px-2 py-1 text-xs"
            disabled={busy !== null || !curatorSkill.trim()}
            onClick={() =>
              void runVerdict(`curator pin ${curatorSkill.trim()}`, () =>
                curatorPin(curatorSkill.trim(), CONFIRM_YES),
              )
            }
          >
            Pin
          </button>
          <button
            type="button"
            data-testid="sr-curator-unpin"
            className="border border-border px-2 py-1 text-xs"
            disabled={busy !== null || !curatorSkill.trim()}
            onClick={() =>
              void runVerdict(`curator unpin ${curatorSkill.trim()}`, () =>
                curatorUnpin(curatorSkill.trim(), CONFIRM_YES),
              )
            }
          >
            Unpin
          </button>
          <button
            type="button"
            data-testid="sr-curator-restore"
            className="border border-border px-2 py-1 text-xs"
            disabled={busy !== null || !curatorSkill.trim()}
            onClick={() =>
              void runVerdict(`curator restore ${curatorSkill.trim()}`, () =>
                curatorRestore(curatorSkill.trim(), CONFIRM_YES),
              )
            }
          >
            Restore
          </button>
        </div>
      </section>

      {/* ── Policy-refused operations: disabled with the exact prerequisite ── */}
      <section className="flex flex-col gap-2 rounded-none border border-border p-4">
        <h2 className="text-sm font-medium">Unavailable by policy</h2>
        <p className="text-xs text-muted-foreground">
          Qubit runs no upload store, no automated evaluator, no
          history scanner, and no remote verdict registry. These stay
          disabled; the reason names the exact prerequisite.
        </p>
        <ul className="flex flex-col gap-2">
          {DISABLED_SKILLS_ACTIONS.map((d) => (
            <li
              key={d.action}
              data-testid={`sr-disabled-${d.action}`}
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
              <p className="text-xs text-muted-foreground" data-testid={`sr-disabled-reason-${d.action}`}>
                Prerequisite: {d.reason}. Counterpart: {d.counterpart}.
              </p>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
