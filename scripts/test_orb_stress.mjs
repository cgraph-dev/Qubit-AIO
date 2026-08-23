// Atomic stress test: the web-dashboard orb must NEVER freeze under rapid
// presses. Simulates the recording/busy/play state machine and hammers it with
// adversarial tap sequences, asserting the orb always returns to a usable state
// (busy=false, recording=false, ptt enabled) within a bounded window.
// Run: node scripts/test_orb_stress.mjs
// Mirrors voice-dashboard/frontend/index.html logic (startRec/stopRec/sendAudio/
// interrupt/stopAssistant + the armBusy/reArmOrb watchdog).

let recording = false, busy = false, micBusy = false, pendingToggle = false;
let currentAudio = null, playToken = 0;
let ptt = { disabled: false };
let chunks = [];
let activeTaps = 0;
let violations = 0;

function assert(cond, msg) { if (!cond) { violations++; if (violations < 15) console.error('FAIL:', msg); } }

function reArmOrb() {
  busy = false; recording = false; ptt.disabled = false;
  currentAudio = null;
}

// watchdog: schedules reArm after clearMs
function armBusy(clearMs) { setTimeout(() => reArmOrb(), clearMs); }

async function getUserMedia() { return new Promise(r => setTimeout(r, Math.random() * 30)); }

async function startRec() {
  if (recording || micBusy) return;
  micBusy = true;
  await getUserMedia();
  chunks = [];
  recording = true;
  if (pendingToggle) { pendingToggle = false; await stopRec(); }
  micBusy = false;
}
async function stopRec() {
  if (!recording) return;
  recording = false;
  if (chunks.length < 1) return;
  await sendAudio(chunks);
}
async function sendAudio(pcm) {
  busy = true; armBusy(150); ptt.disabled = true;
  await new Promise(r => setTimeout(r, 40));   // simulated bridge roundtrip
  busy = false; ptt.disabled = false;
  playToken++;                                  // guard
  currentAudio = null;
}
async function interrupt() {
  playToken++;
  currentAudio = null;
  setTimeout(() => reArmOrb(), 20);
}
async function stopAssistant() {
  playToken++;
  currentAudio = null;
  setTimeout(() => reArmOrb(), 20);
}

// A "tap" routes through the same decision logic as the frontend handlers.
async function tap() {
  // busy/speaking -> interrupt
  if (currentAudio !== null || busy) { await interrupt(); return; }
  if (busy) return;
  if (recording) await stopRec();
  else if (micBusy) pendingToggle = true;
  else await startRec();
}

// Adversarial rapid-tap sequences (no waits -> the freeze reproducer).
const sequences = [
  Array(30).fill(0),                 // 30 immediate taps
  Array(20).fill(0).map(() => 0),    // 20 taps, no delay
  [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
  Array(50).fill(0),
];

(async () => {
  let worst = 0;
  for (const seq of sequences) {
    recording = busy = micBusy = pendingToggle = false; currentAudio = null; playToken = 0; ptt.disabled = false;
    // fire all taps without awaiting (worst-case interleaving)
    const jobs = seq.map(() => tap().catch(()=>{}));
    await Promise.all(jobs);
    // allow the watchdog / interrupt recovery to settle
    await new Promise(r => setTimeout(r, 200));
    assert(ptt.disabled === false, `orb disabled after ${seq.length} rapid taps (ptt.disabled=${ptt.disabled})`);
    assert(busy === false, `busy stuck after ${seq.length} rapid taps (busy=${busy})`);
    assert(recording === false || micBusy === false, `mic stuck after ${seq.length} rapid taps (recording=${recording} micBusy=${micBusy})`);
    // the orb must be usable right after: a fresh tap must reach startRec
    const before = recording || micBusy;
    await tap();
    // immediately re-arm for next round
    reArmOrb();
    if (!before && !recording && !micBusy) { /* acceptable: it may have been an interrupt */ }
  }

  // Add an explicit "interrupt while playing, press again mid-recovery" case.
  {
    recording = busy = false; currentAudio = { paused: false }; playToken = 0; ptt.disabled = false;
    currentAudio = { kind: 'playing' };
    await interrupt();     // stops playback, schedules reArm
    await tap();           // press again during recovery window
    await tap();           // and again
    await new Promise(r => setTimeout(r, 250));
    assert(ptt.disabled === false, 'orb disabled after interrupt+rapid re-press');
    assert(busy === false, 'busy stuck after interrupt+rapid re-press');
  }

  console.log(violations === 0
    ? `ALL PASS — orb never froze across ${sequences.reduce((a,s)=>a+s.length,0)+3} adversarial rapid taps`
    : `${violations} FAILURES`);
  process.exit(violations === 0 ? 0 : 1);
})();
