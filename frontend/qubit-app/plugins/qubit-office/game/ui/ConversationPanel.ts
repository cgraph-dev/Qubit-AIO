/* F09 — the conversation panel: the dialogue shell.
 *
 * WHAT THIS FILE IS, AND WHAT IT IS NOT
 * -------------------------------------
 * It is a VIEW over a canonical conversation stream. It is NOT a session store,
 * NOT a transport, and NOT an authority.
 *
 *   C4:    it renders states; it stages nothing and POSTs nothing. The only
 *          thing it can produce is a REQUEST DESCRIPTOR for the caller's
 *          adapter to hand to the existing authority.
 *   C7:    every state it draws comes from an inbound frame. There is no timer
 *          in this module: `typewriterStep` is called by the caller with the
 *          frame it already has, so a cosmetic reveal can never become a clock
 *          that invents progress (the typewriter must never delay settlement).
 *   D14:   no invented percent, no spinner that implies work, no
 *          chain-of-thought. "Thinking" is only ever a REAL pending request
 *          (`status: "pending"`) — see `composerEnabled`.
 *
 * THE LIVE BINDING IS ABSENT IN THIS BUILD
 * ----------------------------------------
 * `docs/game-office/frontend-game/50-BACKEND-CONTRACT.md` lists
 * `getCharacterSession(agentId, signal)` and `sendCharacterMessage(target, body,
 * requestId)` under "Proposed additive DTOs", with the explicit availability
 * behaviour "Unavailable until a compatible existing stream is bound". The only
 * office wire route that exists today is `GET /api/office/tower`
 * (`voicebridge/qubit_compat.py:9431`), which is a read-only snapshot with no
 * recipient, no session id and no message stream.
 *
 * So this panel is written against a TYPED INTERFACE (`SessionBinding`), the
 * six PREVIEW states are exact and tested, and the LIVE binding reports itself
 * BLOCKED. A typed refusal is never a working capability: `bindingStatus()`
 * below returns `blocked` with the contract line that owes it, and the panel
 * renders that as a banner instead of an empty transcript that would read as
 * "nobody has said anything".
 */

import type { OfficeTowerPayload } from "../types";
import { presentCharacter, type CharacterProfile } from "../systems/CharacterPresenter";

type ReactLike = { createElement: (...args: unknown[]) => unknown };

// ---------------------------------------------------------------------------
// The six stream states
// ---------------------------------------------------------------------------

/**
 * The distinct states the plan names. Every one of these is a REAL backend
 * condition, never a client-side mood:
 *
 *   pending    a request was submitted; nothing has been heard yet
 *   streaming  text deltas are arriving for this message id
 *   tool       a tool event for this run — the ACTUAL event, not a spinner
 *   owner_wait the run is blocked on an owner answer (a real request)
 *   error      a transport OR task failure, each with its own source
 *   stale      the connection dropped / the cursor gap was not reseeded
 */
export type StreamState =
  | "pending"
  | "streaming"
  | "tool"
  | "owner_wait"
  | "error"
  | "stale"
  | "idle";

export const STREAM_STATES: readonly StreamState[] = [
  "pending", "streaming", "tool", "owner_wait", "error", "stale",
];

/** Who the failure belongs to. A transport failure is NOT a task failure. */
export type ErrorSource = "transport" | "task";

/** One message in the transcript. */
export interface ConversationMessage {
  /** The canonical message id. Stable; the finalizer's whole job is this. */
  id: string;
  role: "owner" | "agent" | "system";
  /** The recipient this frame was addressed to. Cross-recipient frames drop. */
  recipient: number;
  text: string;
  /** True once a `done` frame finalized THIS id. */
  finalised: boolean;
  /** Artifacts the backend named. Ids only — this view fetches nothing. */
  artifacts: string[];
  /** The state this message's run is in, when it has one. */
  state: StreamState;
}

/** An inbound frame. The panel's ONLY input besides the payload. */
export interface StreamFrame {
  /** The frame's own kind, as the backend emits it. */
  kind: "delta" | "done" | "tool" | "status" | "error" | "gap";
  messageId: string;
  recipient: number;
  /** Delta text. Deltas ACCUMULATE into the id; they never replace it. */
  text?: string;
  /** A tool event's name, from the event that actually happened. */
  tool?: string;
  state?: StreamState;
  error?: { source: ErrorSource; detail: string };
  artifacts?: string[];
  /** The run this frame belongs to, so concurrent messages cannot bleed. */
  runId?: string;
  /** The session cursor this frame arrived at. */
  cursor?: number;
}

export interface ConversationState {
  /** Session id, from the binding. "" until a binding answers. */
  sessionId: string;
  recipient: number | null;
  /** Messages by id. The map IS the dedupe. */
  byId: Record<string, ConversationMessage>;
  /** Append order, so a stable render does not depend on object keys. */
  order: string[];
  /** The highest cursor applied. A lower cursor is out of order -> reseed. */
  cursor: number;
  state: StreamState;
  /** Why `state` is what it is. Never blank for a non-idle state. */
  reason: string;
  /** Owner-answerable asks the backend issued (ids + permitted option ids). */
  asks: Array<{ requestId: string; options: string[]; prompt: string }>;
  /** Set when a gap or drop was seen; cleared by a reseed. */
  needsReseed: boolean;
  /**
   * How many times each message id was FINALISED. The whole point of the
   * once-only rule: this must be 1 for every id that was finalized at all.
   */
  finalizedCount: Record<string, number>;
}

export function initialConversation(
  sessionId: string, recipient: number | null,
): ConversationState {
  return {
    sessionId, recipient, byId: {}, order: [], cursor: 0, state: "idle",
    reason: "", asks: [], needsReseed: false, finalizedCount: {},
  };
}

// ---------------------------------------------------------------------------
// Apply one frame — where the once-only finalizer lives
// ---------------------------------------------------------------------------

export interface ApplyResult {
  state: ConversationState;
  /** True when the frame changed nothing. Duplicates land here, by design. */
  ignored: boolean;
  /** Why it was ignored. Never a silent drop. */
  reason: string;
}

/**
 * Apply ONE frame.
 *
 * FOUR RULES, EACH OF WHICH IS A REAL BUG IT PREVENTS
 * ---------------------------------------------------
 * 1. **A frame for another recipient is dropped.** At ~71 seats, two runs
 *    stream at once; a frame that leaks into the wrong portrait is the
 *    cross-recipient bug the acceptance gate G08 names.
 * 2. **A delta ACCUMULATES.** Text is appended to the id, so a delta can never
 *    replace what already arrived (the "last chunk only" failure).
 * 3. **`done` FINALIZES THE ID EXACTLY ONCE.** The plan's own words: "done
 *    replaces/finalizes it, never appends the full text twice." A `done` frame
 *    that carries the whole body must not append it onto a transcript that
 *    already has the deltas, and a SECOND `done` for the same id must be a
 *    no-op. `finalizedCount[id]` is the evidence: it is bumped only on the
 *    first finalize, so a duplicate cannot hide.
 * 4. **A gap or a lower cursor requires a reseed.** Applying a stale cursor
 *    would silently reorder the transcript.
 */
export function applyFrame(
  state: ConversationState,
  frame: StreamFrame,
): ApplyResult {
  if (state.recipient !== null && frame.recipient !== state.recipient) {
    // Rule 1. Named, not silent.
    return {
      state,
      ignored: true,
      reason: `frame for seat #${frame.recipient} arrived on seat ` +
              `#${state.recipient}'s session`,
    };
  }

  if (typeof frame.cursor === "number" && frame.cursor < state.cursor) {
    return {
      state: { ...state, needsReseed: true, cursor: state.cursor },
      ignored: true,
      reason: `frame cursor ${frame.cursor} is behind ${state.cursor}; reseed`,
    };
  }
  const cursor = typeof frame.cursor === "number"
    ? Math.max(state.cursor, frame.cursor) : state.cursor;

  switch (frame.kind) {
    case "gap":
      return {
        state: {
          ...state, cursor, needsReseed: true, state: "stale",
          reason: state.reason ||
            "the stream reported a gap; the transcript must be reseeded",
        },
        ignored: false, reason: "",
      };

    case "delta": {
      const prev = state.byId[frame.messageId];
      const text = prev ? prev.text + String(frame.text ?? "")
        : String(frame.text ?? "");
      const msg: ConversationMessage = prev
        ? { ...prev, text, state: "streaming" }
        : {
            id: frame.messageId, role: "agent",
            recipient: frame.recipient, text, finalised: false,
            artifacts: [], state: "streaming",
          };
      return {
        state: put(state, msg, {
          cursor, state: "streaming",
          reason: `receiving text for ${frame.messageId}`,
        }),
        ignored: false, reason: "",
      };
    }

    case "done": {
      // THE ATTEMPT COUNTER, bumped BEFORE the guard. It counts `done` frames
      // this id RECEIVED, which is the only quantity that can witness a
      // duplicate: a count written only on the first pass would read 1 for one
      // done and 1 for five, so the "once-only" evidence would be a constant.
      const counts = { ...state.finalizedCount };
      counts[frame.messageId] = (counts[frame.messageId] ?? 0) + 1;
      const prev = state.byId[frame.messageId];
      if (prev && prev.finalised) {
        // RULE 3, THE DUPLICATE. The message does not change — no second
        // append, no second artifact merge — but the ATTEMPT is recorded and
        // the frame is named, so the duplicate is visible rather than silent.
        return {
          state: { ...state, finalizedCount: counts },
          ignored: true,
          reason: `${frame.messageId} was already finalised`,
        };
      }
      const base = prev ?? {
        id: frame.messageId, role: "agent" as const,
        recipient: frame.recipient, text: "", finalised: false,
        artifacts: [], state: "pending" as StreamState,
      };
      // THE TEXT RULE. A `done` frame carrying the full body REPLACES what the
      // deltas built (never appends to it). An empty `done` body means the
      // deltas already hold the whole message, so the accumulated text stays.
      const body = String(frame.text ?? "");
      const text = body.length > 0 ? body : base.text;
      const msg: ConversationMessage = {
        ...base, text, finalised: true, state: "idle",
        artifacts: frame.artifacts ?? base.artifacts,
      };
      return {
        state: put(
          { ...state, finalizedCount: counts }, msg, {
            cursor, state: "idle",
            reason: `${frame.messageId} finalised`,
          }),
        ignored: false, reason: "",
      };
    }

    case "tool": {
      const prev = state.byId[frame.messageId];
      // A tool frame names the tool the backend ACTUALLY used. There is no
      // path here that invents one to look busy.
      const msg: ConversationMessage = prev
        ? { ...prev, state: "tool" }
        : {
            id: frame.messageId, role: "agent", recipient: frame.recipient,
            text: "", finalised: false, artifacts: [], state: "tool",
          };
      return {
        state: put(state, msg, {
          cursor, state: "tool",
          reason: `running tool ${frame.tool ?? "(unnamed)"}`,
        }),
        ignored: false, reason: "",
      };
    }

    case "status": {
      const next = frame.state ?? "pending";
      let asks = state.asks;
      if (next === "owner_wait") {
        // The ask carries its OWN request id and permitted option ids; the
        // panel never generates an actionable option client-side.
        const ask = {
          requestId: frame.runId ?? frame.messageId,
          options: frame.artifacts ?? [],
          prompt: String(frame.text ?? ""),
        };
        if (!asks.some((a) => a.requestId === ask.requestId)) {
          asks = [...asks, ask];
        }
      }
      return {
        state: {
          ...state, cursor, state: next, asks,
          reason: stateReason(next, frame.tool),
        },
        ignored: false, reason: "",
      };
    }

    case "error": {
      const src = frame.error?.source ?? "transport";
      return {
        state: {
          ...state, cursor, state: "error",
          // A transport failure is NOT a task failure. The panel says which,
          // because they imply different next actions.
          reason: `${src === "transport" ? "connection" : "run"} failed: ` +
                  `${frame.error?.detail ?? "no detail reported"}`,
        },
        ignored: false, reason: "",
      };
    }

    default:
      return { state, ignored: true, reason: "unknown frame kind" };
  }
}

function stateReason(state: StreamState, tool?: string): string {
  switch (state) {
    case "pending": return "a request is outstanding";
    case "streaming": return "text is arriving";
    case "tool": return `running tool ${tool ?? "(unnamed)"}`;
    case "owner_wait": return "this run is waiting for you to answer";
    case "error": return "the run failed";
    case "stale": return "the stream is not current";
    default: return "";
  }
}

function put(
  state: ConversationState,
  msg: ConversationMessage,
  patch: Partial<ConversationState>,
): ConversationState {
  const known = state.byId[msg.id] !== undefined;
  return {
    ...state,
    ...patch,
    byId: { ...state.byId, [msg.id]: msg },
    order: known ? state.order : [...state.order, msg.id],
  };
}

/** Apply a whole batch, oldest first. Returns the final state. */
export function applyFrames(
  state: ConversationState,
  frames: StreamFrame[],
): ConversationState {
  let cur = state;
  for (const f of frames) cur = applyFrame(cur, f).state;
  return cur;
}

/** The transcript, in arrival order. */
export function transcript(state: ConversationState): ConversationMessage[] {
  return state.order.map((id) => state.byId[id]).filter(Boolean);
}

/**
 * THE FALSIFIABLE INVARIANT. Every message must have received EXACTLY ONE
 * `done` frame, and it must have been applied once. A `2` here is the
 * duplicate-append bug, stated as data.
 *
 * `finalizedCount` counts done FRAMES RECEIVED, bumped before the guard, so a
 * duplicate is visible here even though the message itself never changed twice.
 * A count written only on the first pass would read 1 for one frame and 1 for
 * five — a constant cannot witness anything.
 */
export function finalizeAudit(state: ConversationState): {
  duplicated: string[]; counts: Record<string, number>; allOnce: boolean;
} {
  const duplicated = Object.keys(state.finalizedCount)
    .filter((id) => state.finalizedCount[id] !== 1);
  return {
    duplicated,
    counts: { ...state.finalizedCount },
    allOnce: duplicated.length === 0
      && Object.keys(state.finalizedCount).length > 0,
  };
}

// ---------------------------------------------------------------------------
// The honest banner: what is bound, and what owes it
// ---------------------------------------------------------------------------

export interface BindingStatus {
  status: "blocked" | "available";
  /** The contract line that owes this binding, verbatim. */
  owedBy: string;
  reason: string;
}

/**
 * Whether the canonical recipient/session stream is bound.
 *
 * IT IS NOT, IN THIS BUILD, AND THE PANEL SAYS SO. The only office wire route
 * that exists is the read-only tower snapshot; there is no
 * `getCharacterSession` and no recipient-addressed message stream. A panel that
 * rendered an empty transcript here would read as "nobody has spoken", which is
 * a claim about the org rather than about the missing binding.
 */
export function bindingStatus(bound: boolean): BindingStatus {
  if (bound) {
    return {
      status: "available", owedBy: "",
      reason: "the canonical session stream answered",
    };
  }
  return {
    status: "blocked",
    owedBy: "50-BACKEND-CONTRACT.md § Frontend adapter contract — " +
            "`getCharacterSession` / `sendCharacterMessage` " +
            "(availability: \"Unavailable until a compatible existing stream " +
            "is bound\")",
    reason: "no canonical recipient/session stream is bound in this build, so " +
            "this conversation is a PREVIEW: the states are exact, but nothing " +
            "here is live.",
  };
}

// ---------------------------------------------------------------------------
// The composer: what the owner may do, and what is refused
// ---------------------------------------------------------------------------

/** A message send, as a REQUEST. Never the send itself (C4). */
export interface SendRequest {
  /** The canonical recipient. A message with no recipient is not sendable. */
  recipient: number;
  /** The owner's text. */
  body: string;
  /** Derived from the content, so a retry is recognised as the same request. */
  requestId: string;
}

export interface ComposerView {
  enabled: boolean;
  /** Named reason when disabled. */
  reason: string;
  /** True when text is non-empty. */
  hasText: boolean;
}

/**
 * Whether the composer can send. Three separate refusals, each named:
 *
 *   * no recipient selected — "Unavailable ... No fictional session created
 *     locally" (50-BACKEND-CONTRACT). A generic chat with an undisclosed target
 *     is exactly what the design doc forbids.
 *   * the binding is blocked — the send path does not exist yet.
 *   * empty text — nothing to send.
 *
 * NOTE `owner_wait` and `tool` DO NOT disable the composer: a run waiting on an
 * answer is a run you can still talk to.
 */
export function composerView(
  state: ConversationState,
  body: string,
  bound: boolean,
): ComposerView {
  const text = String(body ?? "").trim();
  if (state.recipient === null) {
    return {
      enabled: false, hasText: text.length > 0,
      reason: "no recipient is selected; this build never opens a generic chat",
    };
  }
  if (!bound) {
    return {
      enabled: false, hasText: text.length > 0,
      reason: "the conversation binding is not available in this build",
    };
  }
  if (text.length === 0) {
    return { enabled: false, hasText: false, reason: "nothing to send" };
  }
  return { enabled: true, hasText: true, reason: "" };
}

export function requestIdFor(sessionId: string, recipient: number, body: string):
    string {
  // A DERIVED id, not a random one: a retry of the same message rebuilds the
  // SAME id, so the authority can recognise it instead of appending twice.
  const s = `${sessionId}|${recipient}|${String(body ?? "").trim()}`;
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `msg-${h.toString(16).padStart(8, "0")}`;
}

/**
 * Build the send REQUEST. This function does not send it. `bound` is checked
 * here too, so a caller cannot reach the network path by forgetting.
 */
export function buildSendRequest(
  state: ConversationState, body: string, bound: boolean,
): SendRequest | { ok: false; reason: string } {
  const ok = composerView(state, body, bound);
  if (!ok.enabled) return { ok: false, reason: ok.reason };
  return {
    recipient: state.recipient as number,
    body: String(body).trim(),
    requestId: requestIdFor(state.sessionId, state.recipient as number, body),
  };
}

// ---------------------------------------------------------------------------
// Typewriter vs instant — cosmetic, and it never delays settlement
// ---------------------------------------------------------------------------

export type RevealMode = "instant" | "typewriter";

export interface RevealState {
  messageId: string;
  /** How many characters of `fullText` are visible. */
  shown: number;
  /** The VISIBLE PREFIX. What to paint. */
  text: string;
  /**
   * The whole message. Carried so `skipReveal` can reach the real end —
   * without it, "Skip" would only be able to jump to `text`, which is already
   * visible, and would report a done reveal for a truncated message.
   */
  fullText: string;
  done: boolean;
}

/**
 * Reveal progress. Pure: the caller passes the CHARACTERS IT ALREADY HAS, so a
 * typewriter can never fetch, and can never hold settlement back — the message
 * is already finalised in the state; this only decides how much of it to paint.
 *
 * `Instant` is `shown = text.length`. Both modes end at the same place.
 */
export function revealFor(
  text: string,
  mode: RevealMode,
  messageId: string,
  shown: number,
): RevealState {
  const full = String(text ?? "");
  const n = mode === "instant" ? full.length
    : Math.max(0, Math.min(full.length, Math.floor(shown)));
  return {
    messageId, shown: n, text: full.slice(0, n), fullText: full,
    done: n >= full.length,
  };
}

/** Advance a typewriter by one step. Returns the next `shown` count. */
export function typewriterStep(shown: number, total: number, perStep = 3):
    number {
  return Math.min(Math.max(0, total), Math.max(0, shown) + Math.max(1, perStep));
}

/** Skip to the end. The plan requires an Instant/Skip escape from the reveal. */
export function skipReveal(state: RevealState): RevealState {
  // `state.text` is the VISIBLE PREFIX, not the whole message — passing it as
  // the body would make "Skip" jump to the few characters already shown and
  // call that done. The caller keeps the source text; `RevealState.fullText`
  // is where it lives, so Skip can only ever go to the real end.
  return {
    messageId: state.messageId,
    shown: state.fullText.length,
    text: state.fullText,
    fullText: state.fullText,
    done: true,
  };
}

// ---------------------------------------------------------------------------
// Interruption — explicit, and never triggered by closing the panel
// ---------------------------------------------------------------------------

/**
 * What an interrupt actually stops. THREE SEPARATE THINGS, never merged:
 * the design doc: "Cancel/interrupt is explicit and names whether it stops
 * speech, cancels this request or stops an agent. Never turn 'close dialog' into
 * emergency stop."
 */
export type InterruptScope = "speech" | "request" | "agent";

export interface InterruptRequest {
  scope: InterruptScope;
  runId: string;
  /** The label the control shows, so the owner knows what they are stopping. */
  label: string;
}

export function interruptRequest(
  scope: InterruptScope, runId: string,
): InterruptRequest {
  const label = scope === "speech" ? "Stop speaking"
    : scope === "request" ? "Cancel this request"
      : "Stop this agent";
  return { scope, runId, label };
}

/**
 * THE RULE THAT MATTERS MOST HERE: closing the panel is NOT an interrupt.
 *
 * Returns the request to issue — `null` for a close, ALWAYS, whatever the
 * stream state. A close that cancelled a run would make the X button an
 * emergency stop, which is the single failure this function exists to prevent.
 */
export function onPanelClose(): InterruptRequest | null {
  return null;
}

/**
 * What a run's interrupt state IS, read from the frames — not from the fact
 * that we asked. "Interrupt acknowledgement is not proof of cancellation until
 * observed state confirms it" (50-BACKEND-CONTRACT).
 *
 * `runId` may be a MESSAGE id (a delta/done stream) or a RUN id (a status
 * frame). Both are checked, because a status frame creates no transcript row:
 * reading only `byId` reported `unknown` for a run that was plainly streaming.
 * An unknown run is still `unknown` — never assumed cancelled.
 */
export function interruptObserved(
  state: ConversationState, runId: string,
): "running" | "cancelled" | "unknown" {
  const msg = state.byId[runId];
  if (msg) {
    // A finalised message is a COMPLETED run. That is not the same as an
    // acknowledged interrupt, so it is reported as `cancelled` only when the
    // stream also stopped — i.e. the message is done. Anything else is running.
    return msg.finalised ? "cancelled" : "running";
  }
  const ask = state.asks.find((a) => a.requestId === runId);
  if (ask) {
    // A known ask whose run is mid-stream: the run exists and is live.
    return state.state === "error" ? "unknown" : "running";
  }
  if (state.state === "error" || state.state === "stale") return "unknown";
  return state.state === "idle" ? "unknown" : "running";
}

// ---------------------------------------------------------------------------
// Follow-up + artifacts
// ---------------------------------------------------------------------------

/**
 * A follow-up is a send to the SAME recipient carrying the message it follows.
 * It is still a REQUEST; nothing is appended locally.
 */
export function followUpRequest(
  state: ConversationState, parentId: string, body: string, bound: boolean,
): (SendRequest & { parentId: string }) | { ok: false; reason: string } {
  const req = buildSendRequest(state, body, bound);
  if ("ok" in req) return req;
  return { ...req, parentId };
}

/**
 * The artifacts a message named. IDs only: this view opens nothing and reads
 * nothing. A caller hands the refs to the existing artifact authority, which
 * applies its own permissions and redaction.
 */
export interface ArtifactRef {
  id: string;
  /** True when the ref came from a finalized message (it is settled). */
  settled: boolean;
}

export function artifactRefs(
  state: ConversationState, messageId: string,
): ArtifactRef[] {
  const msg = state.byId[messageId];
  if (!msg) return [];
  return msg.artifacts.map((id) => ({ id, settled: msg.finalised }));
}

// ---------------------------------------------------------------------------
// Render — the host's React, no `react` import
// ---------------------------------------------------------------------------

export interface ConversationPanelProps {
  R: ReactLike;
  payload: OfficeTowerPayload | null;
  state: ConversationState;
  /** Whether the canonical session stream is actually bound. */
  bound?: boolean;
  /** The owner's draft text. */
  draft?: string;
  reveal?: RevealState;
  /** Initials, per the plan. Cosmetic only. */
  mode?: RevealMode;
  /** Called with a REQUEST the caller hands to the authority. Never sent here. */
  onSend?: (req: SendRequest) => void;
  onInterrupt?: (req: InterruptRequest) => void;
}

/** The named state chip. `data-state` is the machine-readable version. */
function stateChip(R: ReactLike, state: ConversationState): unknown {
  return R.createElement("div", {
    key: "state",
    "data-testid": "conversation-state",
    "data-state": state.state,
    role: "status",
    "aria-live": "polite",
    className: "office-game__stat-label",
  }, state.reason
    ? `${state.state} — ${state.reason}`
    : state.state);
}

/**
 * The whole panel.
 *
 * HONESTY BANNER FIRST: when the binding is blocked, the banner is rendered
 * ABOVE the transcript and names the contract that owes it, because a blank
 * transcript reads as "nobody spoke" and that is a claim about the org.
 */
export function renderConversationPanel(
  props: ConversationPanelProps,
): unknown {
  const { R, state } = props;
  const bound = props.bound === true;
  const binding = bindingStatus(bound);
  const messages = transcript(state);
  const rows = Array.isArray(props.payload?.characters)
    ? props.payload!.characters : [];
  const recipient: CharacterProfile | null = state.recipient === null ? null
    : (() => {
        const raw = rows.find(
          (c) => Number(c.agent_id) === Number(state.recipient));
        return raw
          ? presentCharacter(
              raw, rows, Number(props.payload?.active_window_s ?? 0))
          : null;
      })();

  const banner = binding.status === "blocked"
    ? R.createElement("div", {
        key: "blocked", role: "status",
        "data-testid": "conversation-binding-blocked",
        "data-status": binding.status,
        "data-owed-by": binding.owedBy,
        className: "office-game__refusal",
      }, [
        R.createElement("div", { key: "t" }, "LIVE INTEGRATION BLOCKED"),
        R.createElement("div", {
          key: "r", style: { fontSize: "var(--office-fs-xs)" },
        }, binding.reason),
        R.createElement("div", {
          key: "o", style: { fontSize: "var(--office-fs-xs)" },
          "data-testid": "conversation-binding-owed-by",
        }, `Owed by: ${binding.owedBy}`),
      ])
    : null;

  const head = R.createElement("header", {
    key: "head", "data-testid": "conversation-recipient",
    "data-recipient": state.recipient === null ? "" : String(state.recipient),
    "data-session": state.sessionId,
    "data-bound": String(bound),
  }, [
    // Speaker attribution + recipient + scope. The design doc requires all
    // three; a generic chat with an undisclosed target is the failure.
    R.createElement("div", { key: "who" },
      recipient ? `Talking to ${recipient.slug}` : "No recipient selected"),
    R.createElement("div", {
      key: "scope", style: { fontSize: "var(--office-fs-xs)" },
    }, `session ${state.sessionId || "(none)"} · profile scope: this panel ` +
       `only · ${bound ? "live" : "preview"}`),
  ]);

  const body = messages.length > 0
    ? R.createElement("ul", {
        key: "log", "data-testid": "conversation-transcript",
        style: { margin: 0, paddingLeft: 0, listStyle: "none" },
      }, messages.map((m) => R.createElement("li", {
        key: m.id,
        "data-testid": `conversation-message-${m.id}`,
        "data-message-id": m.id,
        "data-role": m.role,
        "data-finalised": String(m.finalised),
        "data-state": m.state,
        "data-artifacts": String(m.artifacts.length),
      }, [
        R.createElement("strong", { key: "r" }, `${m.role}: `),
        R.createElement("span", { key: "t" },
          // The reveal applies ONLY to the message being revealed; every other
          // message shows its full text.
          props.reveal && props.reveal.messageId === m.id
            ? props.reveal.text : m.text),
      ])))
    : R.createElement("div", {
        key: "empty", "data-testid": "conversation-transcript-empty",
      }, bound
        ? "Nothing has been said in this session."
        : "No transcript: the canonical session stream is not bound in this " +
          "build.");

  const composer = composerView(state, props.draft ?? "", bound);
  const asks = state.asks.length > 0
    ? R.createElement("div", {
        key: "asks", "data-testid": "conversation-asks",
      }, state.asks.map((a) => R.createElement("div", {
        key: a.requestId, "data-request-id": a.requestId,
        "data-options": String(a.options.length),
      }, `${a.prompt || "a request needs your answer"} ` +
         `(${a.options.join(", ") || "no options issued"})`)))
    : null;

  const controls = R.createElement("div", {
    key: "ctrls", "data-testid": "conversation-controls",
  }, [
    R.createElement("button", {
      key: "send", type: "button", className: "office-game__btn",
      "data-testid": "conversation-send",
      disabled: !composer.enabled,
      "data-reason": composer.reason,
      "aria-label": composer.enabled ? "Send" : composer.reason,
    }, "Send"),
    R.createElement("button", {
      key: "skip", type: "button", className: "office-game__btn",
      "data-testid": "conversation-instant",
      // An Instant/Skip escape is REQUIRED for the cosmetic reveal. It never
      // touches settlement: the message is already finalised.
      disabled: !props.reveal || props.reveal.done,
    }, "Instant text"),
    // Three separate interrupts. Each names its scope; none of them is "close".
    ...(["speech", "request", "agent"] as InterruptScope[]).map((scope) =>
      R.createElement("button", {
        key: `int-${scope}`, type: "button", className: "office-game__btn",
        "data-testid": `conversation-interrupt-${scope}`,
        "data-scope": scope,
        title: interruptRequest(scope, "").label,
      }, interruptRequest(scope, "").label)),
    R.createElement("button", {
      key: "close", type: "button", className: "office-game__btn",
      "data-testid": "conversation-close",
      // Stated on the control itself: closing does not cancel the run.
      title: "Close this panel. The run continues.",
    }, "Close (the run continues)"),
  ]);

  return R.createElement("section", {
    "data-testid": "office-conversation-panel",
    // `reseed` is surfaced as data so the reconnect path can be asserted.
    "data-needs-reseed": String(state.needsReseed),
    "data-state": state.state,
    role: "region", "aria-label": "Conversation",
  }, [
    banner, head, stateChip(R, state), asks, body, controls,
  ]);
}
