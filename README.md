# The Stand

**The room for your hardest conversations.** A live-voice sparring room:
practice a cross-examination, a B2B discovery call, or a dissertation
defense — against a fictional, live, interruptible voice counterpart — and
get evasive, in-character answers you can push back on. Started as a voice
cross-examination trainer for junior litigators; the room and its rubric
mechanism generalize to any hard, high-stakes conversation (see F7/F14-F16
below).

> The Stand trains technique. It does not give legal advice. All case files,
> witnesses, and facts are fictional.

## What's here (M0 — walking skeleton)

- `witness_agent/` — a single ADK agent (`WitnessAgent`) that plays a fictional
  witness using Gemini Live for bidirectional, native-audio, interruptible
  voice conversation.
- `case_files/martinez_v_nordbay.yaml` — one fictional case file (witness
  persona, affidavit, escalation levels, a short scoring rubric).

See `docs/architecture.svg` for the full multi-agent architecture diagram
(WitnessAgent -> RubricScorer -> DebriefAgent -> Firestore, Cloud Run
deploy).

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
expected). Click "End session." to end and see the debrief.

`adk web` (see Run, above) still works standalone for the M0/M1 witness
agent alone — it just has no dial UI or sidebar, which is what this server
adds.

## Eval suite (M3, F6)

`eval/run_eval.py` is the CI-shaped entry point the brief asks for — a
runnable command that prints pass/fail per criterion.

**Why this isn't `adk eval` against WitnessAgent directly:** checked
adk-docs (`evaluate/index.md`) before writing anything here. Every path ADK
offers — web UI, `pytest`, and the `adk eval` CLI — drives an agent through
`AgentEvaluator`, which runs turn-based `run_async` sessions. There is no
session-replay path for a bidi Live connection in ADK 2.8.0's evaluation
framework, so WitnessAgent (Live/bidi, `run_live`) genuinely cannot be
evaluated this way. RubricScorer and DebriefAgent also aren't ADK `Agent`s
in the first place — they call `google.genai` directly (see
`rubric_scorer/scorer.py`, `rubric_scorer/debrief.py`), so `AgentEvaluator`
has nothing to invoke for them either.

The honest scoped equivalent: `eval/rubric_judge_agent/` and
`eval/debrief_judge_agent/` are turn-based ADK `Agent` **doubles** that
reuse (import, not copy) RubricScorer's and DebriefAgent's exact system
prompts, so the real judging logic runs through the real `AgentEvaluator`
pipeline instead of a bespoke harness pretending to be one. They're graded
with ADK's `rubric_based_final_response_quality_v1` and
`rubric_based_multi_turn_trajectory_quality_v1` criteria (LLM-as-judge,
`gemini-3.7-flash`, per-case rubrics) — a genuine stock-ADK fit, since these
criteria don't require tool calls or exact-match reference responses, just
a judge checking specific properties of a text response. Each eval set lives
in its own subdirectory under `eval/eval_sets/` because `AgentEvaluator`
auto-discovers a sibling `test_config.json` per directory
(`AgentEvaluator.find_config_for_test_file`), not per file.

Three eval sets:
- `eval_sets/rubric_scorer/` — T-01..T-05 scripted deterministic exchanges
  from `expert_dossier.md`, each case's rubric checking the correct `D-xx`
  citation with the correct triggered/violation polarity.
- `eval_sets/novice_trajectory/` — the scoped-down stand-in for a
  TTS-User-Simulator: a full audio simulator was out of scope for this
  milestone, so this is a hand-authored 4-turn NOVICE-examiner-persona text
  trajectory (open question → compound question → well-formed impeachment
  question → weak open follow-up that loses the thread), graded on the
  judge's behavior across the WHOLE session via
  `rubric_based_multi_turn_trajectory_quality_v1`.
- `eval_sets/debrief/` — T-06, DebriefAgent's session-close output (AMTA
  score range, citation reuse, courtroom-sober tone).

`run_eval.py` also runs `RubricTrajectoryJudge`
(`rubric_scorer/trajectory_judge.py`) **directly** — a new session-level
`gemini-3.7-flash` judge, extending RubricScorer's per-exchange logic to a
full-transcript replay, independent of the `AgentEvaluator` wrapper — against
the same novice trajectory, and checks it never invents a `dxx` citation
outside the case's rubric.

Run it:

```bash
pip install "google-adk[eval]==2.8.0"  # already in requirements.txt
python eval/run_eval.py
```

**Real output from the last run** (all real `gemini-3.7-flash` calls, no
mocking):

```
=== EVAL SUITE SUMMARY ===
PASS: rubric_judge_agent vs rubric_scorer.evalset.json (T-01..T-05)
  all criteria met threshold
PASS: rubric_judge_agent vs novice_trajectory.evalset.json (simulated NOVICE)
  all criteria met threshold
PASS: debrief_judge_agent vs debrief.evalset.json (T-06)
  all criteria met threshold
PASS: RubricTrajectoryJudge direct run (no invented citations)
  no invented dxx ids

OVERALL: PASS
```

## Deploy (M3, F8)

Deployed to Cloud Run in the `the-stand-2026` GCP project, region
`europe-west1`, from the FastAPI app in `server/app.py` (not `adk deploy
cloud_run` / `adk web` — the real product is the custom WebSocket server, so
a plain `Dockerfile` + `gcloud run deploy` per adk-docs' documented "gcloud
CLI for Python" path, not the ADK dev-UI deployment).

**Timeout/memory traps** (per `stack_briefing.md`, both explicit, neither
left at default): Cloud Run's default request timeout (300s) is far too
short for a multi-minute voice cross-examination session, and the default
memory (512Mi) OOMs loading the ADK/Gemini/Firestore stack.

```bash
gcloud run deploy the-stand \
  --source . \
  --project=the-stand-2026 \
  --region=europe-west1 \
  --allow-unauthenticated \
  --timeout=3600 \
  --memory=1Gi \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=the-stand-2026,GOOGLE_GENAI_USE_ENTERPRISE=FALSE" \
  --set-secrets="GOOGLE_API_KEY=GOOGLE_API_KEY:latest"
```

The API key is a Secret Manager secret (`GOOGLE_API_KEY`, project
`the-stand-2026`), not a plain env var — Secret Manager wasn't in the
original H.T stack-contract API list, but enabling it took one
`gcloud services enable secretmanager.googleapis.com` call (a few seconds),
so the brief's "prefer Secret Manager unless meaningfully more friction"
call resolved in favor of Secret Manager. The compute default service
account was granted `roles/secretmanager.secretAccessor` (read the secret)
and `roles/datastore.user` (Firestore session persistence, see below).

**Firestore session persistence:** ADK 2.8.0's Python package has no native
Firestore session service — the `FirestoreSessionService` documented on
adk-docs is Java-only (checked `integrations/firestore-session-service`,
explicitly `Supported in ADK Java`; `google.adk.sessions` in the installed
Python package only ships `InMemorySessionService`, `DatabaseSessionService`
and `VertexAiSessionService`). Per the brief's own fallback, `server/
firestore_store.py` is a direct `google-cloud-firestore` client wrapping the
session state that actually matters for the product — case selection,
transcript, scored rubric events, and the debrief — one document per session
in the `the_stand_sessions` collection, written incrementally from
`server/app.py`'s WebSocket handler. ADK's own `InMemorySessionService`
still runs the Runner's internal session bookkeeping (rewriting that as a
custom `BaseSessionService` wasn't worth it for this milestone). Writes are
best-effort and never raise into the live session, the same tolerance
pattern the RubricScorer already used for its own failures. A Firestore
Native database had to be created first (`gcloud firestore databases
create --location=europe-west1 --type=firestore-native`) — the project had
Firestore's API enabled but no database provisioned yet.

Live URL: **https://the-stand-596357648145.europe-west1.run.app**

```
$ curl -s -o /dev/null -w "%{http_code}\n" https://the-stand-596357648145.europe-west1.run.app/
200
$ curl -s https://the-stand-596357648145.europe-west1.run.app/api/cases
{"cases":[{"case_id":"martinez_v_nordbay", ...}, {"case_id":"chen_v_summit_biotech", ...}],
 "disclaimer":"The Stand trains technique. It does not give legal advice."}
```

## Status

M3: `adk eval`-shaped eval suite (`eval/run_eval.py`, real `gemini-3.7-flash`
judging, no mocked results) and a real Cloud Run deploy with Firestore
session persistence. Still no full UI-shell polish, multi-language witness,
or profession-module config swap — see the project brief for the remaining
roadmap.

**F5 (debrief) scope: text excerpts, not audio clips.** The brief asks for
the two key debrief moments as audio clips; building session-audio
buffering to cut and serve real clips wasn't worth the remaining build
time, so F5 stays what it already was — `[D-xx]`-cited text excerpts with
timestamps. Instead, the demo's Proof beat (brief 1.6, 2:00-2:45) shows
`adk web`'s own eval-results viewer, which renders playable inline audio
clips per turn when reviewing a run — per Google's ADK blog post
(https://developers.googleblog.com/how-to-evaluate-live-voice-agents-in-adk/).
That playback comes from the underlying event data actually containing
audio (`inline_data`) parts, which only a Live/bidi run produces. Our
original three eval sets (`eval/eval_sets/rubric_scorer`,
`eval/eval_sets/debrief`, `eval/eval_sets/novice_trajectory`) evaluate
`rubric_judge_agent` and `debrief_judge_agent` — text-turn doubles of the
RubricScorer/DebriefAgent judges, run through plain `run_async` — so their
eval results have no audio to play back. `eval/eval_sets/live_audio_witness`
closes that gap: WitnessAgent itself, run Live/bidi, against a
`conversation_scenario` driven by ADK 2.8.0's `LlmAudioUserSimulatorConfig`
(real TTS-generated audio turns, `gemini-2.5-flash-preview-tts`). Its
`.evalset_result.json` carries real `inline_data` audio parts, and
`adk web`'s Evals tab plays them back per turn (evidence:
`docs/eval_live_audio_playback.jpg`). Beat 4 can show this run directly —
`adk eval re-plays a voice session — with playable audio` is now `[ECHT]`,
not a downscoped claim. See `eval/run_eval.py`'s module docstring for how
to run it and browse it (it isn't wired into that script — it goes through
`LocalEvalService`/`adk eval`, not `AgentEvaluator`, since it's a live
simulation rather than a scripted-turn replay).

**Cost telemetry and architecture diagram (Close beat, brief 1.6).**
`server/cost_tracker.py` accumulates real `usage_metadata` token counts off
the actual Gemini responses (witness, RubricScorer, DebriefAgent) for the
session and ships them in the `debrief` WebSocket message's `cost` field,
rendered as a line under the debrief panel. Token counts are never
estimated — only the RubricScorer/DebriefAgent calls (`gemini-3.7-flash`,
token-priced) get a USD estimate, computed from published pricing
(https://ai.google.dev/gemini-api/docs/pricing, checked 2026-08-29); the
witness runs on a Live/bidi audio model Google prices per minute of audio,
not per token, so its token counts are shown without a fabricated dollar
figure. Architecture diagram: `docs/architecture.svg`.

## New modes and Bring Your Own Case (Round 2, F14-F16)

The room generalizes past cross-examination: `case_files/*.yaml` is the
only thing that changes between a courtroom, a sales call, and a
dissertation defense — persona, escalation ladder, and rubric, in one file,
with the WitnessAgent/RubricScorer/DebriefAgent pipeline unchanged.

**F14 — German B2B discovery call** (`case_files/discovery_call_de.yaml`,
Britta Vogel / "Rheinwerk Logistik GmbH", fictional). Same sales rubric
(S-01..S-04) as the English discovery case — language is a witness trait
(F12), not a rubric translation. Functionally verified (WS session opens,
audio flows, German `SpeechConfig.language_code` resolves); voice-quality
QA note in `docs/DIRECTORS_NOTES.md` since no agent in this pipeline can
hear audio.

**F15 — Dissertation defense** (`case_files/dissertation_defense.yaml`).
The "witness" is one agent voicing a two-register committee (measured
chair + sharper external reader). Rubric citation policy per this
project's cite-or-GAP rule: 4 of the 5 criteria are paraphrased from a
real, fetched university dissertation-defense evaluation rubric (Andrews
University and SMU Ph.D. Program in Clinical Psychology, both `.edu`,
fetched and quoted 2026-08-29 — full URLs and verbatim quotes are in the
case file's `rubric[].source` fields). The 5th criterion ("stays factual
and composed when a question turns adversarial") has **no citable
published rubric line** in the sources checked (Andrews, U. Rochester,
SMU) — it's marked `"Common defense-prep practice, uncited"` in the case
file rather than dressed up with an invented source. This is the one
honest gap the prompt's own escape hatch anticipates.

**F16 — Bring Your Own Case** (`witness_agent/case_generator.py`,
`server/firestore_store.py`'s `UploadedCaseStore`, `POST
/api/cases/upload`). Upload a PDF or text document in Defense or Sales
mode only — **not** legal cross-exam, which stays fiction-only per brief
Section 8's confidentiality principle (Rule 1.6, D-28). Generation runs
once at upload time (never inside a live session — the leitplanken's
latency rule), as a single `gemini-3.7-flash` call over the document
(native PDF understanding, no separate PDF-parsing dependency), producing
a title/summary/affidavit and 3-5 goals that must each cite a real
page/section/quote from the document — a goal without a citation is
dropped rather than invented. The escalation ladder, rubric, and persona
archetype are **not** regenerated per upload; they're merged in from the
matching curated case (`dissertation_defense.yaml` or
`sales_discovery_call.yaml`) — the technique being trained doesn't change
just because the source material does, and a rubric isn't something this
project improvises per upload. Persisted in Firestore
(`the_stand_uploaded_cases`, best-effort, same philosophy as session
persistence) so a generated case survives a restart; an in-process cache
keeps an upload immediately playable and listed even before/without a
Firestore write landing. Upload capped at 20MB (`MAX_UPLOAD_BYTES`,
`witness_agent/case_generator.py`) — comfortably under Cloud Run's 1Gi
memory and 3600s timeout (both already set explicitly, see Deploy above),
well within Gemini's ~1M-token input context for a bound document like a
dissertation or product briefing. The uploaded document's full text is
never logged — only filename/size/mode.
