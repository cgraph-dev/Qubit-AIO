/* War Room / Idea Lab + the role/floor directory.
 *
 * The panel is a view over durable Business OS rows. It does not generate an
 * idea, research, evidence or a decision: the operator adds sources and
 * evidence, reads the recorded debate/dissent, and records the owner decision.
 * An empty store is a named empty state; a refusal shows its type and code.
 *
 * The directory tab renders the shipped TEMPLATES (145 roles, 5 floors) and
 * never dresses a template as a staffed agent.
 */
import type { OfficeGameShellProps } from "../OfficeGameShell";
import {
  type BusinessDirectory, type BusinessOverview,
  type WarRoomBundle, type WarRoomSession,
  BusinessApiError, addWarRoomEvidence, addWarRoomSource,
  createWarRoomSession, createWarRoomTasks, fetchDirectory, fetchOverview,
  fetchWarRoomBundle, fetchWarRoomSessions, newIdempotencyKey,
  recordWarRoomDecision, researchWarRoomSession, startEtsyPilot,
} from "./business-api";
import { createTowerNavigator } from "./TowerNavigator";
import { type WorldTower, fetchWorldTower } from "./world-tower";

export interface WarRoomPanelProps {
  base?: string;
  fetchImpl?: typeof fetch;
  onClose?: () => void;
  /** For tests/host: preselect a session instead of the newest. */
  sessionId?: string | null;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; overview: BusinessOverview; sessions: WarRoomSession[];
      tenantId: string; businessId: string | null }
  | { kind: "empty"; message: string }
  | { kind: "error"; message: string; code?: string };

function errText(exc: unknown): { message: string; code?: string } {
  if (exc instanceof BusinessApiError) {
    return { message: exc.message, code: exc.code };
  }
  return { message: exc instanceof Error ? exc.message : String(exc) };
}

const LABEL_COLORS: Record<string, string> = {
  FACT: "#4ade80",
  INFERENCE: "#60a5fa",
  ASSUMPTION: "#facc15",
  UNKNOWN: "#a1a1aa",
};

function chip(R: OfficeGameShellProps["React"], text: string, key: string,
              color?: string) {
  return R.createElement("span", {
    key, className: "warroom__chip",
    "data-testid": "warroom-chip",
    style: color ? { borderColor: color, color } : undefined,
  }, text);
}

function section(R: OfficeGameShellProps["React"], id: string, title: string,
                 children: unknown[]) {
  return R.createElement("section", {
    key: id, className: "warroom__section",
    "data-testid": `warroom-section-${id}`,
  }, [
    R.createElement("h4", { key: "h", className: "warroom__sectionTitle" },
      title),
    ...children,
  ]);
}

function input(R: OfficeGameShellProps["React"], key: string, props: Record<string, unknown>) {
  return R.createElement("input", {
    key, className: "warroom__input", autoComplete: "off", ...props,
  });
}

export function createWarRoomPanel(
  R: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
) {
  const { useState, useEffect } = hooks;
  // Built ONCE per War Room instance: a factory called inside the render body
  // would return a new component type every render and remount the tower
  // (the same class of bug fixed for the HUD/agents docks).
  const TowerNavigator = createTowerNavigator(R, hooks);

  return function WarRoomPanel(props: WarRoomPanelProps) {
    const [tab, setTab] = useState<"idea" | "directory">("idea");
    const [load, setLoad] = useState<LoadState>({ kind: "loading" });
    const [selectedId, setSelectedId] = useState<string | null>(
      props.sessionId ?? null);
    const [bundle, setBundle] = useState<WarRoomBundle | null>(null);
    const [directory, setDirectory] = useState<BusinessDirectory | null>(null);
    const [world, setWorld] = useState<WorldTower | null>(null);
    const [title, setTitle] = useState("");
    const [sourceKind, setSourceKind] = useState("note");
    const [sourceLocator, setSourceLocator] = useState("");
    const [evidenceLabel, setEvidenceLabel] = useState("INFERENCE");
    const [evidenceClaim, setEvidenceClaim] = useState("");
    const [evidenceSource, setEvidenceSource] = useState("");
    const [researchQuery, setResearchQuery] = useState("");
    const [decisionNote, setDecisionNote] = useState("");
    const [busy, setBusy] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const opts = { base: props.base, fetchImpl: props.fetchImpl };

    const loadSessions = async (tenantId: string, businessId: string | null) => {
      const listed = await fetchWarRoomSessions(
        { tenant_id: tenantId, business_id: businessId ?? undefined }, opts);
      return listed.sessions;
    };

    useEffect(() => {
      let live = true;
      const ctl = typeof AbortController === "undefined" ? undefined
        : new AbortController();
      (async () => {
        try {
          const overview = await fetchOverview({ ...opts, signal: ctl?.signal });
          const tenantId = overview.default_tenant_id;
          if (!tenantId) {
            if (live) setLoad({
              kind: "empty",
              message: "No Business OS tenant yet. Opening the first idea "
                + "creates the local Personal workspace.",
            });
            return;
          }
          const businessId = overview.default_business_id;
          const sessions = await loadSessions(tenantId, businessId);
          if (!live) return;
          setLoad({ kind: "ready", overview, sessions, tenantId, businessId });
          setSelectedId((current) => current ?? sessions[0]?.session_id ?? null);
        } catch (exc) {
          if (!live) return;
          const { message, code } = errText(exc);
          setLoad({ kind: "error", message, code });
        }
      })();
      return () => { live = false; ctl?.abort(); };
    }, [props.base, props.fetchImpl]);

    // The directory is a template catalog; load it only when opened.
    useEffect(() => {
      if (tab !== "directory" || directory) return;
      let live = true;
      const ctl = typeof AbortController === "undefined" ? undefined
        : new AbortController();
      (async () => {
        try {
          const next = await fetchDirectory({ ...opts, signal: ctl?.signal });
          if (live) setDirectory(next);
        } catch (exc) {
          if (!live) return;
          const { message, code } = errText(exc);
          setError(code ? `${code}: ${message}` : message);
        }
      })();
      return () => { live = false; ctl?.abort(); };
    }, [tab, directory, props.base, props.fetchImpl]);

    // The room scene is an enhancement over the directory: the navigator
    // already renders template rooms, so an absent/malformed endpoint simply
    // leaves the explicit template fallback in place.
    useEffect(() => {
      if (tab !== "directory" || !directory) return;
      let live = true;
      const ctl = typeof AbortController === "undefined" ? undefined
        : new AbortController();
      (async () => {
        const next = await fetchWorldTower(directory.floors, {
          ...opts, signal: ctl?.signal,
        });
        if (live) setWorld(next);
      })();
      return () => { live = false; ctl?.abort(); };
    }, [tab, directory, props.base, props.fetchImpl]);

    useEffect(() => {
      if (!selectedId || load.kind !== "ready") {
        setBundle(null);
        return;
      }
      let live = true;
      const ctl = typeof AbortController === "undefined" ? undefined
        : new AbortController();
      (async () => {
        try {
          const next = await fetchWarRoomBundle(selectedId, {
            tenant_id: load.tenantId,
          }, { ...opts, signal: ctl?.signal });
          if (live) setBundle(next);
        } catch (exc) {
          if (!live) return;
          const { message, code } = errText(exc);
          setError(code ? `${code}: ${message}` : message);
        }
      })();
      return () => { live = false; ctl?.abort(); };
    }, [selectedId, load.kind, props.base, props.fetchImpl,
        load.kind === "ready" ? load.tenantId : ""]);

    const run = async (
      label: string, fn: () => Promise<string | void>,
    ) => {
      setBusy(label);
      setError(null);
      setNotice(null);
      try {
        const explicit = await fn();
        // Re-read the real scope and rows after every mutation. This also
        // promotes an empty store to a ready one when the first idea creates
        // the local Personal workspace.
        const overview = await fetchOverview(opts);
        const tenantId = overview.default_tenant_id;
        if (!tenantId) {
          setLoad({ kind: "empty", message:
            "No Business OS tenant yet. Opening the first idea creates the "
            + "local Personal workspace." });
          setNotice(`${label} recorded.`);
          return;
        }
        const businessId = overview.default_business_id;
        const sessions = await loadSessions(tenantId, businessId);
        setLoad({ kind: "ready", overview, sessions, tenantId, businessId });
        const nextSelected = (typeof explicit === "string" && explicit)
          ? explicit
          : selectedId ?? sessions[0]?.session_id ?? null;
        if (nextSelected) {
          setSelectedId(nextSelected);
          setBundle(await fetchWarRoomBundle(nextSelected, {
            tenant_id: tenantId,
          }, opts));
        }
        setNotice(`${label} recorded.`);
      } catch (exc) {
        const { message, code } = errText(exc);
        setError(code ? `${code}: ${message}` : message);
      } finally {
        setBusy(null);
      }
    };

    const header = R.createElement("div", {
      key: "head", className: "office-game__dockHead office-theme-dock-head",
    }, [
      R.createElement("span", { key: "t", className: "office-theme-dock-title" },
        "War Room / Idea Lab"),
      R.createElement("button", {
        key: "x", type: "button",
        className: "office-game__btn office-theme-dock-close",
        "data-testid": "warroom-close", "aria-label": "Close War Room",
        onClick: () => props.onClose?.(),
      }, "Close"),
    ]);

    const children: unknown[] = [header];
    children.push(R.createElement("div", {
      key: "tabs", className: "warroom__tabs", role: "tablist",
    }, [
      R.createElement("button", {
        key: "idea", type: "button", role: "tab",
        "aria-selected": tab === "idea" ? "true" : "false",
        "data-testid": "warroom-tab-idea",
        className: "warroom__tab" + (tab === "idea" ? " is-active" : ""),
        onClick: () => setTab("idea"),
      }, "Idea Lab"),
      R.createElement("button", {
        key: "directory", type: "button", role: "tab",
        "aria-selected": tab === "directory" ? "true" : "false",
        "data-testid": "warroom-tab-directory",
        className: "warroom__tab" + (tab === "directory" ? " is-active" : ""),
        onClick: () => setTab("directory"),
      }, "Roles & floors"),
    ]));

    if (tab === "directory") {
      if (!directory) {
        children.push(R.createElement("div", {
          key: "dloading", className: "warroom__state",
          "data-testid": "warroom-directory-loading", role: "status",
        }, "Loading the role/floor catalog…"));
      } else {
        children.push(R.createElement("div", {
          key: "dcounts", className: "warroom__counts",
          "data-testid": "warroom-directory-counts",
        }, `${directory.counts.departments} departments · `
          + `${directory.counts.roles} roles · `
          + `${directory.counts.floors} floors (`
          + `${directory.counts.active_floors} active / `
          + `${directory.counts.dormant_floors} dormant) · `
          + `${directory.counts.wings} wings`));
        children.push(section(R, "departments", "Departments", [
          R.createElement("ul", {
            key: "list", className: "warroom__list",
            "data-testid": "warroom-departments",
          }, directory.departments.map((dept) =>
            R.createElement("li", {
              key: dept.id, "data-testid": "warroom-department",
              "data-department": dept.id,
            }, [
              R.createElement("strong", { key: "t" }, dept.title),
              R.createElement("span", {
                key: "m", className: "warroom__muted",
              }, ` ${dept.id} · ${dept.service_lines.length} service lines`),
            ]))),
        ]));
        children.push(section(R, "roles", "Role templates", [
          R.createElement("ul", {
            key: "list", className: "warroom__list warroom__list--scroll",
            "data-testid": "warroom-roles",
          }, directory.roles.map((role) =>
            R.createElement("li", {
              key: role.id, "data-testid": "warroom-role",
              "data-role": role.id,
            }, [
              R.createElement("strong", { key: "t" }, role.title),
              R.createElement("span", {
                key: "m", className: "warroom__muted",
              }, ` · ${role.department} · ${role.autonomy} · `
                + `${role.model.provider_hint || "provider?"}/`
                + `${role.model.model_hint || "model?"}`),
            ]))),
        ]));
        children.push(section(R, "floors", "Tower directory", [
          R.createElement(TowerNavigator as never, {
            key: "tower", floors: directory.floors, world,
            "data-testid": "warroom-tower",
          }),
        ]));
      }
    } else {
      if (load.kind === "loading") {
        children.push(R.createElement("div", {
          key: "loading", className: "warroom__state",
          "data-testid": "warroom-loading", role: "status",
        }, "Loading War Room sessions…"));
      } else if (load.kind === "error") {
        children.push(R.createElement("div", {
          key: "state", className: "warroom__state warroom__state--error",
          "data-testid": "warroom-state", role: "alert",
          "data-code": load.code,
        }, load.code ? `${load.code}: ${load.message}` : load.message));
      } else {
        if (load.kind === "empty") {
          children.push(R.createElement("div", {
            key: "empty", className: "warroom__state",
            "data-testid": "warroom-empty", role: "status",
          }, load.message));
        } else if (!load.sessions.length) {
          children.push(R.createElement("div", {
            key: "empty", className: "warroom__state",
            "data-testid": "warroom-empty", role: "status",
          }, "No War Room sessions yet. Open one below to record intake."));
        } else {
          children.push(R.createElement("label", {
            key: "picker", className: "warroom__picker",
          }, "Session",
            R.createElement("select", {
              key: "s",
              "data-testid": "warroom-session-picker",
              value: selectedId ?? "",
              onChange: (event: { target: { value: string } }) =>
                setSelectedId(event.target.value || null),
            }, load.sessions.map((session) => R.createElement("option", {
              key: session.session_id, value: session.session_id,
            }, `${session.title} — ${session.state}`)))));
        }

        // Intake / create. The server creates the local Personal workspace on
        // the first owner-opened session when none exists.
        children.push(section(R, "new", "New idea", [
          R.createElement("div", { key: "row", className: "warroom__row" }, [
            input(R, "title", {
              "data-testid": "warroom-new-title",
              placeholder: "Idea title", value: title,
              onInput: (event: { target: { value: string } }) =>
                setTitle(event.target.value),
            }),
            R.createElement("button", {
              key: "create", type: "button", className: "office-game__btn",
              "data-testid": "warroom-new-create",
              disabled: busy !== null || !title.trim(),
              onClick: () => run("Idea", async () => {
                const created = await createWarRoomSession({
                  title: title.trim(),
                  tenant_id: load.kind === "ready" ? load.tenantId : undefined,
                  business_id: load.kind === "ready"
                    ? load.businessId ?? undefined : undefined,
                  idempotency_key: newIdempotencyKey("warroom.create"),
                }, opts);
                setTitle("");
                setSelectedId(created.session.session_id);
              }),
            }, "Open session"),
          ]),
          R.createElement("div", { key: "pilot", className: "warroom__row" }, [
            R.createElement("button", {
              key: "etsy", type: "button", className: "office-game__btn",
              "data-testid": "warroom-etsy-pilot",
              disabled: busy !== null,
              onClick: () => run("Etsy pilot", async () => {
                const result = await startEtsyPilot({
                  tenant_id: load.kind === "ready" ? load.tenantId : undefined,
                  business_id: load.kind === "ready"
                    ? load.businessId ?? undefined : undefined,
                  idempotency_key: newIdempotencyKey("warroom.etsy-pilot"),
                }, opts);
                return result.session.session_id;
              }),
            }, "Start Etsy pilot"),
            R.createElement("span", {
              key: "hint", className: "warroom__muted",
            }, "Creates one pilot session and leaves the decision to you. "
              + "No publish, task, spend or contact."),
          ]),
        ]));

        if (bundle) {
          const session = bundle.session;
          children.push(R.createElement("div", {
            key: "sessionHead", className: "warroom__sessionHead",
            "data-testid": "warroom-session-head",
            "data-session-state": session.state,
          }, [
            R.createElement("strong", { key: "t" }, session.title),
            chip(R, session.state, "state"),
            R.createElement("span", {
              key: "id", className: "warroom__muted",
            }, session.session_id),
          ]));

          children.push(section(R, "sources", "Sources", [
            R.createElement("ul", {
              key: "list", className: "warroom__list",
              "data-testid": "warroom-sources",
            }, bundle.sources.map((source) =>
              R.createElement("li", {
                key: source.source_id, "data-testid": "warroom-source",
                "data-source-kind": source.kind, "data-status": source.status,
              }, [
                R.createElement("strong", { key: "k" }, source.kind),
                R.createElement("span", { key: "l" }, ` ${source.locator}`),
                chip(R, source.status, "s"),
              ]))),
            R.createElement("div", { key: "row", className: "warroom__row" }, [
              R.createElement("select", {
                key: "kind", "data-testid": "warroom-source-kind",
                value: sourceKind,
                onChange: (event: { target: { value: string } }) =>
                  setSourceKind(event.target.value),
              }, ["note", "url", "youtube", "document", "image", "text"]
                .map((kind) => R.createElement("option", {
                  key: kind, value: kind,
                }, kind))),
              input(R, "locator", {
                "data-testid": "warroom-source-locator",
                placeholder: "URL, note or document text",
                value: sourceLocator,
                onInput: (event: { target: { value: string } }) =>
                  setSourceLocator(event.target.value),
              }),
              R.createElement("button", {
                key: "add", type: "button", className: "office-game__btn",
                "data-testid": "warroom-source-add",
                disabled: busy !== null || !sourceLocator.trim(),
                onClick: () => run("Source", async () => {
                  await addWarRoomSource(session.session_id, {
                    kind: sourceKind,
                    locator: sourceLocator.trim(),
                  }, opts);
                  setSourceLocator("");
                }),
              }, "Add source"),
            ]),
          ]));

          const canResearch = session.state === "intake"
            || session.state === "research";
          children.push(section(R, "research", "Research with Perplexity", [
            R.createElement("div", { key: "row", className: "warroom__row" }, [
              input(R, "research-query", {
                "data-testid": "warroom-research-query",
                placeholder: "Question about the idea or a source",
                value: researchQuery,
                onInput: (event: { target: { value: string } }) =>
                  setResearchQuery(event.target.value),
              }),
              R.createElement("button", {
                key: "run", type: "button", className: "office-game__btn",
                "data-testid": "warroom-research-perplexity",
                disabled: busy !== null || !canResearch,
                onClick: () => run("Research", async () => {
                  await researchWarRoomSession(session.session_id, {
                    query: researchQuery.trim() || session.title,
                    provider: "perplexity",
                  }, opts);
                  setResearchQuery("");
                }),
              }, "Research with Perplexity"),
            ]),
            R.createElement("span", {
              key: "hint", className: "warroom__muted",
              "data-testid": "warroom-research-hint",
            }, canResearch
              ? "Adds cited source URLs and labelled evidence only. No "
                + "decision, task, publish, spend or contact."
              : "Research runs while the session is in intake or research."),
          ]));

          children.push(section(R, "evidence", "Evidence", [
            R.createElement("ul", {
              key: "list", className: "warroom__list",
              "data-testid": "warroom-evidence",
            }, bundle.evidence.map((item) =>
              R.createElement("li", {
                key: item.evidence_id, "data-testid": "warroom-evidence-item",
                "data-label": item.label,
              }, [
                chip(R, item.label, "c", LABEL_COLORS[item.label]),
                R.createElement("span", { key: "t" }, ` ${item.claim}`),
                item.source_id
                  ? R.createElement("span", {
                      key: "src", className: "warroom__muted",
                    }, ` (source ${item.source_id})`)
                  : null,
              ]))),
            R.createElement("div", { key: "row", className: "warroom__row" }, [
              R.createElement("select", {
                key: "label", "data-testid": "warroom-evidence-label",
                value: evidenceLabel,
                onChange: (event: { target: { value: string } }) =>
                  setEvidenceLabel(event.target.value),
              }, ["FACT", "INFERENCE", "ASSUMPTION", "UNKNOWN"]
                .map((label) => R.createElement("option", {
                  key: label, value: label,
                }, label))),
              input(R, "claim", {
                "data-testid": "warroom-evidence-claim",
                placeholder: "Claim",
                value: evidenceClaim,
                onInput: (event: { target: { value: string } }) =>
                  setEvidenceClaim(event.target.value),
              }),
              R.createElement("select", {
                key: "source", "data-testid": "warroom-evidence-source",
                value: evidenceSource,
                onChange: (event: { target: { value: string } }) =>
                  setEvidenceSource(event.target.value),
              }, [
                R.createElement("option", { key: "", value: "" },
                  "no source"),
                ...bundle.sources.map((source) => R.createElement("option", {
                  key: source.source_id, value: source.source_id,
                }, `${source.kind}: ${source.locator.slice(0, 40)}`)),
              ]),
              R.createElement("button", {
                key: "add", type: "button", className: "office-game__btn",
                "data-testid": "warroom-evidence-add",
                disabled: busy !== null || !evidenceClaim.trim(),
                onClick: () => run("Evidence", async () => {
                  await addWarRoomEvidence(session.session_id, {
                    label: evidenceLabel,
                    claim: evidenceClaim.trim(),
                    source_id: evidenceSource || undefined,
                  }, opts);
                  setEvidenceClaim("");
                }),
              }, "Add evidence"),
            ]),
          ]));

          children.push(section(R, "debate", "Debate & dissent", [
            R.createElement("ul", {
              key: "list", className: "warroom__list",
              "data-testid": "warroom-arguments",
            }, bundle.arguments.map((argument) =>
              R.createElement("li", {
                key: argument.argument_id,
                "data-testid": "warroom-argument",
                "data-stance": argument.stance,
              }, [
                R.createElement("strong", { key: "r" },
                  `${argument.role} (${argument.stance})`),
                R.createElement("span", { key: "t" }, ` ${argument.text}`),
              ]))),
            bundle.session.dissent.length
              ? R.createElement("p", {
                  key: "dissent", className: "warroom__dissent",
                  "data-testid": "warroom-dissent",
                }, `${bundle.session.dissent.length} dissenting/questioning `
                  + `argument${bundle.session.dissent.length === 1 ? "" : "s"} `
                  + "preserved.")
              : null,
          ]));

          if (bundle.session.brief && Object.keys(bundle.session.brief).length) {
            children.push(section(R, "synthesis", "Synthesis", [
              R.createElement("p", {
                key: "brief", className: "warroom__brief",
                "data-testid": "warroom-brief",
              }, String(bundle.session.brief.idea || "Idea brief recorded")),
              R.createElement("p", {
                key: "score", className: "warroom__muted",
                "data-testid": "warroom-score",
              }, Object.entries(bundle.session.score || {})
                .map(([key, value]) => `${key}=${value}`).join(" · ")
                || "no score recorded"),
            ]));
          }

          const decided = bundle.decisions[bundle.decisions.length - 1];
          children.push(section(R, "decision", "Owner decision", [
            decided
              ? R.createElement("p", {
                  key: "d", "data-testid": "warroom-decision",
                  "data-status": decided.status,
                }, `${decided.status} by ${decided.decided_by}`
                  + (decided.note ? ` — ${decided.note}` : ""))
              : null,
            session.state === "owner_decision"
              ? R.createElement("div", { key: "row", className: "warroom__row" }, [
                  input(R, "note", {
                    "data-testid": "warroom-decision-note",
                    placeholder: "Decision note (optional)",
                    value: decisionNote,
                    onInput: (event: { target: { value: string } }) =>
                      setDecisionNote(event.target.value),
                  }),
                  R.createElement("button", {
                    key: "approve", type: "button", className: "office-game__btn",
                    "data-testid": "warroom-approve",
                    disabled: busy !== null,
                    onClick: () => run("Decision", () =>
                      recordWarRoomDecision(session.session_id, {
                        decision: "approved",
                        note: decisionNote.trim(),
                        idempotency_key: newIdempotencyKey("warroom.decision"),
                      }, opts)),
                  }, "Approve"),
                  R.createElement("button", {
                    key: "reject", type: "button",
                    className: "office-game__btn warroom__danger",
                    "data-testid": "warroom-reject",
                    disabled: busy !== null,
                    onClick: () => run("Decision", () =>
                      recordWarRoomDecision(session.session_id, {
                        decision: "rejected",
                        note: decisionNote.trim(),
                        idempotency_key: newIdempotencyKey("warroom.decision"),
                      }, opts)),
                  }, "Reject"),
                ])
              : null,
            session.state === "approved"
              ? R.createElement("button", {
                  key: "tasks", type: "button", className: "office-game__btn",
                  "data-testid": "warroom-create-tasks",
                  disabled: busy !== null,
                  onClick: () => run("Tasks", () =>
                    createWarRoomTasks(session.session_id, opts)),
                }, "Create approved tasks")
              : null,
          ]));

          children.push(section(R, "outputs", "Outputs", [
            R.createElement("ul", {
              key: "tasks", className: "warroom__list",
              "data-testid": "warroom-tasks",
            }, bundle.tasks.map((task) =>
              R.createElement("li", {
                key: task.task_id, "data-testid": "warroom-task",
                "data-status": task.status,
              }, [
                R.createElement("strong", { key: "t" }, task.title),
                R.createElement("span", { key: "m", className: "warroom__muted" },
                  ` · ${task.status}`),
              ]))),
            R.createElement("ul", {
              key: "experiments", className: "warroom__list",
              "data-testid": "warroom-experiments",
            }, bundle.experiments.map((experiment) =>
              R.createElement("li", {
                key: experiment.experiment_id,
                "data-testid": "warroom-experiment",
              }, [
                R.createElement("strong", { key: "n" }, experiment.name),
                R.createElement("span", { key: "m", className: "warroom__muted" },
                  ` · ${experiment.status} · ${experiment.success_metric}`),
              ]))),
          ]));
        }
      }
    }

    if (busy) {
      children.push(R.createElement("div", {
        key: "busy", className: "warroom__state",
        "data-testid": "warroom-busy", role: "status",
      }, `${busy}…`));
    }
    if (error) {
      children.push(R.createElement("div", {
        key: "error", className: "warroom__state warroom__state--error",
        "data-testid": "warroom-error", role: "alert",
      }, error));
    }
    if (notice) {
      children.push(R.createElement("div", {
        key: "notice", className: "warroom__state",
        "data-testid": "warroom-notice", role: "status",
      }, notice));
    }

    return R.createElement("div", {
      className: "warroom", "data-testid": "warroom", "data-tab": tab,
    }, children);
  };
}
