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
