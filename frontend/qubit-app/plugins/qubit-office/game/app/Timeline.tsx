/* P4-07 operator timeline: a replayable, evidence-backed operator panel.
 *
 * A semantic, keyboard-operable region over the real Business OS timeline.
 * Every run status comes from the durable job row; `completed` is only shown
 * when an artifact or receipt is linked, and `unknown`/`pending` are never
 * upgraded. Replay is labelled a simulation with stubbed effects.
 *
 * It is a side sheet in the Qubit shell (never a modal) and it never covers
 * the iframe: closing restores focus, focus is never trapped, and the panel
 * issues only the one read route.
 *
 * React comes from the HOST: `R.createElement`, never an import.
 */
import type { OfficeGameShellProps } from "../OfficeGameShell";
import {
  TIMELINE_KINDS,
  announcement,
  causalSort,
  clampIndex,
  costLabel,
  countsSummary,
  domId,
  evidenceHref,
  evidenceRefs,
  evidenceSummary,
  effectiveStatus,
  fetchTimeline,
  filterTimeline,
  hasEvidence,
  isCompleted,
  isPending,
  isReplay,
  isReplayItem,
  isUnknown,
  kindLabel,
  nextIndex,
  replayLabel,
  statusLabel,
  statusMarker,
  statusOptions,
  tokensLabel,
  unavailableSources,
  type TimelineItem,
  type TimelineKind,
  type TimelineRead,
} from "./timeline";

export interface TimelineProps {
  base?: string;
  fetchImpl?: typeof fetch;
  run?: string | null;
  task?: string | null;
  agent?: string | null;
  session?: string | null;
  replay?: boolean;
  initialLimit?: number;
  onClose?: () => void;
}

function evidenceText(
  R: OfficeGameShellProps["React"],
  item: TimelineItem,
  items: readonly TimelineItem[],
) {
  const refs = evidenceRefs(item);
  if (refs.length === 0) return null;
  return R.createElement("span", {
    key: "evidence",
    className: "timeline__evidence",
    "data-testid": "timeline-evidence",
  }, [
    R.createElement("span", { key: "label", className: "timeline__muted" },
      "Evidence: "),
    ...refs.map((ref, index) => {
      const href = evidenceHref(ref, items);
      const text = `${ref.kind} ${ref.id}`;
      return href
        ? R.createElement("a", {
            key: `${ref.kind}-${ref.id}-${index}`,
            href,
            className: "timeline__evidenceLink",
          }, text)
        : R.createElement("span", {
            key: `${ref.kind}-${ref.id}-${index}`,
            className: "timeline__evidenceLink",
          }, text);
    }),
  ]);
}

export function createTimeline(
  R: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
) {
  const { useState, useEffect, useRef } = hooks;

  return function Timeline(props: TimelineProps) {
    const [query, setQuery] = useState("");
    const [kindFilter, setKindFilter] = useState<string>("all");
    const [statusFilter, setStatusFilter] = useState<string>("all");
    const [replayToggle, setReplayToggle] = useState(Boolean(props.replay));
    const [read, setRead] = useState<TimelineRead>({ kind: "loading" });
    const [activeIndex, setActiveIndex] = useState(-1);
    const [retryToken, setRetryToken] = useState(0);
    const [reducedMotion, setReducedMotion] = useState(false);
    const restoreRef = useRef<HTMLElement | null>(null);
    const itemRefs = useRef<Array<HTMLElement | null>>([]);

    // Focus restore: remember the opener, move focus into the sheet and put it
    // back on close. No trap, no sentinel node.
    useEffect(() => {
      const doc = typeof document === "undefined" ? null : document;
      restoreRef.current = (doc?.activeElement as HTMLElement | null) ?? null;
      return () => {
        const element = restoreRef.current;
        if (element && doc?.contains(element)) element.focus?.();
      };
    }, []);

    // The panel is motion-free by design; the media query is still honoured so
    // a future transition cannot ignore the operator's preference.
    useEffect(() => {
      if (typeof window === "undefined" || !window.matchMedia) return;
      const media = window.matchMedia("(prefers-reduced-motion: reduce)");
      const update = () => setReducedMotion(Boolean(media.matches));
      update();
      if (media.addEventListener) media.addEventListener("change", update);
      return () => {
        if (media.removeEventListener) {
          media.removeEventListener("change", update);
        }
      };
    }, []);

    useEffect(() => {
      let live = true;
      const ctl = typeof AbortController === "undefined" ? undefined
        : new AbortController();
      setRead({ kind: "loading" });
      fetchTimeline({
        base: props.base,
        fetchImpl: props.fetchImpl,
        run: props.run,
        task: props.task,
        agent: props.agent,
        session: props.session,
        replay: replayToggle,
        limit: props.initialLimit,
        signal: ctl?.signal,
      }).then((next) => {
        if (!live) return;
        setRead(next);
        setActiveIndex(next.kind === "available" || next.kind === "empty" ? 0 : -1);
      });
      return () => {
        live = false;
        ctl?.abort();
      };
    }, [
      props.base, props.fetchImpl, props.run, props.task, props.agent,
      props.session, props.initialLimit, replayToggle, retryToken,
    ]);

    const projection = read.kind === "available" || read.kind === "empty"
      ? read.projection : null;
    const items = causalSort(filterTimeline(projection?.items, {
      q: query,
      kind: kindFilter as TimelineKind | "all",
      status: statusFilter === "all" ? "" : statusFilter,
    }));
    const active = clampIndex(activeIndex, items.length);
    const replay = isReplay(projection) || replayToggle;

    const focusAt = (index: number) => {
      if (items.length === 0) return;
      const next = clampIndex(index, items.length);
      setActiveIndex(next);
      itemRefs.current[next]?.focus?.();
    };

    const onListKeyDown = (event: {
      key?: string; preventDefault?: () => void;
    }) => {
      const key = String(event.key ?? "");
      if (key === "ArrowDown") {
        event.preventDefault?.();
        focusAt(nextIndex(active, items.length, 1));
      } else if (key === "ArrowUp") {
        event.preventDefault?.();
        focusAt(nextIndex(active, items.length, -1));
      } else if (key === "Home") {
        event.preventDefault?.();
        focusAt(0);
      } else if (key === "End") {
        event.preventDefault?.();
        focusAt(items.length - 1);
      } else if (key === "Escape") {
        event.preventDefault?.();
        props.onClose?.();
      }
    };

    const header = R.createElement("div", {
      key: "head",
      className: "office-game__dockHead office-theme-dock-head",
    }, [
      R.createElement("span", {
        key: "title", className: "office-theme-dock-title",
      }, "Operator timeline"),
      replay
        ? R.createElement("span", {
            key: "replay",
            className: "timeline__replayMarker",
            "data-testid": "timeline-replay-marker",
            role: "note",
            "aria-label": "Replay mode: simulated, effects stubbed",
          }, "REPLAY · simulated · effects stubbed")
        : null,
      props.onClose
        ? R.createElement("button", {
            key: "close", type: "button",
            className: "office-game__btn office-theme-dock-close",
            "data-testid": "timeline-close",
            "aria-label": "Close the operator timeline",
            onClick: () => props.onClose?.(),
          }, "Close")
        : null,
    ]);

    const controls = R.createElement("div", {
      key: "controls",
      className: "timeline__controls",
      role: "group",
      "aria-label": "Timeline filters",
    }, [
      R.createElement("label", { key: "q" }, [
        R.createElement("span", { key: "l" }, "Search "),
        R.createElement("input", {
          key: "i", type: "search",
          "data-testid": "timeline-search",
          "aria-label": "Search the operator timeline",
          value: query,
          onChange: (event: { target?: { value?: string } }) =>
            setQuery(String(event?.target?.value ?? "")),
        }),
      ]),
      R.createElement("label", { key: "kind" }, [
        R.createElement("span", { key: "l" }, "Kind "),
        R.createElement("select", {
          key: "s",
          "data-testid": "timeline-kind",
          "aria-label": "Filter by record kind",
          value: kindFilter,
          onChange: (event: { target?: { value?: string } }) =>
            setKindFilter(String(event?.target?.value ?? "all")),
        }, [
          R.createElement("option", { key: "all", value: "all" }, "All"),
          ...TIMELINE_KINDS.map((kind) =>
            R.createElement("option", { key: kind, value: kind },
              kindLabel(kind))),
        ]),
      ]),
      R.createElement("label", { key: "status" }, [
        R.createElement("span", { key: "l" }, "Status "),
        R.createElement("select", {
          key: "s",
          "data-testid": "timeline-status",
          "aria-label": "Filter by status",
          value: statusFilter,
          onChange: (event: { target?: { value?: string } }) =>
            setStatusFilter(String(event?.target?.value ?? "all")),
        }, [
          R.createElement("option", { key: "all", value: "all" }, "All"),
          ...statusOptions(projection?.items).map((status) =>
            R.createElement("option", { key: status, value: status },
              statusLabel(status))),
        ]),
      ]),
      R.createElement("button", {
        key: "replay", type: "button",
        "data-testid": "timeline-replay-toggle",
        "aria-pressed": replayToggle ? "true" : "false",
        onClick: () => setReplayToggle((value: boolean) => !value),
      }, replayToggle ? "Replay on" : "Replay"),
    ]);

    const children: unknown[] = [header, controls];

    if (read.kind === "loading") {
      children.push(R.createElement("div", {
        key: "loading",
        className: "timeline__state",
        "data-testid": "timeline-loading",
        role: "status",
        "aria-live": "polite",
      }, "Loading the operator timeline…"));
    } else if (read.kind === "error" || read.kind === "unavailable") {
      const code = read.kind === "unavailable" ? read.code : "TIMELINE_READ_FAILED";
      const detail = read.kind === "unavailable" ? read.detail : read.message;
      children.push(R.createElement("div", {
        key: "error",
        className: "timeline__state timeline__state--error",
        "data-testid": "timeline-error",
        "data-code": code,
        role: "alert",
      }, [
        R.createElement("strong", { key: "t" },
          "The operator timeline is unavailable"),
        R.createElement("div", {
          key: "d", className: "timeline__muted",
          "data-testid": "timeline-error-detail",
        }, `${code}: ${detail}`),
        R.createElement("button", {
          key: "r", type: "button",
          "data-testid": "timeline-retry",
          onClick: () => setRetryToken((value: number) => value + 1),
        }, "Retry"),
      ]));
    } else if (read.kind === "empty" || items.length === 0) {
      children.push(R.createElement("div", {
        key: "empty",
        className: "timeline__state",
        "data-testid": "timeline-empty",
        role: "status",
        "aria-live": "polite",
      }, "No durable records match this timeline."));
    } else {
      const sources = unavailableSources(projection);
      if (sources.length > 0) {
        children.push(R.createElement("div", {
          key: "sources",
          className: "timeline__state timeline__state--partial",
          "data-testid": "timeline-partial",
          role: "alert",
        }, `Some sources are unavailable (${sources.map((source) =>
          `${source.name}: ${source.code ?? "error"}`).join("; ")}).`));
      }
      children.push(R.createElement("p", {
        key: "summary",
        className: "timeline__summary",
        "data-testid": "timeline-summary",
        role: "status",
        "aria-live": "polite",
      }, `${countsSummary(projection)}${replayLabel(projection)
        ? ` · ${replayLabel(projection)}` : ""}`));
      children.push(R.createElement("ol", {
        key: "list",
        className: "timeline__list",
        "data-testid": "timeline-list",
        role: "list",
        "aria-label": "Causal operator timeline",
        onKeyDown: onListKeyDown,
      }, items.map((item, index) => {
        const status = effectiveStatus(item);
        const completed = isCompleted(item);
        const unknown = isUnknown(item);
        const pending = isPending(item);
        const markers: unknown[] = [
          R.createElement("span", {
            key: "marker",
            className: "timeline__marker",
            "aria-hidden": "true",
          }, statusMarker(status)),
          R.createElement("span", {
            key: "status",
            className: "timeline__status",
            "data-status": status,
          }, statusLabel(status)),
        ];
        if (unknown) {
          markers.push(R.createElement("span", {
            key: "unknown", className: "timeline__flag",
            "data-testid": "timeline-item-unknown",
          }, "UNKNOWN"));
        }
        if (pending && !unknown) {
          markers.push(R.createElement("span", {
            key: "pending", className: "timeline__flag",
            "data-testid": "timeline-item-pending",
          }, "PENDING"));
        }
        if (completed) {
          markers.push(R.createElement("span", {
            key: "completed", className: "timeline__flag",
            "data-testid": "timeline-item-completed",
          }, "EVIDENCED"));
        }
        if (isReplayItem(item)) {
          markers.push(R.createElement("span", {
            key: "replay", className: "timeline__flag",
            "data-testid": "timeline-item-replay",
          }, "REPLAY"));
        }
        const evidence = evidenceText(R, item, items);
        return R.createElement("li", {
          key: item.item_id,
          id: domId(item.item_id),
          ref: (node: HTMLElement | null) => { itemRefs.current[index] = node; },
          className: "timeline__item",
          "data-testid": "timeline-item",
          "data-kind": item.kind,
          "data-status": status,
          "data-unknown": String(unknown),
          "data-pending": String(pending),
          "data-completed": String(completed),
          "data-replay": String(isReplayItem(item)),
          tabIndex: index === active ? 0 : -1,
          role: "listitem",
          "aria-label": `${kindLabel(item.kind)} ${item.title}: `
            + `${statusLabel(status)}${unknown ? ", unknown" : ""}`
            + `${isReplayItem(item) ? ", replay" : ""}`,
        }, [
          R.createElement("div", { key: "row", className: "timeline__row" },
            markers),
          R.createElement("div", { key: "title", className: "timeline__title" },
            `${kindLabel(item.kind)} · ${item.title}`),
          R.createElement("div", { key: "summaryText", className: "timeline__muted" },
            item.summary),
          R.createElement("div", { key: "meta", className: "timeline__muted" },
            `${item.actor} · ${item.at}`),
          item.cost
            ? R.createElement("div", {
                key: "cost", className: "timeline__cost",
                "data-testid": "timeline-item-cost",
              }, `Cost ${costLabel(item.cost)}`)
            : null,
          item.tokens
            ? R.createElement("div", {
                key: "tokens", className: "timeline__tokens",
                "data-testid": "timeline-item-tokens",
              }, tokensLabel(item.tokens))
            : null,
          evidence,
          hasEvidence(item) || unknown || pending
            ? R.createElement("div", {
                key: "evidenceSummary",
                className: "timeline__muted",
                "data-testid": "timeline-item-evidence-summary",
              }, evidenceSummary(item))
            : null,
          item.detail && Object.keys(item.detail).length > 0
            ? R.createElement("details", {
                key: "detail", className: "timeline__detail",
              }, [
                R.createElement("summary", { key: "s" }, "Record detail"),
                R.createElement("pre", {
                  key: "p", className: "timeline__pre",
                }, JSON.stringify(item.detail, null, 2)),
              ])
            : null,
        ]);
      })));
    }

    return R.createElement("section", {
      className: "timeline",
      "data-testid": "operator-timeline",
      "data-state": read.kind,
      "data-mode": replay ? "replay" : "live",
      "data-replay": String(replay),
      "data-reduced-motion": String(reducedMotion),
      "data-filtered": String(Boolean(query || kindFilter !== "all"
        || statusFilter !== "all")),
      role: "region",
      "aria-label": "Operator timeline",
      "aria-live": "polite",
      style: {
        display: "flex",
        flexDirection: "column",
        gap: "var(--office-s2)",
        flex: "1 1 auto",
        minHeight: 0,
        overflow: "auto",
        padding: "var(--office-s3)",
      },
    }, children);
  };
}

/** The screen-reader announcement for tests and host live regions. */
export function timelineAnnouncement(read: TimelineRead): string {
  return announcement(read);
}
