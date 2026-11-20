import "@/styles/qubit-manga-header-art.css";

/** Final Manga page-header ornaments: one large gold star at the left and the
 * sharp magenta impact slashes at the right. CSS hides them outside Manga. */
export function QubitMangaHeaderArt() {
  return (
    <div className="qubit-manga-header-art" aria-hidden="true">
      <svg className="qubit-manga-header-art__star" viewBox="0 0 72 72" focusable="false">
        <defs>
          <linearGradient id="qmh-gold" x1=".12" y1=".02" x2=".86" y2=".96">
            <stop stopColor="#ffffee" />
            <stop offset=".2" stopColor="#fffdbb" />
            <stop offset=".46" stopColor="#ffe948" />
            <stop offset=".72" stopColor="#ffb500" />
            <stop offset="1" stopColor="#854315" />
          </linearGradient>
          <linearGradient id="qmh-pink" x1="0" y1="0" x2="1" y2="1">
            <stop stopColor="#fff4ff" />
            <stop offset=".19" stopColor="#ff67cb" />
            <stop offset=".65" stopColor="#fe198c" />
            <stop offset="1" stopColor="#8b124d" />
          </linearGradient>
        </defs>
        <path d="M3 68 3 18 18 3 68 3 53 11 29 11 11 29 11 53Z" fill="#0a0615" stroke="#0a0615" strokeWidth="5" />
        <path d="M3 68 3 18 18 3 68 3 53 11 29 11 11 29 11 53Z" fill="url(#qmh-pink)" stroke="#fff1ff" strokeWidth="2.5" />
        <path d="M0 46 0 18 18 0 46 0 33 7 23 7 7 23 7 33Z" fill="url(#qmh-gold)" stroke="#120619" strokeWidth="2" />
        <path d="M0 38 0 18 18 0 38 0 21 4 4 21Z" fill="#fff4ce" opacity=".93" />
        <path d="M5 32 5 20 20 5 31 5" stroke="#ffffff" strokeWidth="2.4" fill="none" />
        <path d="m14 16 7-1-8 15 2-10-7 2z" fill="#120619" />
        <path d="M53 8 65 4 56 14" fill="#fff5ff" />
      </svg>

      <svg className="qubit-manga-header-art__slashes" viewBox="0 0 420 270" focusable="false">
        <defs>
          <linearGradient id="qmh-slash-pink" x1="0" y1="0" x2="1" y2="1">
            <stop stopColor="#fff2ff" />
            <stop offset=".11" stopColor="#ff87db" />
            <stop offset=".28" stopColor="#ff36a9" />
            <stop offset=".67" stopColor="#e41983" />
            <stop offset=".88" stopColor="#821647" />
            <stop offset="1" stopColor="#ffb8e5" />
          </linearGradient>
        </defs>
        <g fill="#090510" stroke="#090510" strokeWidth="10" strokeLinejoin="miter">
          <path d="M15 216 L217 35 L409 7 L233 68 L181 103 L209 84 L397 42 L135 232 L183 160 L78 218Z" />
          <path d="M39 268 L207 114 L390 63 L232 144 L200 163 L255 128 L360 111 L106 259 L137 211 Z" />
          <path d="M170 251 L294 137 L416 108 L316 163 L280 182 L342 164 L408 152 L242 258Z" />
        </g>
        <g fill="url(#qmh-slash-pink)" stroke="#fe5abf" strokeWidth="2">
          <path d="M15 216 L217 35 L409 7 L233 68 L181 103 L209 84 L397 42 L135 232 L183 160 L78 218Z" />
          <path d="M39 268 L207 114 L390 63 L232 144 L200 163 L255 128 L360 111 L106 259 L137 211 Z" />
          <path d="M170 251 L294 137 L416 108 L316 163 L280 182 L342 164 L408 152 L242 258Z" />
        </g>
        <g fill="#fff2fb" opacity=".94">
          <path d="M23 208 L218 41 L377 17 L228 65Z" />
          <path d="M48 257 L209 121 L354 79 L216 148Z" />
          <path d="M180 244 L297 145 L391 117 L303 168Z" />
        </g>
      </svg>
    </div>
  );
}
