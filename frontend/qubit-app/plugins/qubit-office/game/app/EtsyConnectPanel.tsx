/* Office Etsy connection: status, PKCE authorization and callback completion.
 *
 * WHAT IT IS
 * ----------
 * A Qubit-shell dock (never the vendored Habblaud document) over the real
 * Business OS routes. It shows the backend's typed Etsy status, what is
 * missing, a ``Connect Etsy`` button that opens the authorization URL, and a
 * completion form for the redirected code/state.
 *
 * WHAT IT WILL NOT DO
 * -------------------
 * It never labels the account "connected" unless the server reports both
 * ``state: "connected"`` and ``connected: true``. It does not list, publish,
 * price, refund, message or spend: the Etsy adapter's approval gates remain
 * the only path to those operations. Tokens stay on the server; nothing here
 * receives or displays one.
 *
 * React comes from the HOST: ``R.createElement``, never an import.
 */
import { officeFx, sakugaButton, sakugaProgress } from "./sakuga";
import { OfficeInput, OfficeTooltip } from "./sakuga-form";
import type { OfficeGameShellProps } from "../OfficeGameShell";
import {
  EtsyApiError,
  type EtsyAuthorizationStart,
  type EtsyReadState,
  type EtsyStatus,
  completeEtsyAuthorization,
  disconnectEtsy,
  fetchEtsyStatus,
  isConnected,
  missingText,
  parseAuthorizationInput,
  parseStateInput,
  startEtsyAuthorization,
  statusLabel,
} from "./etsy-connection";

export interface EtsyConnectPanelProps {
  base?: string;
  fetchImpl?: typeof fetch;
  onClose?: () => void;
  /** Injected opener so tests never touch a real window. */
  openUrl?: (url: string) => void;
}

type Failure = { message: string; code?: string };

function failureOf(exc: unknown): Failure {
  if (exc instanceof EtsyApiError) {
    return { message: exc.message, code: exc.code };
  }
  return { message: exc instanceof Error ? exc.message : String(exc) };
}

function pillClass(status: EtsyStatus): string {
  const state = String(status.state || "error");
  return `etsy-connect__pill etsy-connect__pill--${state}`;
}

export function createEtsyConnectPanel(
  R: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
) {
  const { useState, useEffect } = hooks;

  return function EtsyConnectPanel(props: EtsyConnectPanelProps) {
    const [read, setRead] = useState<EtsyReadState>({ kind: "loading" });
    const [busy, setBusy] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [failure, setFailure] = useState<Failure | null>(null);
    const [refresh, setRefresh] = useState(0);
    const [started, setStarted] = useState<EtsyAuthorizationStart | null>(null);
    const [codeInput, setCodeInput] = useState("");
    const [stateInput, setStateInput] = useState("");

    useEffect(() => {
      let live = true;
      const ctl = typeof AbortController === "undefined" ? undefined
        : new AbortController();
      fetchEtsyStatus({
        base: props.base,
        fetchImpl: props.fetchImpl,
        signal: ctl?.signal,
      }).then((next) => {
        if (live) setRead(next);
      });
      return () => {
        live = false;
        ctl?.abort();
      };
    }, [props.base, props.fetchImpl, refresh]);

    const applyStatus = (status: EtsyStatus) => {
      setRead({ kind: "ready", status });
      if (status.state !== "connected" || !status.connected) {
        setNotice(status.detail || `Etsy status: ${statusLabel(status)}.`);
      }
    };

    const openAuthorization = (url: string) => {
      if (props.openUrl) {
        props.openUrl(url);
        return;
      }
      if (typeof window !== "undefined" && typeof window.open === "function") {
        window.open(url, "_blank", "noopener,noreferrer");
      }
    };

    const onConnect = () => {
      if (busy) return;
      setBusy("authorize");
      setFailure(null);
      setNotice(null);
      startEtsyAuthorization({
        base: props.base,
        fetchImpl: props.fetchImpl,
      }).then((next) => {
        officeFx("impact");
        setStarted(next);
        setStateInput(next.state);
        openAuthorization(next.url);
        setNotice(
          "Etsy authorization opened. Approve the app, then paste the "
          + "redirect URL (or its code) below.",
        );
      }).catch((exc) => {
        officeFx("error");
        setFailure(failureOf(exc));
      }).finally(() => {
        setBusy(null);
      });
    };

    const onComplete = () => {
      if (busy) return;
      setBusy("complete");
      setFailure(null);
      setNotice(null);
      let code = parseAuthorizationInput(codeInput);
      let state = stateInput.trim() || parseStateInput(codeInput);
      if (!code) {
        setBusy(null);
        setFailure({
          message: "Paste the redirect URL or the authorization code Etsy "
            + "returned.",
          code: "ETSY_CODE_REQUIRED",
        });
        return;
      }
      if (!state) {
        setBusy(null);
        setFailure({
          message: "The OAuth state is required; click Connect Etsy first "
            + "or paste the complete redirect URL.",
          code: "ETSY_STATE_REQUIRED",
        });
        return;
      }
      completeEtsyAuthorization(code, state, {
        base: props.base,
        fetchImpl: props.fetchImpl,
      }).then((status) => {
        applyStatus(status);
        if (isConnected(status)) {
          officeFx("success");
          setNotice("Etsy is connected.");
          setCodeInput("");
          setStarted(null);
        }
      }).catch((exc) => {
        officeFx("error");
        setFailure(failureOf(exc));
      }).finally(() => {
        setBusy(null);
      });
    };

    const onDisconnect = () => {
      if (busy) return;
      setBusy("disconnect");
      setFailure(null);
      setNotice(null);
      disconnectEtsy({
        base: props.base,
        fetchImpl: props.fetchImpl,
      }).then((status) => {
        officeFx("warning");
        applyStatus(status);
        setStarted(null);
        setCodeInput("");
        setStateInput("");
      }).catch((exc) => {
        officeFx("error");
        setFailure(failureOf(exc));
      }).finally(() => {
        setBusy(null);
      });
    };

    const children: unknown[] = [
      R.createElement("div", {
        key: "head", className: "office-game__dockHead office-theme-dock-head",
      }, [
        R.createElement("span", {
          key: "t", className: "office-theme-dock-title",
        }, "Etsy connection"),
        R.createElement(OfficeTooltip, {
          key: "refresh-tip",
          content: "Re-read the token and scope state from the local integration store.",
        }, sakugaButton(R, {
          key: "refresh", type: "button",
          className: "office-game__btn office-theme-dock-link",
          "data-testid": "etsy-connect-refresh",
          "aria-label": "Refresh the Etsy connection status",
          disabled: busy !== null || read.kind === "loading",
          onClick: () => setRefresh((n: number) => n + 1),
        }, "Refresh")),
        sakugaButton(R, {
          key: "x", type: "button",
          className: "office-game__btn office-theme-dock-close",
          "data-testid": "etsy-connect-close",
          "aria-label": "Close Etsy connection",
          onClick: () => props.onClose?.(),
        }, "Close"),
      ]),
    ];

    if (read.kind === "loading") {
      children.push(R.createElement("p", {
        key: "loading", className: "etsy-connect__state",
        "data-testid": "etsy-connect-loading", role: "status",
      }, [
        "Reading the Etsy connection status…",
        sakugaProgress(R, { label: "Reading the Etsy connection status", value: 0.08 }),
      ]));
    } else if (read.kind === "error") {
      children.push(R.createElement("div", {
        key: "read-error", className: "etsy-connect__error",
        "data-testid": "etsy-connect-error", role: "alert",
        "data-code": read.code || undefined,
      }, read.message));
    } else {
      const status = read.status;
      children.push(R.createElement("div", {
        key: "status", className: "etsy-connect__summary",
        "data-testid": "etsy-connect-status",
        "data-state": String(status.state || "error"),
        "data-connected": isConnected(status) ? "true" : "false",
      }, [
        R.createElement("span", {
          key: "label", className: "etsy-connect__label",
        }, "Status "),
        R.createElement("span", {
          key: "pill", className: pillClass(status),
          "data-testid": "etsy-connect-pill",
        }, statusLabel(status)),
      ]));
      const missing = missingText(status);
      if (missing) {
        children.push(R.createElement("p", {
          key: "missing", className: "etsy-connect__hint",
          "data-testid": "etsy-connect-missing",
        }, missing));
      }
      if (status.scopes.length) {
        children.push(R.createElement("p", {
          key: "scopes", className: "etsy-connect__meta",
          "data-testid": "etsy-connect-scopes",
        }, `Requested reads: ${status.scopes.join(", ")}`));
      }
      if (isConnected(status)) {
        const expiry = status.expires_at
          ? new Date(status.expires_at * 1000).toLocaleString()
          : "unknown";
        children.push(R.createElement("div", {
          key: "connected", className: "etsy-connect__connected",
          "data-testid": "etsy-connect-connected",
        }, [
          R.createElement("p", {
            key: "exp", className: "etsy-connect__meta",
          }, `Access expires ${expiry}.`),
          sakugaButton(R, {
            key: "disconnect", type: "button",
            className: "office-game__btn etsy-connect__secondary",
            "data-testid": "etsy-connect-disconnect",
            disabled: busy !== null,
            onClick: onDisconnect,
          }, busy === "disconnect" ? "Disconnecting…" : "Disconnect"),
        ]));
      } else if (status.configured) {
        children.push(R.createElement("div", {
          key: "connect-row", className: "etsy-connect__actions",
        }, [
          sakugaButton(R, {
            key: "connect", type: "button",
            className: "office-game__btn etsy-connect__primary",
            "data-testid": "etsy-connect-start",
            disabled: busy !== null,
            onClick: onConnect,
          }, busy === "authorize" ? "Opening Etsy…" : "Connect Etsy"),
        ]));
        children.push(R.createElement("form", {
          key: "complete", className: "etsy-connect__form",
          "data-testid": "etsy-connect-form",
          onSubmit: (event: { preventDefault?: () => void }) => {
            event?.preventDefault?.();
            onComplete();
          },
        }, [
          R.createElement("label", {
            key: "code-label", className: "etsy-connect__field",
          }, [
            R.createElement("span", { key: "l" }, "Redirect URL or code"),
            R.createElement(OfficeInput, {
              key: "code", label: "", className: "warroom__input",
              "data-testid": "etsy-connect-code",
              autoComplete: "off",
              value: codeInput,
              placeholder: "Paste what Etsy redirected you to",
              onChange: (event: { target: { value: string } }) => {
                const value = event.target.value;
                setCodeInput(value);
                const parsedState = parseStateInput(value);
                if (parsedState) setStateInput(parsedState);
              },
            }),
          ]),
          R.createElement("label", {
            key: "state-label", className: "etsy-connect__field",
          }, [
            R.createElement("span", { key: "l" }, "OAuth state"),
            R.createElement(OfficeInput, {
              key: "state", label: "", className: "warroom__input",
              "data-testid": "etsy-connect-state",
              autoComplete: "off",
              value: stateInput,
              placeholder: "Filled by Connect Etsy",
              onChange: (event: { target: { value: string } }) => {
                setStateInput(event.target.value);
              },
            }),
          ]),
          sakugaButton(R, {
            key: "complete", type: "button",
            className: "office-game__btn etsy-connect__primary",
            "data-testid": "etsy-connect-complete",
            disabled: busy !== null,
            onClick: onComplete,
          }, busy === "complete" ? "Completing…" : "Complete connection"),
        ]));
      }
      if (started) {
        children.push(R.createElement("p", {
          key: "manual-link", className: "etsy-connect__meta",
          "data-testid": "etsy-connect-manual-link",
        }, [
          R.createElement("span", { key: "t" }, "Popup blocked? "),
          R.createElement("a", {
            key: "a", href: started.url,
            target: "_blank", rel: "noopener noreferrer",
          }, "Open the Etsy authorization page"),
        ]));
      }
    }

    if (notice) {
      children.push(R.createElement("p", {
        key: "notice", className: "etsy-connect__notice",
        "data-testid": "etsy-connect-notice", role: "status",
      }, notice));
    }
    if (failure) {
      children.push(R.createElement("div", {
        key: "failure", className: "etsy-connect__error",
        "data-testid": "etsy-connect-action-error", role: "alert",
        "data-code": failure.code || undefined,
      }, failure.message));
    }

    return R.createElement("div", {
      className: "etsy-connect",
      "data-testid": "etsy-connect-panel",
      "data-state": read.kind === "ready" ? String(read.status.state) : read.kind,
    }, children);
  };
}
