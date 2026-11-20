import { useId, type ReactNode } from "react";
import "./q5t-titlebar.css";

export function Q5TSystemTitle({ children = "System", as: Heading = "h2", id }: {
  children?: ReactNode; as?: "h1" | "h2" | "h3" | "h4"; id?: string;
}) {
  const uid = useId();
  return <div className="q5t-titlebar-wrap"><Heading className="q5t-titlebar" id={id}>
<svg className="q5t-titlebar__art" viewBox="0 0 400 76" preserveAspectRatio="none" aria-hidden="true"><defs>
<linearGradient id={`${uid}-face`} x2="1" y2=".8"><stop stopColor="#edc2fd"/><stop offset=".28" stopColor="#f0c5ff"/><stop offset=".61" stopColor="#be84e7"/><stop offset=".84" stopColor="#ce96ee"/><stop offset="1" stopColor="#f3caff"/></linearGradient>
<linearGradient id={`${uid}-edge`} x2="0" y2="1"><stop stopColor="#fff8ff"/><stop offset=".20" stopColor="#efcaff"/><stop offset=".51" stopColor="#a572bf"/><stop offset=".85" stopColor="#66346f"/><stop offset="1" stopColor="#e7b3f5"/></linearGradient>
<radialGradient id={`${uid}-spark`}><stop stopColor="#fff"/><stop offset=".3" stopColor="#ffc7eb"/><stop offset=".58" stopColor="#ff59b6" stopOpacity=".72"/><stop offset="1" stopColor="#ff1994" stopOpacity="0"/></radialGradient>
<clipPath id={`${uid}-inside`}><path d="M34 15H366L386 38 366 61H34L14 38Z"/></clipPath>
</defs>
<path d="M31 9H369L394 38 369 67H31L6 38Z" fill="#2f1737" stroke="#432344" strokeWidth="2"/>
<path d="M32 10H368L392 38 368 65H32L8 38Z" fill={`url(#${uid}-edge)`} stroke="#f4d8fb" strokeWidth="1.3"/>
<path d="M34 15H366L386 38 366 61H34L14 38Z" fill={`url(#${uid}-face)`} stroke="#fff1ff" strokeWidth="1.1"/>
<g clipPath={`url(#${uid}-inside)`}><path d="M97 13H126L81 64H52Z M174 13H210L164 64H128Z M284 13H311L265 64H238Z" fill="#fff" opacity=".16"/><path d="M219 13H250L206 64H171Z M317 13H342L297 64H271Z" fill="#9254c2" opacity=".10"/></g>
<path d="M10 38L32 11H369L391 38 M33 62H367" fill="none" stroke="#fff7ff" strokeWidth="1.5"/>
<path d="M319 22h8l-25 32h-8Z M337 22h8l-25 32h-8Z M355 22h8l-25 32h-8Z" fill="#270b2c" stroke="#f8d7fa" strokeWidth=".6"/>
<g fill={`url(#${uid}-spark)`}><ellipse cx="182" cy="10" rx="35" ry="4"/><ellipse cx="115" cy="65" rx="37" ry="5"/><ellipse cx="219" cy="65" rx="34" ry="4"/><ellipse cx="324" cy="65" rx="32" ry="5"/></g></svg>
    <span className="q5t-titlebar__label">{children}</span>
  </Heading></div>;
}
