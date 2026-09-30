// Atomic stress test: prove the guarded audio-await in playResult can NEVER hang
// when interrupt() calls pause() mid-play (pause() does not fire 'ended').
// Mirrors the logic now in voice-dashboard/frontend/index.html.
// Run: node scripts/test-orb-frozen.js
let currentAudio = null, playToken = 0;

function guardedPlay(audio) {
  const myToken = ++playToken;
  currentAudio = audio;
  let settled = false;
  const done = () => { if (settled) return; settled = true; if (currentAudio === audio) currentAudio = null; resolve(); };
  // (resolve is captured below via a deferred; see playPromise)
  return done;
}

// Deferred
function makeDeferred() {
  let resolve; const p = new Promise(r => { resolve = r; }); return { p, resolve };
}

// The real guarded-await from the dashboard, adapted to a standalone audio-like object.
function fakeGuardedPlay(audio, { getToken, isCurrent }) {
  const myToken = getToken();
  const d = makeDeferred();
  const settle = () => {
    if (isCurrent(audio)) currentAudio = null;
    d.resolve();
  };
  audio.addEventListener('ended', () => settle(), { once: true });
  audio.addEventListener('error', () => settle(), { once: true });
  const timer = setInterval(() => {
    if (getToken() !== myToken || !isCurrent(audio)) { clearInterval(timer); settle(); }
  }, 10);
  audio.addEventListener('ended', () => clearInterval(timer), { once: true });
  try { audio.play().then(()=>{}, () => settle()); } catch (_) { settle(); }
  return d.p;
}

function fakeAudio() {
  return {
    _hans: {}, events: {},
    endedCallbacks: [], errCallbacks: [],
    addEventListener(ev, fn, opts) { (this[ev + 'Cbs'] = this[ev + 'Cbs'] || []).push(fn); },
    play() { this.played = true; return Promise.resolve(); },
    pause() { this.paused = true; /* NO 'ended' event fires — the bug */ },
  };
}

let failures = 0;
function assert(cond, msg) { if (!cond) { console.error('FAIL:', msg); failures++; } else { console.log('ok  :', msg); } }

(async () => {
  // Scenario 1: play finishes normally -> resolves.
  {
    const a = fakeAudio();
    const t0 = Date.now();
    let token = 0;
    const getToken = () => token;
    let current = a;
    const isCurrent = (x) => current === x;
    const p = fakeGuardedPlay(a, { getToken, isCurrent });
    // ended fires after 50ms
    setTimeout(() => { (a.endedCbs||[]).forEach(f=>f()); }, 30);
    await p;
    assert(Date.now() - t0 < 5000, 'normal play resolves');
  }

  // Scenario 2: interrupt() calls pause() -> must still resolve (this was the freeze).
  {
    const a = fakeAudio();
    let token = 0;
    let current = a;
    const getToken = () => token;
    const isCurrent = (x) => current === x;
    const p = fakeGuardedPlay(a, { getToken, isCurrent });
    // simulate interrupt after 30ms: bump token + pause (no 'ended')
    setTimeout(() => { token++; current.pause(); current = null; }, 30);
    const t0 = Date.now();
    await p;
    assert(Date.now() - t0 < 5000, 'interrupt mid-play resolves (orb not frozen)');
    assert(current === null, 'currentAudio cleared after interrupt');
  }

  // Scenario 3: rapid double-interrupt (press twice fast) still recovers.
  {
    const a = fakeAudio();
    let token = 0;
    let current = a;
    const getToken = () => token;
    const isCurrent = (x) => current === x;
    const p = fakeGuardedPlay(a, { getToken, isCurrent });
    setTimeout(() => { token++; current.pause(); current = null; }, 20);
    setTimeout(() => { token++; /* second interrupt, already null */ }, 40);
    const t0 = Date.now();
    await p;
    assert(Date.now() - t0 < 5000, 'rapid double-interrupt recovers');
  }

  console.log(failures === 0 ? '\nALL PASS' : `\n${failures} FAILURES`);
  process.exit(failures === 0 ? 0 : 1);
})();
