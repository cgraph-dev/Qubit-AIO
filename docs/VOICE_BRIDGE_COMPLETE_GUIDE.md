# Voice Bridge When Complete

## The short version

This project is a local personal assistant that lives on your computer.

You speak or type to it. It understands the request, looks only in the places
you allowed, remembers approved facts, asks for permission before risky work,
uses the right local tool, and gives the result back as text, speech, a report,
or a browser notification.

It is not one giant magical model. It is a small group of cooperating parts:

```text
you
 │  voice / text / image / video
 ▼
input adapters
 │  microphone→ASR, image/video→OCR or vision, text unchanged
 ▼
router and planner
 │  decides what kind of job this is
 ▼
context compiler
 │  approved memory + current facts + bounded evidence
 ▼
local model (Ollama / Qubit-brain4)
 │  understands, plans, and explains; it does not receive passwords
 ▼
permission broker
 │  allow / deny / ask for confirmation
 ▼
approved capability
 │  files, repositories, web, Chromium, inbox, reports, workers
 ▼
result + audit trail
 │
 ▼
you: speech, text, dashboard, screenshot, report, or notification
```

The model is the brain that proposes an answer or plan. The broker and the
adapters are the hands. The broker decides which hands are allowed to move.

## What “100% complete” means

It means the planned Voice Bridge system is wired, tested, and documented. It
does **not** mean the base model has become AGI or that every website will work
forever without maintenance.

At completion we should have:

- a working local voice and text assistant;
- bounded memory that can remember, correct, forget, export, and restore facts;
- a router that selects fast local answers, tools, workers, or a stronger tier;
- read-only access to approved files, repositories, web sources, reports,
  browser sessions, and inboxes;
- write and destructive actions behind explicit permissions and confirmation;
- real Chromium sessions for sites that need the user’s saved login state;
- reports with timestamps, source links, citations, screenshots when available,
  failures, omissions, and a clear executive summary;
- OpenClaw/Hermes workers connected through a controlled gateway;
- structured logs, rollback, canary testing, backups, and reproducible tests;
- an installable configuration that can be backed up and moved to another PC.

Some adapters are currently contracts or placeholders. They must not be called
“fully working” until their real source, permission checks, error handling, and
live tests are complete.

## The parts, in donkey language

### 1. Ears: speech input

The microphone captures audio. The ASR engine turns audio into text. The
assistant then handles that text exactly like typed text.

The configured machine uses a HyperX microphone and a separate output device.
The audio device must be tested with real push-to-talk turns, not only mocked
tests. If ALSA or PipeWire cannot open the microphone, the text assistant may
still work while the voice assistant is broken.

### 2. Eyes: images and videos

Images and videos are not fed magically into the text model. A media adapter
first extracts useful information:

- OCR reads visible words;
- a vision model describes images and objects;
- video is sampled into frames and its audio can be transcribed;
- timestamps and source paths are retained.

The resulting description, transcript, or selected frames become bounded,
cited evidence for the main model. Large raw videos should not be placed in a
prompt. The assistant should read only the required part.

### 3. Brain: the local model

Ollama runs the local model. `qubit-brain4` is the current production alias.
The local model writes the answer, decides what information it needs, and
proposes tool work.

The model does not directly control the computer. It produces a structured
request. The router and permission broker decide whether that request is safe.
This makes the system safer, easier to test, and faster to replace or upgrade.

The system can also use different model tiers:

- a fast local model for short answers, classification, and summaries;
- the main local model for normal conversation and planning;
- a stronger local or remote model only when the user has allowed it and the
  task justifies the extra time or cost.

This improves practical intelligence and speed without pretending that a
smaller model has learned everything from a larger model.

### 4. Memory: what the assistant keeps

Memory is separate from the model weights. The memory store contains approved
facts and task history, with scope, tier, source, confidence, timestamps, and
status.

The important operations are:

- remember a fact;
- search relevant recall facts;
- keep important core facts available;
- correct a stale fact;
- forget or tombstone a fact;
- export and restore the store;
- keep work memory separate from personal memory.

Memory is not automatically trusted just because it exists. Retrieved content
is quoted as reference material. It cannot rewrite the system persona, policy,
tools, routing, or permissions.

### 5. Context compiler: the librarian

Before a model request, the compiler selects a small useful packet:

1. the immutable system persona;
2. the current conversation;
3. approved core facts;
4. relevant recall memory;
5. bounded external evidence and citations;
6. an output reserve so the model has room to answer.

Everything has a budget. If something does not fit, it is omitted and the
omission is reported. A failed adapter must produce a visible typed failure,
not silently invent data.

The live chat path currently uses compiler mode `5c`, with a tested `4b`
rollback mode.

### 6. Router and planner: the traffic controller

The router answers: “What kind of request is this?”

Examples:

- casual question → answer locally;
- known memory question → search memory;
- research question → web/evidence adapter;
- coding task → repository access and an approved worker;
- email check → inbox adapter;
- send/delete/change request → permission broker first;
- complicated task → planner creates small observable steps.

Fast rules handle obvious requests cheaply. A semantic router or classifier can
be enabled when it has passed its evaluation gates. A slower planner is used
only when the job really needs it.

### 7. Adapters: the doors into the computer

Each data source has its own adapter and contract. The current scopes are:

```text
files       local files and folders
repos       source-code repositories
web         permitted web research
browser     browser pages and saved sessions
inbox       email or message inboxes
reports     generated report artifacts
worker      OpenClaw/Hermes task execution
memory      the assistant's approved memory
```

An adapter declares its scope, sensitivity, permissions, trust level, version,
freshness, and whether it is read-only. The evidence registry uses these
contracts to keep sources separate.

## Permissions: the assistant never gets a blank cheque

The user chooses permissions during setup. The safe default is deny until
allowed.

Permissions should be separate for:

- reading files;
- writing files;
- executing commands;
- reading browser sessions;
- reading email or social messages;
- sending messages;
- deleting or changing data;
- using the network;
- using a remote model or sending data outside the PC;
- storing information in long-term memory;
- using microphone, camera, or screen capture.

The model can request an action, but it cannot grant itself permission.
Destructive actions require a separate broker decision and normally a clear
user confirmation. A read-only request must not quietly become a write request.

Every decision should record what was requested, which capability matched, what
permission applied, whether confirmation was needed, and the final result.
Secrets must be redacted from prompts, logs, reports, screenshots, and error
messages.

## How computer and website access works

### Local computer

The assistant uses explicitly registered local capabilities rather than giving
the model unrestricted shell access. A capability validates paths, arguments,
scope, and permissions before a worker performs the operation.

For example, “read this repository and run its tests” can be allowed. “Delete
every file” is a destructive request and must be denied or explicitly
confirmed. The worker returns output and artifacts to the assistant; it does
not silently change the conversation’s rules.

### OpenClaw and Hermes

Coding or long-running work can be sent through the existing OpenClaw/Hermes
gateway. The voice bridge remains the front door: it chooses the route,
passes only the approved task context, waits for progress/results, and gives a
short local summary back to the user.

### Gmail, Instagram, Messenger, and other sites

For sites that depend on saved login state, the assistant uses a real headed or
persistent Chromium profile owned by the user. The user logs in manually once;
the assistant reuses the browser session without receiving the password.

The browser adapter should:

- use an allowlisted site and profile;
- read only the permitted pages;
- avoid storing raw cookies or passwords in memory;
- capture source URL, timestamp, title, and message identifiers;
- deduplicate already-seen items;
- ask before sending, deleting, liking, or changing anything.

An hourly inbox/social check is a scheduled read-only job. It compares the
latest permitted state with the previous checkpoint, creates a report for new
items, and notifies the user. If login expires, the job reports “needs login”;
it must not claim that there were no new messages.

## How a normal request travels

Suppose you say: “Check my email and tell me if anything important arrived.”

1. The microphone records your speech.
2. ASR turns it into text.
3. The router classifies it as an inbox read request.
4. The permission broker checks that inbox reading is allowed.
5. Chromium or the approved mail adapter reads the inbox.
6. The adapter returns normalized items with links, timestamps, and trust
   metadata.
7. The context compiler supplies only the relevant bounded items to Qubit.
8. Qubit summarizes them; quoted messages remain untrusted evidence.
9. The report writer creates a professional report with links and screenshots
   when available.
10. The dashboard, voice output, or notification shows the result.
11. The event log records success, omissions, failures, and the permission
    decision without exposing secrets.

If you instead say “send a reply,” the flow stops at the broker until the
message, recipient, and permission are clear.

## Reports: what a professional report contains

Every report should have:

- title, task, run ID, start/end time, and status;
- a short executive summary;
- new, changed, and unchanged item counts;
- each item’s source, direct link, timestamp, and confidence/trust;
- screenshots or saved evidence artifacts when requested and permitted;
- actions taken and actions deliberately not taken;
- adapter failures, login problems, omissions, and stale data warnings;
- a privacy note describing what was excluded or redacted;
- reproducible artifact paths and rollback/cleanup information.

“No new messages” is valid only when the source was reachable, the session was
authenticated, the requested scope was checked, and the checkpoint comparison
completed. Otherwise the report must say that verification was incomplete.

## How to use it day to day

You can use natural language:

```text
What is on my schedule today?
Search the project for the login bug and explain the likely cause.
Check Gmail and Messenger for new unread messages; do not send anything.
Research this question and give me sources and a professional report.
Remember that the current project path is ...
Forget the old project path.
Draft a reply, but ask me before sending it.
Run the tests and show me the failures; do not modify files.
```

The dashboard is the visual control surface. It shows activity, current route,
reports, approvals, errors, and health. Voice is the hands-free front door;
the browser/web endpoint is useful when speech is inconvenient.

### Speech quality and one-voice rule

Qubit uses one user-selected canonical voice. A small sentiment controller may
choose safe delivery states—neutral, warm, calm, concerned, urgent, or
excited—but these are presentation settings, not literal feelings. Fish Audio
can inform TTS design, but it is not Qubit's brain, memory, planner, or STT.

The local speech budget is strict: the complete agent may use at most 5 GB
VRAM, targeting 4 GB or less. The resident model stays on the GPU; Piper TTS
and default ASR stay on CPU, and no second large model may be loaded beside
them. This keeps the voice responsive and leaves headroom for the rest of the
agent.

Background audio capture is a separate permissioned feature. It is off by
default, visibly indicated when active, bounded to a local ring buffer, and
subject to pause, deletion, and retention expiry. Ambient audio is never
silently added to memory or training.

## How it becomes faster and more useful

The system gets faster through engineering, not by blindly adding more prompt
text:

- route easy requests to fast local paths;
- keep the main model warm in Ollama;
- stream model output so TTS starts before the complete answer is finished;
- cache safe repeated evidence for a short, explicit TTL;
- use FTS5 and bounded retrieval instead of sending the whole memory database;
- use smaller ASR, classifier, OCR, or embedding models for specialist jobs;
- parallelize independent read-only adapters with deadlines;
- stop work immediately when the user cancels;
- measure cold start, warm latency, tokens, VRAM, CPU, failures, and fallbacks;
- promote a candidate model only after an isolated canary and rollback test.

The assistant becomes more useful through controlled learning:

- save only approved memories;
- record corrections as corrections, not as extra contradictory facts;
- learn successful task procedures as versioned recipes;
- evaluate recipes before making them reusable;
- let the user inspect, export, correct, or delete learned data;
- never train on secrets or private content without explicit permission.

This improves the assistant around this computer. It does not copy another
model’s private weights or “eat” an entire model safely. Other models can be
used as temporary teachers or evaluators, but their output must be treated as
untrusted training data and checked before it changes behavior.

## How to add capabilities without breaking the system

Use this order for every new capability:

1. Define the user-facing task and its permission scope.
2. Add or reuse an adapter contract; do not call a website or shell directly
   from the model prompt.
3. Keep read and write operations separate.
4. Add input validation, path/site allowlists, redaction, timeouts, and a
   typed failure result.
5. Add unit tests, security tests, integration tests, and a real live smoke
   test where practical.
6. Add an example configuration and documentation.
7. Add observability: run ID, source, latency, result, omission, and failure.
8. Test rollback and run the full suite.
9. Release behind a disabled flag or canary first.

Agents must read the repository rules and the relevant plan before every new
task. They must copy the smallest proven upstream implementation when one
exists, preserve project contracts, document every behavior change, and never
say “done” while tests are skipped, mocked when a live test is required, or
known failures remain.

## Backup and moving to another computer

The assistant is portable if its state is separated from machine-specific
hardware and credentials. Back up:

- configuration templates and permission policy;
- the memory database and export format;
- reports and approved recipes;
- model names/checksums and installation instructions;
- browser profile only through a secure user-controlled backup method;
- migration notes and versioned schema changes.

Do not blindly copy browser cookies or secret tokens. On a new PC, install the
software and models, restore the approved state, reconnect hardware, and log in
to websites again. If Ubuntu is reinstalled without a backup, the assistant’s
memory, permissions, reports, and browser sessions can be lost even though the
source code remains available.

## The honest final picture

When complete, you will have a fast local personal assistant platform: voice,
text, images, video understanding through specialist adapters, memory,
research, browser/inbox reading, reports, coding workers, permissions,
approvals, and rollback in one connected system.

It will be more capable and usually faster because it chooses the smallest
appropriate path and keeps useful state locally. It will not be infallible. A
website can change, a microphone can disappear, a model can hallucinate, and a
permission can be denied. The professional design makes those cases visible,
recoverable, and safe instead of pretending they succeeded.
