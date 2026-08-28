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

## Status

M0 walking skeleton: one witness, one escalation level (of three), in-memory
session state, no rubric scoring or debrief UI yet. See the project brief for
the full roadmap (M1–M3).
