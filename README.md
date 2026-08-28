# The Stand

Voice cross-examination trainer for junior litigators. Practice cross-examining
a fictional witness — in real time, by voice — and get interrupted, evasive,
in-character answers you can push back on.

> The Stand trains technique. It does not give legal advice. All case files,
> witnesses, and facts are fictional.

## What's here (M0 — walking skeleton)

- `witness_agent/` — a single ADK agent (`WitnessAgent`) that plays a fictional
  witness using Gemini Live for bidirectional, native-audio, interruptible
  voice conversation.
- `case_files/martinez_v_nordbay.yaml` — one fictional case file (witness
  persona, affidavit, escalation levels, a short scoring rubric).

## Setup

Requires Python 3.12.

```bash
cd product
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your API key:

```bash
cp .env.example .env
# then edit .env and set:
# GOOGLE_API_KEY=your-actual-key-from-https://aistudio.google.com/apikey
```

Get a key at [Google AI Studio](https://aistudio.google.com/apikey).

## Run

```bash
export SSL_CERT_FILE=$(python3 -m certifi)
adk web
```

Open the printed URL (usually `http://localhost:8000`) in your browser,
select `witness_agent`, click the microphone, and start cross-examining Dale.
You can interrupt him mid-sentence — he'll stop and let you talk (barge-in
is native to the Gemini Live API).

Note: text chat isn't supported with native-audio models in `adk web` —
use voice.

## Character fidelity & the pressure dial (M1)

The witness now has three escalation levels (`case_files/*.yaml` ->
`witness.escalation`), a character-fidelity guardrail, and a live pressure
dial, all built on one mechanism: session state + a live content channel.

**Escalation / character fidelity (F4):** `witness_agent/agent.py` builds the
instruction dynamically from `session.state["pressure_level"]` (1-3, default
1) via an ADK `InstructionProvider`. On top of that, `before_model_callback`
(`guard_character`) is a deterministic backstop: it pattern-matches the
latest user turn for prompt-injection ("ignore your instructions", "break
character", "you're an AI"...) and real-legal-advice requests, and — when it
matches — short-circuits the model call with a canned in-character
deflection instead of trusting the model to hold the line every time. Both
layers are exercised by `tests/test_character_fidelity.py`.

**Pressure dial (F10):** the dial's *starting* level for a session is
`session.state["pressure_level"]`, settable when the session is created.
`adk web`'s Session tab does let you inspect and edit session state live, but
we found (empirically, via `tests/test_pressure_dial.py`) that this does
**not** reach an already-open Live/bidi connection: Gemini Live sends the
system instruction once in its `setup` message at `connect()` time, so a
mid-connection `session.state` edit is invisible until the next reconnect.
So for an *already-open* voice session, the dial is operated by sending a
`[STAGE DIRECTION: ...]` content turn (`stage_direction_for_level(n)` in
`witness_agent/agent.py`) — real per-turn content, which bidi streaming does
pick up. The instruction tells the witness to silently adopt the described
demeanor and never acknowledge the bracket out loud.

**How to actually operate the dial today:** there is no dial UI yet (that's
M2 scope, the rubric sidebar). Today, a human/script drives it by calling
`live_request_queue.send_content(...)` with the text from
`stage_direction_for_level(level)` on the same `LiveRequestQueue` the session
is using — see `tests/test_pressure_dial.py` for a working example. It is not
reachable from `adk web`'s mic/chat UI as a distinct control (typing the
bracketed text into the chat box would work mechanically, since it's just a
user-role content turn, but it would show up in the transcript as if the
examiner said it — acceptable for an M1 smoke test, not for the real product).

## Model fallback

The brief specifies a fallback model
(`gemini-2.5-flash-native-audio-preview-12-2025`) if the primary
(`gemini-3.1-flash-live-preview`) becomes unavailable. ADK 2.8.0's Python
`LlmAgent.model` only accepts a single model string or `BaseLlm` instance —
there's no first-party runtime fallback primitive to hang this on.
(`RoutedLlm`, which does exactly this, is TypeScript-only and marked
Experimental in ADK's own docs; it doesn't exist in the `google-adk` Python
package as of 2.8.0. Live connections also can't switch models mid-stream
even where routing exists.) Building a manual reconnect-with-different-model
loop around `run_live()` would be exactly the fragile hack the brief warns
against, so the fallback stays a documented manual swap of the `LIVE_MODEL`
constant in `witness_agent/agent.py` rather than automatic runtime handling.

## Barge-in / session stability (F1)

No extra `RunConfig` flags were needed for barge-in: Voice Activity Detection
is enabled by default on all Gemini Live models and natively handles
interruption/turn-taking (confirmed via ADK docs, not just the M0 assumption).
For the 3-minute uninterrupted session bar: Gemini Live's connection duration
limit (~10 min) and audio-only session duration limit (15 min) both
comfortably exceed 3 minutes, so `RunConfig.session_resumption` isn't
required to hit that specific acceptance number. It's also not wireable
through `adk web` today in a way worth doing here: `adk web`'s own websocket
endpoint does accept an `enable_session_resumption` query flag, but the
bundled dev UI has no toggle for it, and there's no App/agent-level `RunConfig`
hook — `adk web` builds `RunConfig` itself, per-connection, from its own
query params. Multi-turn stability was verified the way that's actually
testable without a mic: `tests/test_character_fidelity.py` (5 sequential
turns) and `tests/test_pressure_dial.py` (3 sequential turns) both ran to
completion on continuous `run_live()` connections with no framework-level
errors.

## Rubric sidebar, second case file, debrief (M2)

**Second case file (F2):** `case_files/chen_v_summit_biotech.yaml` — a QC lab
manager witness (Priya Raghavan), different evasion style from Dale (clinical
jargon-as-shield vs. blue-collar deflection). Both case files now carry an
`impeachment_fact` block: a specific, checkable detail in the affidavit
(a timestamp) that contradicts a document the examiner can confront the
witness with — the impeachment hook the demo needs.

**RubricScorer (F3):** `rubric_scorer/scorer.py`. Deliberately NOT the Live
model — a separate `gemini-3.7-flash` text-only call (per the H.T stack
contract's Critic/Judge pattern from adk-samples/llm-auditor) that scores one
examiner-question/witness-answer exchange at a time against the active case
file's rubric, using Gemini structured output (`response_schema`) so every
event is `{criterion, dxx, triggered, violation, note, score_delta}` — never
a citation the model invented, since it can only choose from the `dxx` ids
handed to it in the prompt. 2-3s latency behind audio is fine per the brief,
so it's called fire-and-forget per witness turn from the server, not inline
in the live audio path.

**Wiring (`server/app.py`):** we did NOT use `adk web` / `adk api_server` for
this — per adk-docs (Live dev guide Part 1, "FastAPI Application Example"),
the documented, idiomatic pattern for a custom Bidi-streaming client is to
drive `Runner.run_live()` directly from your own FastAPI WebSocket endpoint
with an upstream task (WebSocket → `LiveRequestQueue`) and a downstream task
(`run_live()` events → WebSocket) running concurrently via `asyncio.gather`.
That's exactly what `server/app.py` does, with our own small JSON message
contract instead of raw `Event` dumps (documented at the top of that file) so
the browser only has to understand audio/transcript/score/dial/debrief
messages. `witness_agent/agent.py` gained `make_agent_for_case(case_id)`, a
factory that builds a fresh case-bound `Agent` + instruction provider + dial
function per session, so the server can run either case file per connection
while the M1 tests keep using the original module-level `root_agent` (still
defaulted to Martinez) unchanged.

**Sidebar UI (F9 partial):** `server/static/index.html` + `app.js` — plain
HTML/vanilla JS (no framework, no build step), served by the same FastAPI app
at `/`. Case picker, "Take the stand" button, a live pressure-dial slider
(1-3, sends `{"type":"dial","level":n}` which becomes a `[STAGE DIRECTION]`
content turn on the open `LiveRequestQueue` — the M1 mechanism, now with a
UI), a rubric sidebar that ticks new score lines as they arrive with their
`[D-xx]` citation, and a running AMTA-scale score total. Mic capture uses
`ScriptProcessorNode` (deprecated but needs no separate worklet file — the
pragmatic call for a timeboxed build) downsampled to 16kHz PCM16; playback
queues 24kHz PCM16 chunks through `AudioBufferSourceNode`.

**Debrief (F5):** `rubric_scorer/debrief.py` — one more `gemini-3.7-flash`
call, over the full transcript plus every scored rubric event from the
session, producing an AMTA 1-10 score, the two most important moments as
transcript excerpts (each citing a `[D-xx]` reused from what was actually
scored, never invented), and one concrete practice-focus recommendation.
Copy tone is enforced in the system prompt: terse, courtroom-sober, no
gamified language. Fires when the WebSocket session ends (`end_session`
message or disconnect) and is pushed to the browser as a `{"type":"debrief"}`
message, rendered as a simple full-screen panel.

**Test results (T-01…T-06 from `expert_dossier.md`):** run with
`python tests/test_rubric_scorer.py` (scripted text transcripts, no audio,
real `gemini-3.7-flash` calls). All 6 pass, but two are honestly partial:
- T-01 and T-05 in the dossier assume things `RubricScorer` doesn't model —
  a direct-vs-cross distinction (T-01) and cross-exchange memory of a missed
  objection (T-05), since the scorer is stateless per single Q/A exchange.
  The tests verify the applicable half of each (leading-question recognition
  for T-01, hearsay-objection recognition for T-05) and say so explicitly in
  their output rather than claiming a clean pass.
- T-02, T-03, T-04 map directly and pass cleanly.
- T-06 (AMTA score display) tests `DebriefAgent`, not `RubricScorer` — it's a
  session-close concern, not a per-turn one.

## Try it locally

```bash
cd product
source .venv/bin/activate
export SSL_CERT_FILE=$(python3 -m certifi)
uvicorn server.app:app --reload
```

Open `http://localhost:8000`, pick a case file, click "Take the stand.",
allow microphone access. The dial slider is live once a session starts; the
sidebar ticks as the rubric scorer catches up (2-3s behind audio is
expected). Click "Session beenden" to end and see the debrief.

`adk web` (see Run, above) still works standalone for the M0/M1 witness
agent alone — it just has no dial UI or sidebar, which is what this server
adds.

## Status

M2: second case file with an impeachment hook, a live rubric-scoring sidebar
backed by a real (if per-exchange-stateless) critic model, a pressure-dial UI
driving the M1 stage-direction mechanism, and a courtroom-toned debrief — all
served from The Stand's own FastAPI app, not `adk web`. Still no `adk eval`
suite, Cloud Run deploy, Firestore persistence, or full UI-shell polish — see
the project brief for the remaining roadmap (M3).
