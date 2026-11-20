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


    </div>
  );
}
