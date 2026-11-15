/* P3-05 command-center shell over real state.
 *
 * A semantic, keyboard-operable region that mirrors the office's canvas
 * entities: floors, rooms, desks, agents, tasks and approvals. It reads only
 * the real command-center projection (floors/rooms/desks templates, roster,
 * tasks, sessions, approvals) and renders its typed loading/empty/error/
 * unavailable states honestly. It is a side sheet, not a modal: focus is never
 * trapped, and closing restores the element that opened it.
 *
 * React comes from the HOST: `R.createElement`, never an import.
 */
import type { OfficeGameShellProps } from "../OfficeGameShell";
import { createBusinessSetup, isTenantMissing } from "./BusinessSetup";
import {
  announcement,
  clampIndex,
  COMMAND_CENTER_KINDS,
  entryDeepLink,
  filterEntries,
  fetchCommandCenter,
  inboxSummaryText,
  isAttention,
  kindLabel,
  nextIndex,
  resultSummary,
  rosterSummaryText,
  shortcutAction,
  statusLabel,
  statusMarker,
  statusOptions,
  unavailableSources,
  type CommandCenterEntry,
  type CommandCenterRead,
  type CommandCenterKind,
} from "./command-center";

export interface CommandCenterProps {
  base?: string;
  fetchImpl?: typeof fetch;
  onClose?: () => void;
  onJump?: (entry: CommandCenterEntry, href: string | null) => void;
  initialQuery?: string;
  initialKind?: CommandCenterKind | "all";
  initialStatus?: string;
}

function jumpTo(entry: CommandCenterEntry, href: string | null,
                onJump?: CommandCenterProps["onJump"]) {
  onJump?.(entry, href);
  if (!onJump && href && typeof window !== "undefined") {
    window.location.assign(href);
  }
}

export function createCommandCenter(
  R: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
) {
  const { useState, useEffect, useRef } = hooks;
  const useBusinessSetup = createBusinessSetup(R, hooks);

  return function CommandCenter(props: CommandCenterProps) {
    const [query, setQuery] = useState(String(props.initialQuery ?? ""));
    const [kindFilter, setKindFilter] = useState<string>(
      String(props.initialKind ?? "all"));
    const [statusFilter, setStatusFilter] = useState<string>(
      String(props.initialStatus ?? "all"));
    const [read, setRead] = useState<CommandCenterRead>({ kind: "loading" });
    const [activeIndex, setActiveIndex] = useState(-1);
    // Bumped after the owner boots the Business OS, so the effect re-reads and
    // the projection replaces the setup state.
    const [attempt, setAttempt] = useState(0);
    // Called unconditionally so the hook order is stable. It only renders a
    // write control; nothing is created unless the operator presses it.
    const setup = useBusinessSetup({
      base: props.base,
      fetchImpl: props.fetchImpl,
      surface: "command center",
      onBootstrapped: () => setAttempt((value: number) => value + 1),
    });
    const searchRef = useRef<HTMLInputElement | null>(null);
    const restoreRef = useRef<HTMLElement | null>(null);
    const itemRefs = useRef<Array<HTMLElement | null>>([]);

    // Focus restore: capture what opened the sheet, focus the search field,
    // and put focus back on close. No trap, no sentinel nodes.
    useEffect(() => {
      const doc = typeof document === "undefined" ? null : document;
      restoreRef.current = (doc?.activeElement as HTMLElement | null) ?? null;
      searchRef.current?.focus?.();
      return () => {
        const element = restoreRef.current;
        if (element && doc?.contains(element)) element.focus?.();
      };
    }, []);

    useEffect(() => {
      let live = true;
      const ctl = typeof AbortController === "undefined" ? undefined
        : new AbortController();
      setRead({ kind: "loading" });
      fetchCommandCenter({
        q: query,
        kind: kindFilter as CommandCenterKind | "all",
        status: statusFilter === "all" ? "" : statusFilter,
        base: props.base,
        fetchImpl: props.fetchImpl,
        signal: ctl?.signal,
      }).then((next) => {
        if (live) {
          setRead(next);
          setActiveIndex(next.kind === "available" ? 0 : -1);
        }
      });
      return () => {
        live = false;
        ctl?.abort();
      };
    }, [query, kindFilter, statusFilter, props.base, props.fetchImpl, attempt]);

    const projection = read.kind === "available" ? read.projection : null;
    const entries = filterEntries(projection?.entries, {
      q: query,
      kind: kindFilter as CommandCenterKind | "all",
      status: statusFilter === "all" ? "" : statusFilter,
    });
    const active = clampIndex(activeIndex, entries.length);

    const focusAt = (index: number) => {
      if (entries.length === 0) return;
      const next = clampIndex(index, entries.length);
      setActiveIndex(next);
      const element = itemRefs.current[next];
      if (element && typeof element.focus === "function") element.focus();
    };

    const onKeyDown = (event: {
      key: string; ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean;
      shiftKey?: boolean; target?: unknown;
      preventDefault?: () => void;
    }) => {
      const action = shortcutAction(event);
      if (!action && event.key !== "Tab") return;
      event.preventDefault?.();
      if (action === "focus-search") {
        searchRef.current?.focus?.();
        return;
      }
      if (action === "close") {
        props.onClose?.();
        return;
      }
      if (action === "move-next") {
        focusAt(nextIndex(active, entries.length, 1));
        return;
      }
      if (action === "move-prev") {
        focusAt(nextIndex(active, entries.length, -1));
        return;
      }
      if (action === "move-first") {
        focusAt(0);
        return;
      }
      if (action === "move-last") {
        focusAt(entries.length - 1);
        return;
      }
      if (action === "jump" && entries.length > 0) {
        const entry = entries[active < 0 ? 0 : active];
        jumpTo(entry, entryDeepLink(entry, props.base), props.onJump);
      }
    };

    const sources = unavailableSources(projection);
    const statusChoices = statusOptions(projection?.entries);
    const stateAnnouncement = announcement(read, projection);

    const children: unknown[] = [
      R.createElement("div", {
        key: "head",
        className: "office-game__dockHead office-theme-dock-head",
      }, [
        R.createElement("span", {
          key: "title", className: "office-theme-dock-title",
        }, "Command Center"),
        R.createElement("button", {
          key: "close", type: "button",
          className: "office-game__btn office-theme-dock-close",
          "data-testid": "command-center-close",
          "aria-label": "Close command center",
          onClick: () => props.onClose?.(),
        }, "Close"),
      ]),
      R.createElement("div", {
        key: "search",
        role: "search",
        className: "command-center__search",
        "data-testid": "command-center-search",
      }, [
        R.createElement("label", {
          key: "label", htmlFor: "command-center-query",
          className: "command-center__label",
        }, "Search floors, rooms, desks, agents, tasks and approvals"),
        R.createElement("input", {
          key: "input",
          id: "command-center-query",
          ref: searchRef,
          type: "search",
          value: query,
          autoComplete: "off",
          "aria-describedby": "command-center-help",
          "data-testid": "command-center-query",
          placeholder: "Search by floor, room, desk, agent, task or approval",
          onChange: (event: { target: { value: string } }) => {
            setQuery(event.target.value);
            setActiveIndex(0);
          },
          onKeyDown,
        }),
        R.createElement("p", {
          key: "help",
          id: "command-center-help",
          className: "office-game__sr-only",
        }, "Ctrl or Command K focuses search. Arrow keys move between results. "
          + "Enter opens the selected result. Escape closes the command center."),
        R.createElement("div", {
          key: "filters", className: "command-center__filters",
        }, [
          R.createElement("label", {
            key: "kind-label", htmlFor: "command-center-kind",
            className: "command-center__filterLabel",
          }, "Kind"),
          R.createElement("select", {
            key: "kind",
            id: "command-center-kind",
            value: kindFilter,
            "data-testid": "command-center-kind",
            "aria-label": "Filter by kind",
            onChange: (event: { target: { value: string } }) => {
              setKindFilter(event.target.value);
              setActiveIndex(0);
            },
          }, [
            R.createElement("option", { key: "all", value: "all" }, "All kinds"),
            ...COMMAND_CENTER_KINDS.map((kind) => R.createElement("option", {
              key: kind, value: kind,
            }, kindLabel(kind))),
          ]),
          R.createElement("label", {
            key: "status-label", htmlFor: "command-center-status",
            className: "command-center__filterLabel",
          }, "Status"),
          R.createElement("select", {
            key: "status",
            id: "command-center-status",
            value: statusFilter,
            "data-testid": "command-center-status",
            "aria-label": "Filter by status",
            onChange: (event: { target: { value: string } }) => {
              setStatusFilter(event.target.value);
              setActiveIndex(0);
            },
          }, [
            R.createElement("option", { key: "all", value: "all" }, "All statuses"),
            ...statusChoices.map((status) => R.createElement("option", {
              key: status, value: status,
            }, statusLabel(status))),
          ]),
        ]),
      ]),
    ];

    if (read.kind === "loading") {
      children.push(R.createElement("div", {
        key: "state", className: "command-center__state",
        "data-testid": "command-center-state",
        "data-state": "loading",
        role: "status",
        "aria-live": "polite",
      }, "Loading the command center…"));
    } else if (read.kind === "error" || read.kind === "unavailable") {
      const code = read.kind === "unavailable"
        ? read.code : "COMMAND_CENTER_READ_FAILED";
      const detail = read.kind === "unavailable" ? read.detail : read.message;
      if (isTenantMissing(read.kind === "unavailable" ? read.code : "")) {
        // A fresh profile is a real state, not an error: offer the explicit,
        // owner-triggered setup instead of an inert failure message.
        children.push(setup.render());
      } else {
        children.push(R.createElement("div", {
          key: "state",
          className: "command-center__state command-center__state--error",
          "data-testid": "command-center-state",
          "data-state": read.kind,
          "data-code": code,
          role: "alert",
        }, [
          R.createElement("strong", { key: "t" }, "Command center unavailable"),
          R.createElement("span", {
            key: "d", "data-testid": "command-center-error-detail",
          }, `${code}: ${detail}`),
        ]));
      }
    } else if (projection) {
      children.push(R.createElement("p", {
        key: "announce",
        className: "office-game__sr-only",
        role: "status",
        "aria-live": "polite",
        "data-testid": "command-center-announcement",
      }, stateAnnouncement));

      children.push(R.createElement("section", {
        key: "roster",
        className: "command-center__summary",
        "data-testid": "command-center-roster",
        "aria-label": "Roster summary",
      }, [
        R.createElement("h3", { key: "h" }, "Roster"),
        R.createElement("p", { key: "p" }, rosterSummaryText(projection.roster)),
        R.createElement("p", {
          key: "s",
          "data-testid": "command-center-counts",
        }, `${projection.counts.floors} floors · ${projection.counts.rooms} rooms · `
          + `${projection.counts.desks} desks · ${projection.counts.tasks} tasks · `
          + `${projection.counts.sessions} sessions`),
      ]));

      children.push(R.createElement("section", {
        key: "inbox",
        className: "command-center__summary",
        "data-testid": "command-center-inbox",
        "aria-label": "Notification inbox",
      }, [
        R.createElement("h3", { key: "h" }, "Needs you"),
        R.createElement("p", {
          key: "p", "data-testid": "command-center-inbox-count",
          "data-count": String(projection.inbox?.counts?.approvals_pending ?? 0),
        }, inboxSummaryText(projection)),
        ...(projection.inbox?.items ?? []).map((item) => R.createElement(
          "p", {
            key: item.approval_id,
            className: "command-center__inboxItem",
            "data-testid": "command-center-inbox-item",
            "data-status": item.status,
          }, [
            R.createElement("span", {
              key: "m", "aria-hidden": "true",
            }, `${statusMarker(item.status)} `),
            R.createElement("span", { key: "a" }, item.action),
            R.createElement("span", { key: "r" },
              ` · requested by ${item.requested_by || "unknown"}`),
            R.createElement("span", { key: "s" }, ` · ${item.status}`),
          ])),
      ]));

      if (sources.length > 0) {
        children.push(R.createElement("p", {
          key: "sources",
          className: "command-center__summary command-center__summary--warn",
          "data-testid": "command-center-sources",
          role: "status",
        }, "Some sources are unavailable: "
          + sources.map((source) => `${source.name}${source.code ? ` (${source.code})` : ""}`).join(", ")));
      }

      if (entries.length === 0) {
        children.push(R.createElement("div", {
          key: "empty",
          className: "command-center__state",
          "data-testid": "command-center-state",
          "data-state": "empty",
          role: "status",
          "aria-live": "polite",
        }, `No office records match${query.trim() ? ` “${query.trim()}”` : ""}`
          + `${kindFilter !== "all" ? ` in ${kindLabel(kindFilter)}` : ""}.`));
      } else {
        children.push(R.createElement("p", {
          key: "summary",
          className: "command-center__resultSummary",
          "data-testid": "command-center-result-summary",
        }, resultSummary(projection)));
        children.push(R.createElement("ul", {
          key: "results",
          id: "command-center-results",
          className: "command-center__results",
          "data-testid": "command-center-results",
          "aria-label": "Command center results",
        }, entries.map((entry, index) => {
          const href = entryDeepLink(entry, props.base);
          const common = {
            id: `command-center-entry-${index}`,
            "data-testid": "command-center-entry",
            "data-entry-id": entry.entry_id,
            "data-entry-kind": entry.kind,
            "data-status": entry.status,
            "data-attention": String(isAttention(entry)),
            "aria-current": index === active ? "true" : undefined,
            className: "command-center__entry",
            onFocus: () => setActiveIndex(index),
            onKeyDown,
            ref: (node: HTMLElement | null) => { itemRefs.current[index] = node; },
          };
          const content = [
            R.createElement("span", {
              key: "marker",
              className: "command-center__marker",
              "aria-hidden": "true",
            }, statusMarker(entry.status)),
            R.createElement("span", {
              key: "kind", className: "command-center__kind",
            }, kindLabel(entry.kind)),
            R.createElement("span", {
              key: "title", className: "command-center__title",
            }, entry.title),
            R.createElement("span", {
              key: "subtitle", className: "command-center__subtitle",
            }, entry.subtitle),
            R.createElement("span", {
              key: "status", className: "command-center__status",
            }, statusLabel(entry.status)),
            isAttention(entry)
              ? R.createElement("span", {
                key: "attention",
                className: "command-center__attention",
              }, "needs attention")
              : null,
            R.createElement("span", {
              key: "jump", className: "command-center__jump",
            }, href ? "Jump Here" : "Select"),
          ];
          if (href) {
            return R.createElement("li", {
              key: entry.entry_id,
              className: "command-center__item",
            }, R.createElement("a", {
              ...common,
              href,
              onClick: (event: { preventDefault?: () => void }) => {
                event.preventDefault?.();
                jumpTo(entry, href, props.onJump);
              },
            }, ...content));
          }
          return R.createElement("li", {
            key: entry.entry_id,
            className: "command-center__item",
          }, R.createElement("button", {
            ...common,
            type: "button",
            onClick: () => jumpTo(entry, null, props.onJump),
          }, ...content));
        })));
      }
    }

    return R.createElement("div", {
      className: "command-center",
      "data-testid": "command-center",
      "data-state": read.kind,
      role: "region",
      "aria-label": "Office command center",
      onKeyDown,
    }, children);
  };
}
