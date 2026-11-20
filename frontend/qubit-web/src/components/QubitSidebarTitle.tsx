import "@/styles/qubit-sidebar-title.css";

/**
 * Manga sidebar title panel supplied by the owner. The artwork stretches to
 * the open rail width; the label is real text. Collapsed mode keeps the
 * existing compact rail instead of squeezing this panel.
 */
import { useId } from "react";

export function QubitSidebarTitle({ label = "QUBIT AIO", className }: { label?: string; className?: string }) {
  const uid = useId();
  return (
    <div className={["qubit-sidebar-title", className].filter(Boolean).join(" ")}>
      <svg
        className="qubit-sidebar-title__art"
        viewBox="0 0 320 64"
        preserveAspectRatio="none"
        aria-hidden="true"
        focusable="false"
      >
        <defs>
          <linearGradient id={`${uid}-rim`} x2="0" y2="1">
            <stop stopColor="#ffb3ed" />
            <stop offset=".12" stopColor="#ff25a7" />
            <stop offset=".34" stopColor="#55002f" />
            <stop offset=".78" stopColor="#a20059" />
            <stop offset=".92" stopColor="#ff49b7" />
            <stop offset="1" stopColor="#570032" />
          </linearGradient>
          <linearGradient id={`${uid}-face`} x2="0" y2="1">
            <stop stopColor="#ff229f" />
            <stop offset=".45" stopColor="#a90050" />
            <stop offset="1" stopColor="#500027" />
          </linearGradient>
          <linearGradient id={`${uid}-gold`} x2="0" y2="1">
            <stop stopColor="#fff3a0" />
            <stop offset=".43" stopColor="#ffcf27" />
            <stop offset="1" stopColor="#e87500" />
          </linearGradient>
          <radialGradient id={`${uid}-glint`}>
            <stop stopColor="#fff" />
            <stop offset=".28" stopColor="#ffd2f3" stopOpacity=".92" />
            <stop offset="1" stopColor="#ff3eae" stopOpacity="0" />
          </radialGradient>
          <clipPath id={`${uid}-face-clip`}>
            <path d="M13 13H237L202 51H13Z" />
          </clipPath>
        </defs>

        <path d="M3 4H316L272 57H229L225 61H3Z" fill={`url(#${uid}-rim)`} stroke="#400025" strokeWidth="2" />
        <path d="M8 9H304L269 53H227L223 56H8Z" fill="#240015" />
        <path d="M5 59V6H312" fill="none" stroke="#ff9bdc" strokeWidth="1.3" />

        <path d="M13 13H237L202 51H13Z" fill={`url(#${uid}-face)`} />
        <g clipPath={`url(#${uid}-face-clip)`} fill="#ff62bd">
          <path d="M67 12H105L65 52H27Z" opacity=".31" />
          <path d="M163 12H188L149 52H124Z" opacity=".24" />
        </g>
        <path d="M13 51V13H237" fill="none" stroke="#ffd5f2" strokeWidth="1.6" />
        <path d="M13 51H202L237 13" fill="none" stroke="#ff58bd" strokeWidth="2" />

        <g fill={`url(#${uid}-gold)`} stroke="#963300" strokeWidth="2">
          <path d="M242 14H260L223 55H205Z" />
          <path d="M264 14H282L248 51H230Z" />
          <path d="M286 14H304L270 51H252Z" />
        </g>
        <path d="M208 53L243 16H257 M233 49L265 16H279 M255 49L287 16H301" fill="none" stroke="#fff7b2" strokeWidth="1.5" />

        <g fill={`url(#${uid}-glint)`}>
          <ellipse cx="103" cy="7" rx="36" ry="3.5" />
          <ellipse cx="261" cy="7" rx="26" ry="3" />
          <ellipse cx="61" cy="57" rx="29" ry="3.5" />
          <ellipse cx="159" cy="57" rx="35" ry="4" />
        </g>
      </svg>

      <span className="qubit-sidebar-title__label">{label}</span>
    </div>
  );
}
