import { useId, type ButtonHTMLAttributes } from "react";
import "./q5t-primary.css";

export function Q5TPrimaryButton({ children = "Restart gateway", className = "", type = "button", ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  const uid = useId();
  return <button {...props} type={type} className={`q5t-primary ${className}`}>
    <svg className="q5t-primary__plate" viewBox="0 0 340 80" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id={`${uid}-edge`} x2="0" y2="1"><stop stopColor="#fff2fb"/><stop offset=".18" stopColor="#fa66b8"/><stop offset=".4" stopColor="#76234e"/><stop offset=".68" stopColor="#440a2c"/><stop offset=".88" stopColor="#ff8ecf"/><stop offset="1" stopColor="#920e51"/></linearGradient><linearGradient id={`${uid}-face`} x2="0" y2="1"><stop stopColor="#50112f"/><stop offset=".34" stopColor="#210c17"/><stop offset=".72" stopColor="#270b21"/><stop offset="1" stopColor="#591138"/></linearGradient><radialGradient id={`${uid}-glint`}><stop stopColor="white"/><stop offset=".28" stopColor="#ffe0f3"/><stop offset=".58" stopColor="#ff4da6" stopOpacity=".78"/><stop offset="1" stopColor="#ff0a88" stopOpacity="0"/></radialGradient></defs><path d="M19 5H321L335 18V62L322 75H18L5 63V18Z" fill={`url(#${uid}-edge)`} stroke="#440523" strokeWidth="3"/><path d="M20 8H320L332 19V61L320 72H20L8 61V19Z" fill="#640d38" stroke="#ffe1f1" strokeWidth="1.4"/><path d="M22 12H318L328 21V59L318 68H22L12 59V21Z" fill={`url(#${uid}-face)`} stroke="#ff55a6" strokeWidth="2"/><path d="M23 15H317L325 23V57L317 65H23L15 57V23Z" fill="none" stroke="#ffb8df" strokeOpacity=".47"/><g fill={`url(#${uid}-glint)`}><ellipse cx="145" cy="9" rx="62" ry="7"/><ellipse cx="143" cy="70" rx="68" ry="6"/><ellipse cx="289" cy="70" rx="35" ry="5"/><ellipse cx="22" cy="14" rx="14" ry="4"/><ellipse cx="329" cy="33" rx="4" ry="20"/></g></svg>
    <svg className="q5t-primary__icon" viewBox="0 0 32 32" aria-hidden="true"><path d="M27 12A11.5 11.5 0 0 0 6 18m-1 2a11.5 11.5 0 0 0 21-6" fill="none" stroke="currentColor" strokeWidth="3.4" strokeLinecap="round"/><path d="M28 4v9h-9zM4 28v-9h9z" fill="currentColor"/></svg>
    <span>{children}</span>
  </button>;
}
