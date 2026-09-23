/**
 * Browser WebSocket client for the tui_gateway JSON-RPC protocol.
 *
 * Speaks the exact same newline-delimited JSON-RPC dialect that the Ink TUI
 * drives over stdio. The server-side transport abstraction
 * (tui_gateway/transport.py + ws.py) routes the same dispatcher's writes
 * onto either stdout or a WebSocket depending on how the client connected.
 *
 *   const gw = new GatewayClient()
 *   await gw.connect()
 *   const { session_id } = await gw.request<{ session_id: string }>("session.create")
 *   gw.on("message.delta", (ev) => console.log(ev.payload?.text))
 *   await gw.request("prompt.submit", { session_id, text: "hi" })
 */

import {
  JsonRpcGatewayClient,
  buildQubitWebSocketUrl,
  type ConnectionState,
  type GatewayEvent,
  type GatewayEventName,
} from "@qubit/shared";

import { QUBIT_BASE_PATH, buildWsAuthParam, withApiPrefix } from "@/lib/api";
import { maybeReloadForLoopbackWsAuthFailure } from "@/lib/dashboard-auth-reload";

export type { ConnectionState, GatewayEvent, GatewayEventName };

export class GatewayClient extends JsonRpcGatewayClient {
  constructor() {
    super({
      closedErrorMessage: "WebSocket closed",
      connectErrorMessage: "WebSocket connection failed",
      notConnectedErrorMessage: "gateway not connected",
      onSocketClose: (event) => maybeReloadForLoopbackWsAuthFailure(event.code),
      requestIdPrefix: "w",
    });
  }

  async connect(token?: string): Promise<void> {
    if (this.connectionState === "open" || this.connectionState === "connecting") {
      return;
    }

    // Gated mode: legacy ``?token=`` is rejected by ``_ws_auth_ok``; the SPA
    // must fetch a single-use ticket. Explicit ``token`` keeps the test-only
    // override path.
    const authParam = token ? (["token", token] as const) : await buildWsAuthParam();
    if (!authParam[1]) {
      throw new Error(
        "Session token not available — page must be served by the Qubit dashboard server",
      );
    }

    await super.connect(
      buildQubitWebSocketUrl({
        authParam,
        basePath: QUBIT_BASE_PATH,
        path: withApiPrefix("/api/ws"),
      }),
    );
  }

  /**
   * Sample round-trip latency to the gateway, in milliseconds.
   *
   * Times a `gateway.ping` JSON-RPC round trip — the same method the heartbeat
   * sends — so the reading measures the live socket, not a fresh HTTP
   * handshake. Rejects when the socket is not open or the ping times out.
   */
  async sampleLatency(timeoutMs = 5_000): Promise<number> {
    const start = performance.now();
    await this.request("gateway.ping", {}, timeoutMs);
    return Math.max(0, performance.now() - start);
  }
}
