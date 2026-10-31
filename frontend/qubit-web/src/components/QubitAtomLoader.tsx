import "./QubitAtomLoader.css";

type QubitAtomLoaderProps = {
  label?: string;
  className?: string;
  size?: "md" | "sm" | "inline";
  showLabel?: boolean;
  showBar?: boolean;
  showCat?: boolean;
};

const ORBITS = [
  { rotate: 0, rx: 46, ry: 17, dur: "3.4s", electron: "qatom-electron-a" },
  { rotate: 60, rx: 42, ry: 15, dur: "4.1s", electron: "qatom-electron-b" },
  { rotate: 120, rx: 38, ry: 14, dur: "4.9s", electron: "qatom-electron-c" },
] as const;

function ellipsePath(rx: number, ry: number): string {
  return `M ${60 - rx},60 a ${rx},${ry} 0 1,0 ${rx * 2},0 a ${rx},${ry} 0 1,0 ${-rx * 2},0`;
}

export function QubitAtomLoader({
  label = "Loading…",
  className,
  size = "md",
  showLabel = true,
  showBar = true,
  showCat = true,
}: QubitAtomLoaderProps) {
  const showText = showLabel && size !== "inline";
  return (
    <div
      className={`qatom qatom--${size}${className ? ` ${className}` : ""}`}
      role="status"
      aria-live="polite"
      aria-label={label}
    >
      <div className="qatom-stage" aria-hidden="true">
        <svg className="qatom-svg" viewBox="0 0 120 120" fill="none">
          <defs>
            <radialGradient id="qatom-core" cx="38%" cy="30%" r="72%">
              <stop offset="0%" stopColor="#ffffff" />
              <stop offset="26%" stopColor="var(--qatom-cream, #ffe6cb)" />
              <stop offset="62%" stopColor="var(--qatom-teal, #2dd4bf)" />
              <stop offset="100%" stopColor="var(--qatom-deep, #0f766e)" />
            </radialGradient>
            <linearGradient id="qatom-orbit" gradientUnits="userSpaceOnUse" x1="14" y1="60" x2="106" y2="60">
              <stop offset="0%" stopColor="var(--qatom-cyan, #22d3ee)" stopOpacity="0.35" />
              <stop offset="42%" stopColor="var(--qatom-teal, #2dd4bf)" stopOpacity="0.95" />
              <stop offset="72%" stopColor="var(--qatom-cream, #ffe6cb)" stopOpacity="1" />
              <stop offset="100%" stopColor="var(--qatom-violet, #a78bfa)" stopOpacity="0.38" />
            </linearGradient>
            <filter id="qatom-glow" x="-120%" y="-120%" width="340%" height="340%">
              <feGaussianBlur stdDeviation="3.2" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {ORBITS.map((orbit) => (
            <g
              key={orbit.rotate}
              className="qatom-orbit"
              transform={`rotate(${orbit.rotate} 60 60)`}
            >
              <ellipse
                cx="60"
                cy="60"
                rx={orbit.rx}
                ry={orbit.ry}
                stroke="url(#qatom-orbit)"
                strokeWidth="1.5"
              />
              <circle
                className={`qatom-electron ${orbit.electron}`}
                r="2.9"
                fill="var(--qatom-cream, #ffe6cb)"
                filter="url(#qatom-glow)"
              >
                <animateMotion
                  dur={orbit.dur}
                  repeatCount="indefinite"
                  path={ellipsePath(orbit.rx, orbit.ry)}
                />
              </circle>
            </g>
          ))}

          <circle
            className="qatom-core"
            cx="60"
            cy="60"
            r="9.5"
            fill="url(#qatom-core)"
            filter="url(#qatom-glow)"
          />
          <circle
            className="qatom-core-hot"
            cx="57.4"
            cy="56.8"
            r="2.4"
            fill="#ffffff"
            fillOpacity="0.92"
          />
        </svg>

        {showCat && size !== "inline" ? (
        <svg className="qatom-cat" viewBox="0 0 72 52" aria-hidden="true">
          <path
            d="M7 50c0-19 12-31 29-31s29 12 29 31z"
            fill="var(--qatom-cat, #2dd4bf)"
          />
          <path
            d="M14 23 9.5 5.5 27 16.5z"
            fill="var(--qatom-cat, #2dd4bf)"
          />
          <path
            d="M58 23 62.5 5.5 45 16.5z"
            fill="var(--qatom-cat, #2dd4bf)"
          />
          <g className="qatom-cat-eyes">
            <circle cx="27" cy="34" r="2.6" fill="var(--qatom-cream, #ffe6cb)" />
            <circle cx="45" cy="34" r="2.6" fill="var(--qatom-cream, #ffe6cb)" />
          </g>
          <path
            d="M31.5 42.5c2.6 2.7 6.4 2.7 9 0"
            stroke="var(--qatom-cream, #ffe6cb)"
            strokeWidth="1.7"
            strokeLinecap="round"
          />
        </svg>
        ) : null}
      </div>

      {showText ? <div className="qatom-label">{label}</div> : null}
      {showText && showBar ? (
        <div className="qatom-bar" aria-hidden="true">
          <span />
        </div>
      ) : null}
    </div>
  );
}
