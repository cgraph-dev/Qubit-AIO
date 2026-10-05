/**
 * `/api/events` subscriber for the dashboard chat sidebar.
 *
 * A notification-only view of the shared JSON-RPC client: the server never
 * answers requests on this socket (subscribers don't speak), so `request()` is
 * not part of the surface. One instance lives for the component's whole life —
 * the owner calls `connect()` again after a drop instead of building a new
 * client — so the shared per-generation guards (stale-socket close, connect
 * timeout, `error`+`close` collapsed into one `closed` transition) apply to
 * every attempt.
 *
 * Ported from the pinned upstream `web/src/lib/eventsFeedClient.ts`
 * (`d337b736aa1e8ebecfab043842d13e4a2d2f48a3`) with the rename map
 * (`@nous/shared` → `@qubit/shared`). Qubit's shared client does not expose
 * an upstream `replay` option, so that constructor field is omitted; the
 * Qubit overlay drives replay internally.
 */

import {
  JsonRpcGatewayClient,
  type ConnectionState,
  type GatewayEvent,
  type GatewayEventName,
} from "@qubit/shared";

import { buildWsUrl } from "@/lib/api";
import { maybeReloadForLoopbackWsAuthFailure } from "@/lib/dashboard-auth-reload";
import { EVENTS_CONNECT_TIMEOUT_MS } from "@/lib/events-reconnect";

export type { ConnectionState, GatewayEvent, GatewayEventName };

type CloseHandler = (code: number | undefined) => void;

export class EventsFeedClient extends JsonRpcGatewayClient {
  private readonly closeHandlers = new Set<CloseHandler>();
  private closeCode: number | null | undefined = undefined;

  constructor() {
    super({
      closedErrorMessage: "events feed closed",
      connectErrorMessage: "events feed connection failed",
      connectTimeoutMs: EVENTS_CONNECT_TIMEOUT_MS,
      heartbeatDeadlineMs: 0,
      heartbeatIntervalMs: 0,
      onSocketClose: (event) => {
        this.closeCode = event.code;
        // Loopback + stale token: the page reloads; nobody should retry.
        return maybeReloadForLoopbackWsAuthFailure(event.code);
      },
      requestIdPrefix: "e",
    });
    this.onState((state) => {
      if (state === "closed" || state === "error") {
        const code = this.closeCode ?? undefined;
        this.closeCode = undefined;
        for (const handler of this.closeHandlers) {
          handler(code);
        }
      }
    });
  }

  /** Close code of the most recent drop; `null` before any socket was dialed. */
  get lastCloseCode(): number | null | undefined {
    return this.closeCode;
  }

  /**
   * Called once per generation that ends without opening or after opening:
   * `code` is the WebSocket close code, or `undefined` when the handshake
   * timed out / errored without a close frame.
   */
  onClose(handler: CloseHandler): () => void {
    this.closeHandlers.add(handler);
    return () => this.closeHandlers.delete(handler);
  }

  /** Mint a fresh single-use ticket and dial `/api/events` for `channel`. */
  async connect(channel: string): Promise<void> {
    this.closeCode = null;
    // Cover ticket minting with the same deadline as the handshake: a stalled
    // pre-socket request otherwise emits no close event and strands the retry
    // loop at its last "reconnecting in …" banner.
    const url = await withTimeout(
      buildWsUrl("/api/events", { channel }),
      EVENTS_CONNECT_TIMEOUT_MS,
    );
    await super.connect(url);
  }
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error("events feed ticket request timed out")),
      ms,
    );
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error instanceof Error ? error : new Error(String(error)));
      },
    );
  });
}
