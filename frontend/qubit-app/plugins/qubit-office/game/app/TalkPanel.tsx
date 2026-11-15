/* The agent TALK panel: one roster seat's real canonical conversation.
 *
 * WHY IT LIVES IN THE QUBIT SHELL
 * ------------------------------
 * The vendored office document has no hook for a Qubit panel and its bridge
 * refuses every non-GET. So, like the Agents panel and the seat model panel,
 * this is part of the shell AROUND the iframe: it reads and writes the
 * canonical chat store over real HTTP with the host's session token.
 *
 * WHAT IT SHOWS
 * -------------
 * The seat's portrait/name/role/room from the roster, the transcript the
 * CANONICAL chat-session store holds for `office-talk-<slug>`, and a composer
 * that submits the existing non-streaming chat turn. A seat with no session is
 * a typed empty state with an explicit "Start conversation" action; a refused
 * token/route is a typed unavailable state with the composer disabled. No
 * message, reply, or progress line is ever fabricated.
 *
 * React comes from the HOST: `R.createElement`, never an import.
 */
import type { OfficeGameShellProps } from "../OfficeGameShell";
import { seatTypeLabel, type AgentSeat } from "./agents-panel";
import {
  type OfficeTheme,
  initialsFor,
  officeUiSliceAttrs,
  portraitCellStyle,
  themeAtlasUrls,
} from "./office-theme";
import {
  type TalkMessage,
  type TalkState,
  fetchTalkTranscript,
  sendTalkMessage,
  startTalkSession,
  talkSessionId,
  visibleTalkMessages,
} from "./talk-panel";

export interface TalkPanelProps {
  /** The roster seat whose conversation this is. */
  seat: AgentSeat;
  /** The active theme payload (portrait atlas + Modern UI slices). */
  theme?: OfficeTheme | null;
  /** An explicit base path (a test); otherwise the host's injected global. */
  base?: string;
  /** Injected for tests; defaults to the page's own fetch. */
  fetchImpl?: typeof fetch;
  /** Close the dock (the shell owns the dock; this is the content). */
  onClose: () => void;
}

/** The portrait display box (the pack's tier is normally 16 or 32). */
const PORTRAIT_BOX = 32;

/**
 * The portrait atlas cell size. The payload exposes the BODY tier; the builder
 * emits the same tier for portraits. The sprite is displayed at its native
 * cell size: `background-size: 200%` would be 200% of the ELEMENT, not of the
 * atlas, and would misplace every cell after the first.
 */
function portraitCellPx(theme: OfficeTheme | null | undefined): number {
  const size = theme?.bodies?.size;
  return typeof size === "number" && Number.isFinite(size) && size > 0
    ? Math.round(size) : PORTRAIT_BOX;
}

function seatAvatar(
  R: OfficeGameShellProps["React"],
  seat: AgentSeat,
  theme: OfficeTheme | null | undefined,
) {
  const urls = themeAtlasUrls(theme);
  const displayPx = Math.min(PORTRAIT_BOX, portraitCellPx(theme));
  const style = seat.portrait
    ? portraitCellStyle(seat.portrait, displayPx)
    : undefined;
  if (urls.portrait && style) {
    return R.createElement("span", {
      key: "portrait",
      className: "office-talk__portrait",
      "data-testid": "talk-portrait",
      role: "img",
      "aria-label": `${seat.name} portrait`,
      "data-sprite": "portrait",
      style: {
        ...style,
        backgroundImage: `url("${urls.portrait}")`,
        backgroundSize: "auto",
        width: `${displayPx}px`,
        height: `${displayPx}px`,
      },
    });
  }
  const initials = initialsFor(seat.name);
  if (initials) {
    return R.createElement("span", {
      key: "portrait",
      className: "office-talk__initials",
      "data-testid": "talk-portrait",
      role: "img",
      "aria-label": `${seat.name} initials`,
      "data-sprite": "initials",
    }, initials);
  }
  return R.createElement("span", {
    key: "portrait",
    className: "office-talk__emoji",
    "data-testid": "talk-portrait",
    role: "img",
    "aria-label": seat.name ? `${seat.name} portrait` : "Agent portrait",
    "data-sprite": "emoji",
  }, (seat.emoji || "").trim() || "\u2022");
}

function stateLabel(state: TalkState): string {
  switch (state.kind) {
    case "loading": return "Loading conversation…";
    case "empty": return "No conversation yet";
    case "ready": return "Conversation open";
    default: return "Conversation unavailable";
  }
}

function stateCode(state: TalkState): string {
  if (state.kind === "empty" || state.kind === "unavailable") return state.code;
  return "";
}

function messageRow(
  R: OfficeGameShellProps["React"],
  message: TalkMessage,
  index: number,
  seatName: string,
) {
  const mine = message.role === "user";
  return R.createElement("li", {
    key: `${message.role}:${index}:${message.timestamp ?? ""}`,
    className: "office-talk__message office-talk__message--"
      + (mine ? "user" : "assistant"),
    "data-testid": "talk-message",
    "data-role": message.role,
  }, [
    R.createElement("span", {
      key: "a", className: "office-talk__author",
    }, mine ? "You" : seatName),
    R.createElement("span", {
      key: "t", className: "office-talk__text",
    }, message.content),
  ]);
}

export function createTalkPanel(
  R: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
) {
  const { useState, useEffect, useRef } = hooks;

  return function TalkPanel(props: TalkPanelProps) {
    const [state, setState] = useState<TalkState>({ kind: "loading" });
    const [draft, setDraft] = useState("");
    const [sending, setSending] = useState(false);
    const [notice, setNotice] = useState("");
    const listRef = useRef<HTMLOListElement | null>(null);
    const seat = props.seat;
    const sessionId = talkSessionId(seat);
    const theme = props.theme ?? null;

    // Read the canonical transcript whenever the seat changes. The guard is
    // the seat identity, not a timer: no polling, no invented live updates.
    useEffect(() => {
      let live = true;
      setState({ kind: "loading" });
      setNotice("");
      fetchTalkTranscript(seat, {
        base: props.base, fetchImpl: props.fetchImpl,
      }).then((next) => { if (live) setState(next); });
      return () => { live = false; };
    }, [seat.slug, seat.name, props.base]);

    // Keep the newest real row visible after a load or a completed send.
    useEffect(() => {
      if (state.kind !== "ready") return;
      const node = listRef.current;
      if (node && typeof node.scrollIntoView === "function") {
        node.scrollIntoView({ block: "nearest" });
      }
    }, [state.kind === "ready" ? state.messages.length : -1]);

    const tokenOk = state.kind === "ready"
      || state.kind === "empty";
    const canCompose = state.kind === "ready" && tokenOk && !sending;
    const status = stateLabel(state);
    const code = stateCode(state);

    const onKeyDown = (event: { key: string; preventDefault?: () => void }) => {
      if (event.key !== "Escape") return;
      event.preventDefault?.();
      props.onClose();
    };

    const onStart = async () => {
      setNotice("");
      setState({ kind: "loading" });
      const next = await startTalkSession(seat, {
        base: props.base, fetchImpl: props.fetchImpl,
      });
      setState(next);
      if (next.kind === "unavailable") {
        setNotice(`[${next.code}] ${next.detail}`);
      }
    };

    const onSubmit = async (event: { preventDefault: () => void }) => {
      event.preventDefault();
      const text = String(draft || "").trim();
      if (!text || !canCompose) return;
      setSending(true);
      setNotice("");
      const result = await sendTalkMessage(seat, text, {
        base: props.base, fetchImpl: props.fetchImpl,
      });
      if (!result.ok) {
        setNotice(`[${result.code}] ${result.detail}`);
        setSending(false);
        return;
      }
      setDraft("");
      // Re-read the canonical store instead of appending an optimistic copy:
      // the real rows (including the just-completed turn) are the transcript.
      const next = await fetchTalkTranscript(seat, {
        base: props.base, fetchImpl: props.fetchImpl,
      });
      setState(next);
      setSending(false);
    };

    const onComposerKeyDown = (event: {
      key: string; shiftKey?: boolean; preventDefault?: () => void;
    }) => {
      if (event.key !== "Enter" || event.shiftKey) return;
      event.preventDefault?.();
      void onSubmit({ preventDefault: () => {} });
    };

    const portrait = seatAvatar(R, seat, theme);
    const role = seatTypeLabel(seat.seat_type);
    const room = seat.room
      ? (seat.room === "boardroom" ? "Boardroom" : seat.room)
      : "No room";
    const visible = state.kind === "ready"
      ? visibleTalkMessages(state.messages) : [];
    const emptyTranscript = state.kind === "ready" && visible.length === 0;
    const canStart = state.kind === "empty";

    return R.createElement("section", {
      className: "office-talk",
      "data-testid": "talk-panel",
      "data-state": state.kind,
      "data-code": code || undefined,
      "aria-label": `Talk with ${seat.name}`,
      onKeyDown,
    }, [
      R.createElement("header", {
        key: "head", className: "office-talk__head",
      }, [
        portrait,
        R.createElement("div", { key: "who", className: "office-talk__who" }, [
          R.createElement("h2", {
            key: "n", className: "office-talk__name",
            "data-testid": "talk-name",
          }, seat.name),
          R.createElement("p", {
            key: "r", className: "office-talk__meta",
            "data-testid": "talk-role",
          }, [
            R.createElement("span", { key: "role" }, role),
            R.createElement("span", { key: "sep" }, " · "),
            R.createElement("span", { key: "room" }, room),
            R.createElement("span", { key: "sep2" }, " · "),
            R.createElement("span", { key: "div" }, seat.division || "Boardroom"),
          ]),
          R.createElement("p", {
            key: "s", className: "office-talk__status",
            "data-testid": "talk-status",
            "aria-live": "polite",
          }, status),
        ]),
        R.createElement("button", {
          key: "x", type: "button",
          className: "office-game__btn office-theme-dock-close",
          "data-testid": "talk-close",
          "aria-label": `Close conversation with ${seat.name}`,
          onClick: () => props.onClose(),
          ...officeUiSliceAttrs(theme, "panel", true),
        }, "Close"),
      ]),
      R.createElement("div", {
        key: "body", className: "office-talk__body",
      }, [
        state.kind === "loading"
          ? R.createElement("p", {
              key: "loading", className: "office-talk__note",
              "data-testid": "talk-loading", role: "status",
            }, "Reading the canonical conversation…")
          : null,
        state.kind === "unavailable"
          ? R.createElement("div", {
              key: "unavailable", className: "office-talk__unavailable",
              "data-testid": "talk-unavailable", role: "alert",
            }, [
              R.createElement("p", { key: "d" }, state.detail),
              R.createElement("p", {
                key: "c", className: "office-talk__code",
              }, state.code),
            ])
          : null,
        state.kind === "empty"
          ? R.createElement("div", {
              key: "empty", className: "office-talk__empty",
              "data-testid": "talk-empty",
            }, [
              R.createElement("p", { key: "d" }, state.detail),
              R.createElement("button", {
                key: "start", type: "button",
                className: "office-game__btn office-talk__start",
                "data-testid": "talk-start",
                onClick: () => { void onStart(); },
                ...officeUiSliceAttrs(theme, "panel", true),
              }, "Start conversation"),
            ])
          : null,
        state.kind === "ready"
          ? (emptyTranscript
            ? R.createElement("p", {
                key: "empty-transcript", className: "office-talk__note",
                "data-testid": "talk-empty-transcript",
              }, "No user or assistant messages in this conversation yet.")
            : R.createElement("ol", {
                key: "transcript", ref: listRef,
                className: "office-talk__transcript",
                "data-testid": "talk-transcript",
                "aria-label": `Conversation with ${seat.name}`,
              }, visible.map((message, index) =>
                messageRow(R, message, index, seat.name))))
          : null,
      ]),
      R.createElement("form", {
        key: "composer", className: "office-talk__composer",
        onSubmit,
        "data-testid": "talk-composer",
      }, [
        R.createElement("label", {
          key: "l", className: "office-talk__label",
          htmlFor: `talk-input-${sessionId}`,
        }, `Message ${seat.name}`),
        R.createElement("textarea", {
          key: "i", id: `talk-input-${sessionId}`,
          className: "office-talk__input",
          "data-testid": "talk-input",
          rows: 2,
          value: draft,
          disabled: !canCompose,
          "aria-disabled": canCompose ? undefined : "true",
          "aria-describedby": "talk-composer-note",
          placeholder: canStart
            ? "Start the conversation to write a message."
            : (state.kind === "unavailable"
              ? "Conversation unavailable."
              : "Write a message…"),
          onChange: (event: { target: { value: string } }) =>
            setDraft(event.target.value),
          onKeyDown: onComposerKeyDown,
          ...officeUiSliceAttrs(theme, "panel", true),
        }),
        R.createElement("div", {
          key: "actions", className: "office-talk__actions",
        }, [
          R.createElement("button", {
            key: "send", type: "submit",
            className: "office-game__btn office-talk__send",
            "data-testid": "talk-send",
            disabled: !canCompose || !String(draft).trim(),
            "aria-busy": sending ? "true" : undefined,
            ...officeUiSliceAttrs(theme, "panel", true),
          }, sending ? "Sending…" : "Send"),
        ]),
        R.createElement("p", {
          key: "note", id: "talk-composer-note",
          className: "office-talk__composerNote",
          "data-testid": "talk-composer-note",
          role: "status",
          "aria-live": "polite",
        }, sending
          ? "Waiting for the agent's real reply…"
          : (state.kind === "empty"
            ? "Start the conversation first; messages go through the canonical chat store."
            : (state.kind === "unavailable"
              ? `Composer disabled: ${state.code}. ${state.detail}`
              : (notice || "Enter sends · Shift+Enter adds a line.")))),
        notice && state.kind === "ready"
          ? R.createElement("p", {
              key: "notice", className: "office-talk__error",
              "data-testid": "talk-error", role: "alert",
            }, notice)
          : null,
      ]),
    ]);
  };
}
