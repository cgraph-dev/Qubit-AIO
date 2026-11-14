/* The owned floor campus: the Habblaud office multiplied by floor.
 *
 * WHY THIS EXISTS
 * ---------------
 * The owner asked for the Habblaud office repeated and placed next to each
 * other as floors — not a directory and not a second vector renderer. Each card
 * below is a REAL Habblaud app instance scoped to one live floor, so the pixel
 * art, furniture and character sprites are the same assets, and each floor's
 * characters are the ones the tower seats there. No floor is invented and no
 * agent is moved.
 *
 * The app's own topbar/sidebar/feed are hidden inside each frame so the card
 * shows the office scene, not five copies of the same chrome. The Qubit shell
 * keeps the labels and the close control.
 */
import type { OfficeGameShellProps } from "../OfficeGameShell";
import { OfficeAdapter } from "../adapter";
import { officeAppSrc } from "./office-app";
import { installOfficeApiBridge } from "./api-bridge";
import { installOfficeBrand } from "./brand-patch";

export interface FloorCampusProps {
  base?: string;
  /** The active office theme (`limezu`), so every copy uses the same assets. */
  theme?: string;
}

interface FloorRow {
  number: number;
  name: string;
  role: string;
  rooms: number;
  agents: number;
}

function embedCss(win: Window): void {
  const doc = win?.document;
  if (!doc || !doc.head) return;
  if (doc.getElementById("qubit-campus-embed")) return;
  const style = doc.createElement("style");
  style.id = "qubit-campus-embed";
  style.textContent = [
    "html,body{margin:0!important;overflow:hidden!important;background:#0b0a13!important}",
    ".ui-topbar,.ui-panel.ui-sidebar,.ui-panel.ui-feed,.ui-lapse-vignette{display:none!important}",
    ".ui-root{padding:0!important}",
  ].join("");
  doc.head.appendChild(style);
}

export function createFloorCampus(
  R: OfficeGameShellProps["React"],
  H: OfficeGameShellProps["hooks"],
) {
  return function FloorCampus(props: FloorCampusProps) {
    const { useState, useEffect } = H;
    const [floors, setFloors] = useState<FloorRow[]>([]);
    const [error, setError] = useState("");

    useEffect(() => {
      let live = true;
      const baseUrl = props.base ? `${props.base}/agent` : "/agent";
      const adapter = new OfficeAdapter({
        baseUrl,
        intervalMs: 5000,
        onSnapshot: (snapshot) => {
          if (!live) return;
          if (!snapshot.payload) {
            if (snapshot.error) setError(snapshot.error);
            return;
          }
          const chars = snapshot.payload.characters ?? [];
          const rows = (snapshot.payload.floorplan?.floors ?? []).map((floor) => ({
            number: floor.number,
            name: floor.name,
            role: floor.role || "",
            rooms: floor.rooms?.length ?? 0,
            agents: chars.filter(
              (ch) => Number(ch.position?.floor) === Number(floor.number),
            ).length,
          }));
          setFloors(rows);
          setError("");
        },
      });
      adapter.start();
      return () => {
        live = false;
        adapter.dispose();
      };
    }, [props.base]);

    const cards = floors.map((floor) => R.createElement("article", {
      key: floor.number,
      className: "office-game__floorCard",
      "data-testid": `office-floor-card-${floor.number}`,
    }, [
      R.createElement("header", {
        key: "head", className: "office-game__floorCardHead",
      }, [
        R.createElement("strong", { key: "n" }, `Floor ${floor.number}`),
        R.createElement("span", { key: "r" }, floor.role || floor.name),
        R.createElement("span", { key: "c" },
          `${floor.agents} agent${floor.agents === 1 ? "" : "s"} · `
          + `${floor.rooms} room${floor.rooms === 1 ? "" : "s"}`),
      ]),
      R.createElement("iframe", {
        key: "frame",
        title: `Floor ${floor.number} · ${floor.role || floor.name}`,
        className: "office-game__floorFrame",
        "data-testid": `office-floor-frame-${floor.number}`,
        sandbox: "allow-scripts allow-same-origin allow-forms allow-downloads allow-popups allow-modals",
        allow: "clipboard-read; clipboard-write",
        src: officeAppSrc({
          base: props.base,
          theme: props.theme,
          floor: `live:${floor.number}`,
        }),
        onLoad: (ev: { currentTarget: HTMLIFrameElement }) => {
          const win = ev.currentTarget.contentWindow as Window | null;
          if (!win) return;
          try {
            installOfficeApiBridge(win);
            installOfficeBrand(win);
            embedCss(win);
          } catch {
            // A single failed frame must not take out the other floors.
          }
        },
      }),
    ]));

    return R.createElement("div", {
      className: "office-game__floorCampus",
      "data-testid": "office-floor-campus",
    }, [
      R.createElement("p", {
        key: "note", className: "office-game__floorCampusNote",
      }, "Each card is the Habblaud office rendered for one real floor, "
        + "using the same LimeZu art and that floor's characters."),
      error
        ? R.createElement("p", {
            key: "error", className: "office-game__campusState", role: "alert",
          }, `Floor campus unavailable: ${error}`)
        : null,
      !floors.length && !error
        ? R.createElement("p", {
            key: "loading", className: "office-game__campusState", role: "status",
          }, "Loading the live floors…")
        : null,
      R.createElement("div", {
        key: "grid", className: "office-game__floorGrid",
      }, cards),
    ]);
  };
}
