# Plan B: recorded fallback for the demo video

Not a feature. This is a production note for whoever cuts the demo video.

The Hero Moment (the live interruption, F1/F10) is the one beat in the
drehbuch where a live take can fail for reasons that have nothing to do with
the product — mic glitch, network hiccup, a Live API latency spike during
the exact 60 seconds being recorded.

**The rule:**

- Prefer a real, live take of the session every time. Re-record live before
  reaching for the fallback.
- If a live take truly isn't landing after a couple of tries, it's fine to
  splice in a clip from an earlier real session that was recorded live
  (not scripted, not text-to-speech, not edited dialogue) — the room, the
  witness, the interruption all really happened, just not during this
  recording session.
- Any such clip must be declared as a recording in the video, on screen or
  in the voiceover (e.g. "recorded live earlier today") — it is never
  presented as happening in that moment. This follows the brief's honesty
  boundary (Ehrlichkeitsgrenze): the voice core is never mocked or
  scripted, only re-cut from a genuine prior take.
- This is a last-resort cut for the video only. It has no bearing on the
  live, deployed app — the Cloud Run URL always runs the real thing.
