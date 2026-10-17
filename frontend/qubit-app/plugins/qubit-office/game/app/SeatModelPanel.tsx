/* The per-seat model/provider panel, mounted beside the Office iframe.
 *
 * React comes from the HOST (see OfficeAppShell.tsx): `R.createElement`, no
 * `import from "react"` — a second React copy would silently split the hook
 * runtime.
 *
 * The panel is honest about its states: loading, unavailable, and saved all
 * render differently, and a failed write shows the server's own reason rather
 * than a green tick. It never claims a save it did not make.
 */
import type { OfficeGameShellProps } from "../OfficeGameShell";
import {
  type SeatModelState,
  describeSeatRouting,
  fetchSeatModel,
  modelsFor,
  saveSeatModel,
  selectableProviders,
} from "./seat-model-panel";

export interface SeatModelPanelProps {
  /** The office seat to configure. */
  agentId: string | number;
  /** Label for the seat (its slug or name), shown as the panel heading. */
  seatLabel?: string;
}

export function createSeatModelPanel(
  R: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
) {
  const { useState, useEffect } = hooks;

  return function SeatModelPanel(props: SeatModelPanelProps) {
    const [state, setState] = useState<SeatModelState | null>(null);
    const [provider, setProvider] = useState("");
    const [model, setModel] = useState("");
    const [saving, setSaving] = useState(false);
    const [notice, setNotice] = useState("");

    useEffect(() => {
      let live = true;
      fetchSeatModel(props.agentId, window.fetch.bind(window), window)
        .then((next) => {
          if (!live) return;
          setState(next);
          setProvider(next.provider ?? "");
          setModel(next.model ?? "");
        })
        .catch(() => {
          if (live) setState({ ok: false, detail: "The office API did not answer." });
        });
      return () => { live = false; };
    }, [props.agentId]);

    const providers = selectableProviders(state);
    const models = modelsFor(state, provider);

    const onSave = () => {
      setSaving(true);
      setNotice("");
      saveSeatModel(props.agentId, provider, model, window.fetch.bind(window), window)
        .then((next) => {
          setState((prev: SeatModelState | null): SeatModelState => ({ ...(prev ?? {}), ...next }));
          if (next.ok === false) {
            setNotice(next.detail || "The change was not saved.");
          } else {
            setNotice(next.inherits ? "Cleared — this seat inherits the profile default." : "Saved.");
          }
          setSaving(false);
        })
        .catch(() => {
          setNotice("The office API did not answer; nothing was changed.");
          setSaving(false);
        });
    };

    const children: unknown[] = [
      R.createElement("div", { key: "head", className: "seat-model__head" },
        props.seatLabel ? `${props.seatLabel} — model` : "Seat model"),
      R.createElement("div", {
        key: "current",
        className: "seat-model__current",
        "data-testid": "seat-model-current",
      }, describeSeatRouting(state)),
    ];

    if (!state) {
      children.push(R.createElement("div", {
        key: "loading", className: "seat-model__loading", role: "status",
      }, "Loading model assignment\u2026"));
    } else if (!state.ok) {
      children.push(R.createElement("div", {
        key: "error", className: "seat-model__error", role: "alert",
        "data-testid": "seat-model-error",
      }, state.detail || "Model assignment unavailable"));
    } else {
      children.push(R.createElement("label", { key: "prov-label", className: "seat-model__label" },
        "Provider",
        R.createElement("select", {
          key: "prov",
          "data-testid": "seat-model-provider",
          value: provider,
          disabled: saving,
          onChange: (e: { target: { value: string } }) => {
            setProvider(e.target.value);
            setModel("");
          },
        }, [
          R.createElement("option", { key: "inherit", value: "" },
            "Inherit profile default"),
          ...providers.map((p) =>
            R.createElement("option", { key: p.slug, value: p.slug },
              p.name || p.slug)),
        ]),
      ));

      children.push(R.createElement("label", { key: "model-label", className: "seat-model__label" },
        "Model",
        R.createElement("select", {
          key: "model",
          "data-testid": "seat-model-model",
          value: model,
          disabled: saving || !provider,
          onChange: (e: { target: { value: string } }) => setModel(e.target.value),
        }, [
          R.createElement("option", { key: "none", value: "" },
            provider ? "— pick a model —" : "— pick a provider first —"),
          ...models.map((m) =>
            R.createElement("option", { key: m, value: m }, m)),
        ]),
      ));

      if (state.providers_note) {
        children.push(R.createElement("div", {
          key: "note", className: "seat-model__note",
        }, state.providers_note));
      }

      children.push(R.createElement("button", {
        key: "save",
        type: "button",
        className: "seat-model__save",
        "data-testid": "seat-model-save",
        disabled: saving,
        onClick: onSave,
      }, saving ? "Saving\u2026" : "Save"));
    }

    if (notice) {
      children.push(R.createElement("div", {
        key: "notice",
        className: "seat-model__notice",
        role: "status",
        "data-testid": "seat-model-notice",
      }, notice));
    }

    return R.createElement("div", {
      "data-testid": "seat-model-panel",
      className: "seat-model",
    }, children);
  };
}
