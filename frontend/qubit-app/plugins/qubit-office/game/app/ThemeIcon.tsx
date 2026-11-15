/* A licensed toolbar/panel icon from the active theme, or nothing.
 *
 * The icon is decorative: every caller also renders a text label / aria-label,
 * so an absent, blocked (licenseOk=false) or unlisted icon simply disappears
 * and the text fallback remains. No empty image ever renders.
 */
import type { OfficeGameShellProps } from "../OfficeGameShell";
import { officeUiIcon, type OfficeTheme } from "./office-theme";

export interface ThemeIconProps {
  theme?: OfficeTheme | null;
  name: string;
  className?: string;
}

export function createThemeIcon(R: OfficeGameShellProps["React"]) {
  return function ThemeIcon(props: ThemeIconProps) {
    const icon = officeUiIcon(props.theme, props.name);
    if (!icon) return null;
    return R.createElement("img", {
      className: "office-theme-icon"
        + (props.className ? ` ${props.className}` : ""),
      src: icon.src,
      width: icon.width,
      height: icon.height,
      alt: "",
      "aria-hidden": "true",
      "data-office-ui-icon": props.name,
      loading: "lazy",
      decoding: "async",
    });
  };
}
