import { useId, type ReactNode } from "react";
import "./q5t-tooltip.css";

export function Q5TTooltipPlate({ title, children, tooltipId }: { title?: ReactNode; children?: ReactNode; tooltipId?: string }) {
  const id = useId();
  return <div className="q5t-tooltip" id={tooltipId} role={tooltipId ? "tooltip" : undefined}>
<svg viewBox="0 0 300 155" aria-hidden="true"><defs>
<linearGradient id={`${id}-metal`} x2="0" y2="1"><stop stopColor="#fff5ff"/><stop offset=".12" stopColor="#a78db7"/><stop offset=".29" stopColor="#fff0ff"/><stop offset=".57" stopColor="#7e225b"/><stop offset=".82" stopColor="#ff52ae"/><stop offset="1" stopColor="#ffe8fa"/></linearGradient>
<linearGradient id={`${id}-face`} x2="0" y2="1"><stop stopColor="#130911"/><stop offset=".62" stopColor="#210d22"/><stop offset="1" stopColor="#460b34"/></linearGradient>
<linearGradient id={`${id}-gold`} x2="0" y2="1"><stop stopColor="#fff8a8"/><stop offset=".44" stopColor="#ffd62b"/><stop offset="1" stopColor="#c86500"/></linearGradient>
<radialGradient id={`${id}-flare`}><stop stopColor="white"/><stop offset=".25" stopColor="#ffdcf2"/><stop offset=".53" stopColor="#ff49a7" stopOpacity=".7"/><stop offset="1" stopColor="#ff1090" stopOpacity="0"/></radialGradient>
<radialGradient id={`${id}-badge`}><stop stopColor="#43092b"/><stop offset=".69" stopColor="#52062f"/><stop offset="1" stopColor="#b01265"/></radialGradient>
<filter id={`${id}-glow`} x="-30%" y="-35%" width="160%" height="175%"><feDropShadow dx="0" dy="0" stdDeviation="4" floodColor="#ff198d" floodOpacity=".65"/></filter>
</defs>
<path d="M25 32H270L284 46V115L270 129H166L150 148L134 129H25L10 115V46Z" fill={`url(#${id}-metal)`} stroke="#4d1737" strokeWidth="4" filter={`url(#${id}-glow)`}/>
<path d="M26 35H269L281 47V114L269 126H164L150 143L136 126H26L13 114V47Z" fill="#541431" stroke="#fff0fa" strokeWidth="1.5"/>
<path d="M28 40H267L276 49V112L267 122H162L150 136L138 122H28L18 112V49Z" fill={`url(#${id}-face)`} stroke="#ff86c8" strokeWidth="1.7"/>
<path d="M20 68V50L30 41H49Z M248 121H266L275 112V95Z" fill={`url(#${id}-gold)`} stroke="#5d2d03" strokeWidth="2"/>
<path d="M22 62V51L31 43H44 M254 119H265L273 111V100" fill="none" stroke="#fff7b0" strokeWidth="1.3"/>
<g fill={`url(#${id}-flare)`}><ellipse cx="50" cy="35" rx="26" ry="5"/><ellipse cx="202" cy="35" rx="29" ry="5"/><ellipse cx="61" cy="124" rx="33" ry="5"/><ellipse cx="226" cy="124" rx="34" ry="5"/><ellipse cx="279" cy="79" rx="4" ry="25"/></g>
<g filter={`url(#${id}-glow)`}><circle cx="255" cy="40" r="26" fill="#700c47" stroke="#ffb6da" strokeWidth="2"/><circle cx="255" cy="40" r="23.5" fill={`url(#${id}-metal)`} stroke="#fff5fb" strokeWidth="1.3"/></g>
<circle cx="255" cy="40" r="19.5" fill={`url(#${id}-badge)`} stroke="#290517" strokeWidth="2"/><circle cx="255" cy="40" r="17" fill="none" stroke="#ff75ba" strokeWidth=".8"/><path d="M255 36h4l-4 13h4l-.5 2h-8l.4-2h1.8l3.2-11h-3z" fill="#fff4f8"/><circle cx="259" cy="31" r="2.6" fill="#fff4f8"/>
</svg>
    {(title || children) && <div className="q5t-tooltip__copy">{title && <strong>{title}</strong>}{children}</div>}
  </div>;
}
