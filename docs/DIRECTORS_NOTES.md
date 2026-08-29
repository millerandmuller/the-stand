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
