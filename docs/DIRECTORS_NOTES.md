# Director's Notes — Round 1 (Creative Review Fixes)

> Source: `creative_review.md` (JURY-VERDICT: FIX-THEN-SUBMIT, 2026-08-29), Fix 3. For `/academy-submit` to fold into `demo_script.md` and the shot list.

## Cut timing
- Pull the interruption moment ("Oh!"-Moment, mid-sentence witness cut-off → in-character answer) as early as possible in Beat 3 (Brief 1.6). Current rehearsal lands it ~1:10 — target is *before* the 1:00 mark. [W-20]
- This is the single moment the video is built around; every other beat can flex around it, not the reverse.

## Thumbnail (300×300, Devpost)
- Show the room (the witness stand / cross-exam view) or the rubric sidebar.
- Do **not** use an architecture diagram — that's the wrong register for the 5-second thumbnail test (save the diagram for the Close beat / repo asset).

## Rubric sidebar polish (optional, time permitting)
- Aesthetics is currently 7/8 (Mike Swift, 6D diagnosis) — "clean but generically clean."
- If time remains after the demo-critical path is locked: nudge the sidebar's typography toward more "verdict weight" (heavier rule lines, a more judicial type scale). This is a finish pass, not a rebuild — no layout changes.

# Round 2 (UI Redesign + German Sales Case)

> Source: `creative_prompts_round_2.md` (Prompt 4 + 5), design canvas `design/the-stand/*.dc.html`.

## German case (`discovery_call_de`, Britta Vogel / Rheinwerk Logistik) — voice QA

- **Functionally verified (agent, not ear):** `case_language_code()` resolves `de-DE` correctly for this case, `make_agent_for_case("discovery_call_de")` builds a working `LlmAgent` bound to the German persona, and a synthetic-audio WS session against it connects cleanly (no `1008`, no server error, upstream audio chunks flow) — same regression check run against the redesigned UI for all 5 cases.
- **Not verified by any agent in this pipeline: how the German actually *sounds*.** No agent in this project can hear audio. A synthetic sine-tone probe proves the pipe is open, not that `gemini-3.1-flash-live-preview` produces natural, accent-free German speech.
- **Before this case goes anywhere near the demo video:** a human needs to run one real session against Rheinwerk Logistik and listen. If the German voice quality is weak (robotic, mispronounced, or clearly non-native-sounding), ship the case anyway (it's a legitimate F12 feature and passes every functional check) but leave it out of the recorded demo — this note is the required documentation for that decision, per Prompt 5's acceptance criteria. If it sounds good, Beat 6 (Vision) can use it as a second language-swap beat alongside the existing Spanish Lopez case.

# Round 3 (Dissertation Defense + Bring Your Own Case)

> Source: `creative_prompts_round_2.md` (Prompt 6 + 7). This is the round the prompt file itself marks as the **final** feature scope — no further Build→Examine→Revise cycle should add new features after this one lands and passes re-review.

## Dissertation defense (`dissertation_defense.yaml`) — rubric honesty

- Rubric research was done live (WebSearch + WebFetch, actually reading fetched university PDFs, not from memory) against Andrews University, University of Rochester, and SMU Ph.D. Program in Clinical Psychology's published dissertation-defense evaluation rubrics. 4 of 5 rubric criteria are direct paraphrases of a verbatim-quoted line from a real, fetched `.edu` PDF (URLs + quotes are in the case file itself).
- The 5th criterion ("stays factual and composed under an adversarial question") has **no citable published rubric line** in any of the three sources actually read. Marked `"Common defense-prep practice, uncited"` in the case file and called out in `README.md`'s new F14-F16 section, per the prompt's own explicit escape hatch for this exact situation — not invented as if sourced.
- Functionally verified with a real (not synthetic) `RubricScorer` call against the live Gemini API: an exchange about methodology choice correctly triggered `DEF-02` with an honest, non-templated note. `make_agent_for_case("dissertation_defense")` builds successfully; escalation levels 1-3 (wohlwollend → bohrend → feindselig-forensisch) all present and validated.

## Bring Your Own Case (F16) — real end-to-end proof, not just unit tests

- Ran the actual upload flow against a running local server with a real (non-fictional-legal) test document — a short synthetic ML-paper excerpt with page-cited claims — through both `POST /api/cases/upload?mode=defense` and `mode=sales`. Both completed in well under a minute against the live Gemini API and returned a playable, schema-valid case (`_validate_case` passes) whose `card_summary`/`summary` were genuinely grounded in the uploaded text's specific content (drift-reweighted FedAvg, CIFAR-10, Byzantine-client limitation) — not generic filler.
- **Found and fixed a real bug during this verification:** `/api/cases` only ever read uploaded cases from Firestore, never the in-process cache — so a freshly uploaded case could vanish from the very next case-list call on the *same* server that just generated it. Fixed by having `/api/cases` fall back to the in-process cache for any uploaded `case_id` Firestore's listing didn't return.
- **Correction — the fallback above was masking a second, more serious bug.** "Firestore reachable/writable on Cloud Run" was an assumption, not something actually checked at the time. `gcloud logging read` on the live service showed every single upload's Firestore write failing with `One or more components is not a string or is empty` — the in-process-cache fallback made the case *look* persisted (served by the same warm instance) when it actually wasn't landing in Firestore at all, on Cloud Run or locally. Root cause: PyYAML parses a case's `escalation:` block into a dict with integer keys, which `google-cloud-firestore` can't serialize as a map field. Fixed with key-stringifying/de-stringifying helpers in `server/firestore_store.py`. Re-verified three independent ways that don't trust the app's own cache: no more error in Cloud Run logs after a fresh upload, a direct Firestore client read (bypassing the app) confirms the document exists, and it survives the next `/api/cases` call. Deployed as `the-stand-00010-f9m`. Full writeup: `revision_log.md`'s Round 5 section, finding #34.
- Legal cross-exam mode confirmed absent from `upload_modes` (`{"defense", "sales"}` only) — the brief Section 8 confidentiality boundary holds.
- Not verified by any agent: what the generated persona *sounds* like reciting a citation live, or a real human's judgment of whether the generated attack lines feel "smart" rather than merely schema-valid — same category of gap as every other voice-quality note in this file. Worth one real human upload+session test (own dissertation or a product doc) before this goes in the demo video, per Prompt 7's own "Demo-Vorbereitung" note about pre-uploading Lutfiya's dissertation before the shoot.

# Round 4 (Repo-Grün, Cold-Start, Replay, Reverse-Beat, Antechamber)

> Source: `creative_prompts_round_3.md`, Prompts 1–4, plus `build-prompts/f24-antechamber.md` (F24, added mid-round).

## F20 — Repo-Grün + Judge-De-Flake

- `pytest -q` now runs `tests/test_pressure_dial.py::test_live_dial_mechanism` for real (added `pytest.ini` with `asyncio_mode = auto` + `pytest-asyncio` as a dev dependency in `requirements-dev.txt`, previously the async test silently errored out of collection). 7/7 passing, verified locally against the real Gemini Live connection.
- Two empty `<main>` landmarks (`roomView`, `debriefView` — both `hidden` by default, only one view is ever the actual document's main content) changed to `<div>`; `caseSelectView` keeps the one real `<main>`. No JS or CSS referenced the tag name, only element ids — zero behavior change.
- Favicon added as an inline data-URI SVG (small gold gavel-and-scales glyph matching the accent color) — no more console 404.
- `rubric_scorer/trajectory_judge.py`'s dxx-citation matcher now strips bracket wrapping (`[[D-01]]`, `[D-01]`) before matching against the rubric's bare `D-01` ids — see `directives/common_issues.md`'s 2026-08-29 entry, now marked fixed. `python eval/run_eval.py` → `OVERALL: PASS`, verified 2x consecutive plus the run that produced the F22 replay bundle (3rd).
- `UploadedCaseStore.delete_case` (already non-routed) now has a real invocation path: `python -m server.admin_prune_case <case_id>` — used for the Prompt 6 grid-hygiene step (deleting the stale `uploaded_cf7074ea6e`).

## F21 — Cold-Start

- Cloud Run service `the-stand`: `min-instances=1` set (`gcloud run services update the-stand --min-instances=1 --project=the-stand-2026 --region=europe-west1`), applied with explicit user sign-off given the ongoing cost. Cold curl after the change: 0.15s.
- **Superseded by F24's cold-test gate, not yet rolled back:** per the F24 acceptance criterion, `min-instances` only goes back to `0` after the antechamber's own cold-test passes 3x reproduced (service genuinely cold → open antechamber → ~15s → launch responds <1s). Until that's run, both `min-instances=1` and the antechamber stay active in parallel — no window without a net.

## F22 — "Watch a replayed session"

- Built via `eval/build_replay_bundle.py`, run once against a real `adk eval` invocation of `eval/eval_sets/live_audio_witness/` (see `eval/run_eval.py`'s docstring item 6 for how that run is produced). Extracts the witness's real Gemini Live output-audio chunks (base64 is URL-safe/unpadded — needed a custom decode step, `_b64decode`, not stdlib `base64.b64decode`), concatenates them into `server/static/replay/session_audio.wav`, and runs the transcript through the **same `RubricScorer` the live app uses** (offline, against the recorded Q&A pairs) to get real `[D-xx]`-cited score events — nothing in the bundle is invented, every audio byte and every citation traces back to the actual recorded session.
- Frontend (`server/static/replay.js`) is fully self-contained: does not touch `app.js`, the WS handshake, mic code, or `run_live` — confirmed by `git diff --stat` showing zero changes to those files this round.
- **Honesty-boundary note (important for the demo script):** this recorded session is a clean, linear Q&A exchange that ends with "Nothing further, Your Honor" — it does **not** contain a genuine interruption/barge-in event. The brief's replay acceptance criterion mentions the interruption moment as one of the sidebar states to reproduce; fabricating one here would cross the honesty boundary ("nichts wird als live behauptet" / never inventing evidence), so the replay only shows what actually happened in this session. If an interruption beat is wanted in the replayed artifact specifically (as opposed to the live demo, which already has one per the Hero Moment), it needs a fresh `adk eval` recording engineered to include a real barge-in — out of scope for this round, flagged as a GAP rather than silently worked around.
- **Verification gap (needs a human or a different environment):** this session's Chrome automation sandbox cannot decode *any* audio — confirmed with a trivial 1-sample data-URI WAV (`loadedmetadata`/`canplay` never fire, `readyState` stays 0 indefinitely) even via a blob URL with zero network involved. Everything server-side and file-level was verified directly instead: the WAV is a valid RIFF/PCM16/24kHz file (`file` command), the real FastAPI/Starlette static server returns correct `206 Partial Content` for Range requests (a plain `python -m http.server` does not, and was ruled out as the cause), a raw `fetch()` in the same sandboxed page downloads the full file successfully, and the transcript/sidebar sync logic was verified by manually driving `renderTurnUpTo`'s render path with the real bundle JSON (produces the correct running score and score-line count). **What's not yet verified: that clicking play in a real browser with working audio actually hears the witness and sees the sidebar advance in sync with real `timeupdate` events.** This is the Examiner's/human's job on this round's demo-rehearsal pass — do not treat the above as a substitute for one real hard-refresh, mic-permission-free play-through on a normal machine.

## F23 — Reverse-Beat in the Drehbuch

- Brief Section 1.6 (Vision beat) and this file updated to show a 10–15s role-swap using F18's existing Reverse Case mechanism: the AI opens the sales pitch unprompted, the sidebar visibly switches from scoring the user to annotating the AI's own techniques with citations where available. Narration line: "Now switch sides — the AI sells, and the sidebar names every technique it uses on you." Total Vision beat window (2:45–3:20) unchanged; the reverse beat is a sub-segment near the end of it, not an extension. Plan B (a declared recording) still applies to this beat like every other live segment.
- No code changes — F18 (Reverse Case) already ships and passed its own round. This is a documentation/direction change only.

## F24 — Antechamber (Firebase Hosting)

- Static landing page (`product/hosting/index.html`) deployed to Firebase Hosting at `https://the-stand-2026.web.app` (same GCP project as Cloud Run, `firebase.json` + `.firebaserc` added to the repo). Fires an invisible `fetch(..., {mode: "no-cors"})` warm-up ping at the Cloud Run URL on load; the "Take the stand" launch button stays disabled ("Waking the room…") until that resolves (or a 20s timeout elapses unconditionally, so a juror is never locked out). Launch is a real `window.location.href` navigation to the Cloud Run URL — never an iframe, since microphone permissions inside iframes are unreliable across browsers (notably Safari).
- Verified live against the real deployed page and the real Cloud Run URL (not a stub): the warm-up ping resolves and the button arms correctly.
- **Cold-test acceptance criterion (3x reproduced, service genuinely cold) is not yet run** — that's a human step, same category as the audio-playback gap above. `min-instances=0` should not be applied until that's done (see F21 note).
- Devpost URL should point at the antechamber; the direct Cloud Run URL stays in `README.md`'s try-it-out section per the brief.

# Round 5 (Upload Privacy, Upload UX)

> Source: manual test session on the deployed Cloud Run URL, 2026-08-30 evening. Findings: an uploaded case (including a real document, Lutfiya's dissertation) was visible to every visitor of the public URL, the upload focus field went unnoticed, and reverse mode silently disappeared on an older-schema uploaded case.

## Upload privacy (P1)

- Root cause: `/api/cases` and `GET /api/cases/{id}/briefing` served every uploaded case in Firestore to every caller — no ownership concept existed at all. Verified live from an outside network before the fix (the reported bug), and the exact same case was confirmed gone from a token-less client's view after.
- Fixed with an anonymous per-browser `owner_token` (frontend mints one via `crypto.randomUUID()`, stored in `localStorage` behind a try/catch so a blocked/private-mode store degrades to "works this tab, doesn't survive reload" instead of failing the upload). Sent as the `owner_token` form field on upload, the `X-Owner-Token` header on `/api/cases` and the briefing endpoint, and an `owner_token` field on the WebSocket `start` message. Server stores it on the case dict, never returns it in `_case_summary`, and a case with no `owner_token` (only possible pre-fix) is hidden from everyone rather than falling back to the old "visible to all" behavior.
- Starting a session on an uploaded `case_id` with a missing/wrong token gets the same generic "unknown case" WS error a made-up id would — no "found but not yours" signal to probe with.
- **Found and fixed a second, independent bug during verification**, not in the original report: `_to_firestore_case`/`_from_firestore_case` (the int-key stringify/destringify helpers from the Round 4 Firestore fix) only ever handled `witness.escalation`, never `reverse.escalation` — so every upload of a case whose template declares reverse mode (both current templates do) was *still* silently failing its Firestore write, the same failure signature as the bug F20 supposedly closed. Fixed by applying the same key-round-trip to `case["reverse"]["escalation"]`. Re-verified with a live upload + a direct Firestore read outside the app: document lands, `reverse.escalation` keys come back as ints.
- Pruned the reported case (`uploaded_cf7074ea6e`, `the-stand-2026` Firestore project — not the project this shell's default `gcloud` config pointed at, worth flagging for whoever runs `admin_prune_case.py` next) plus two other pre-fix uploads that had no `owner_token` and were therefore unreachable by anyone going forward anyway.
- **Demo implication:** an upload now lives in the browser tab that made it. Beat 5 (Lutfiya's fresh upload) must happen on the same machine/browser used for the recording — re-upload takes under a minute if the token is ever lost (private window, cleared site data), an accepted and honestly-stated trade-off, not a bug.

## Upload UX (P2)

- **Focus step (2a):** the focus field moved out from under the mode buttons — it's now its own step, shown only after a file is actually chosen ("Where should the committee press you?", Skip/Start buttons), matching what the tester actually looked for and when.
- **In-session refocus (2b):** a "Shift the pressure: ___" input next to the pressure dial, using the exact same `send_content` stage-direction mechanism the dial already uses — no new codepath into the live loop, no generation call during a live session. The active focus is also handed to the `RubricScorer`'s prompt as context (never as a citation source) and echoed back at the end in the debrief ("You asked to be pressed on: ..."). Honesty boundary: the stage direction is deliberately generic ("press specifically on X") — if the shifted focus falls outside what was cited at upload time, the witness presses on the topic in character and the UI never claims a document source for it.
- **Change focus between sessions (2c):** a "Change focus" link on an uploaded case's card re-attaches the file (document text was never stored server-side, so re-generation needs the file again) and regenerates the cited attack lines against the new focus, in place — same `case_id`, so it doesn't fork into a duplicate grid entry. Gated by the same `owner_token` check as everything else uploaded.
- **Reverse-toggle hardening (Fix 3):** an uploaded case built before reverse mode existed (old schema, no `reverse` block) now shows an honest "re-upload to unlock the other chair" label instead of the button silently not appearing. The toggle itself already renders on the selection card before any session starts (`card-actions`, no CSS gating it behind a breakpoint) — confirmed at both a desktop width and a narrow (390px) viewport.

## Regression

- `pytest -q` — 7/7 passing, unchanged from Round 4.
- Curated case list (6 cases), `/api/cases/{id}/briefing` for curated cases, and the F22 replay bundle were not touched by this round's diff and were re-checked after: identical behavior.
- Deployed to Cloud Run (`the-stand`, `the-stand-2026`, `europe-west1`) and re-verified live: a token-less client sees only the 6 curated cases and gets a clean WS error on a guessed uploaded id; an uploader's own upload (with a focus set) shows up with the focus in its briefing panel and the reverse toggle, both before the session starts.
