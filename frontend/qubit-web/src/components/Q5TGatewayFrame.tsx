import type { ReactNode } from "react";
import "./q5t-gateway.css";

/**
 * Manga-only gateway panel. The artwork is the generated 600x206 plate in
 * `/manga-ui/gateway-panel.svg`; the three rows are real values supplied by the
 * semantic child (`NavLink` -> `/sessions`), never baked into the art.
 */
export function Q5TGatewayFrame({ children }: { children?: ReactNode }) {
  return (
    <div className="q5t-gateway">
      {children != null && <div className="q5t-gateway__content">{children}</div>}
    </div>
  );
}
