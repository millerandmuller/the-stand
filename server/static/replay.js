// F22 — "Watch a replayed session". Self-contained: reads a pre-generated
// static bundle (server/static/replay/session_bundle.json + session_audio.wav,
// built once by eval/build_replay_bundle.py from a real `adk eval` run — see
// that script's docstring) and drives its own view. Deliberately does not
// touch app.js, the WS handshake, mic code, or run_live — this feature is
// pure playback of an already-recorded session.

(function () {
  const watchReplayBtn = document.getElementById("watchReplayBtn");
  const caseSelectHeader = document.getElementById("caseSelectHeader");
  const caseSelectView = document.getElementById("caseSelectView");
  const replayHeader = document.getElementById("replayHeader");
  const replayView = document.getElementById("replayView");
  const replayCloseBtn = document.getElementById("replayCloseBtn");
  const replayTranscript = document.getElementById("replayTranscript");
  const replayAvatarInitials = document.getElementById("replayAvatarInitials");
  const replayAudio = document.getElementById("replayAudio");
  const replayPlayBtn = document.getElementById("replayPlayBtn");
  const replayScrubber = document.getElementById("replayScrubber");
  const replayScrubberFill = document.getElementById("replayScrubberFill");
  const replayTime = document.getElementById("replayTime");
  const replayScoreLines = document.getElementById("replayScoreLines");
  const replayScoreTotal = document.getElementById("replayScoreTotal");
  const replayContext = document.getElementById("replayContext");

  if (!watchReplayBtn) return; // markup not present — nothing to wire up

  let bundle = null;
  let renderedTurnIdx = -1;
  let runningScore = 0;

  function fmtTime(seconds) {
    const s = Math.max(0, Math.round(seconds));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  function initialsFor(name) {
    return (name || "")
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0].toUpperCase())
      .join("");
  }

  async function loadBundle() {
    if (bundle) return bundle;
    const res = await fetch("/static/replay/session_bundle.json");
    bundle = await res.json();
    return bundle;
  }

  function resetPlayback() {
    renderedTurnIdx = -1;
    runningScore = 0;
    replayScoreLines.innerHTML = "";
    replayScoreTotal.textContent = "0";
    replayTranscript.innerHTML = "";
    replayScrubberFill.style.width = "0%";
  }

  function renderTurnUpTo(currentTime) {
    if (!bundle) return;
    while (
      renderedTurnIdx + 1 < bundle.turns.length &&
      bundle.turns[renderedTurnIdx + 1].offset_seconds <= currentTime
    ) {
      renderedTurnIdx += 1;
      const turn = bundle.turns[renderedTurnIdx];
      replayTranscript.innerHTML = `
        <div class="examiner-line sans">YOU — "${turn.question}"</div>
        <div class="witness-line">"${turn.answer}"</div>
        <div class="interrupt-status sans"></div>
      `;
      for (const evt of turn.events) {
        runningScore += evt.score_delta;
        const div = document.createElement("div");
        div.className = "score-line " + (evt.violation ? "violation" : evt.triggered ? "triggered" : "");
        div.innerHTML = `
          <div class="row">
            <div class="note">${evt.note}</div>
            <div class="ts">${fmtTime(turn.offset_seconds)}</div>
          </div>
          <div class="cite">[${evt.dxx}] ${evt.criterion}</div>
        `;
        replayScoreLines.prepend(div);
      }
      replayScoreTotal.textContent = runningScore;
    }
  }

  function openReplay() {
    caseSelectHeader.hidden = true;
    caseSelectView.hidden = true;
    replayHeader.hidden = false;
    replayView.hidden = false;
    resetPlayback();
    replayAudio.pause();
    replayAudio.currentTime = 0;
    replayPlayBtn.disabled = false;
    loadBundle()
      .then((b) => {
        replayContext.textContent = b.case_name;
        replayAvatarInitials.textContent = initialsFor(b.witness_name);
      })
      .catch(() => {
        replayPlayBtn.disabled = true;
        replayTranscript.innerHTML =
          '<div class="interrupt-status sans">Replay unavailable right now — could not load the recorded session.</div>';
      });
  }

  function closeReplay() {
    replayAudio.pause();
    replayHeader.hidden = true;
    replayView.hidden = true;
    caseSelectHeader.hidden = false;
    caseSelectView.hidden = false;
  }

  watchReplayBtn.addEventListener("click", openReplay);
  replayCloseBtn.addEventListener("click", closeReplay);

  replayPlayBtn.addEventListener("click", () => {
    if (replayAudio.paused) {
      replayAudio.play();
    } else {
      replayAudio.pause();
    }
  });

  replayAudio.addEventListener("play", () => {
    replayPlayBtn.innerHTML =
      '<svg width="12" height="14" viewBox="0 0 12 14" fill="currentColor"><rect x="0" y="0" width="4" height="14"></rect><rect x="8" y="0" width="4" height="14"></rect></svg>';
  });
  replayAudio.addEventListener("pause", () => {
    replayPlayBtn.innerHTML =
      '<svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor"><path d="M2 1.5v11l10-5.5z"></path></svg>';
  });

  replayAudio.addEventListener("timeupdate", () => {
    const dur = replayAudio.duration || (bundle && bundle.total_seconds) || 0;
    replayTime.textContent = `${fmtTime(replayAudio.currentTime)} / ${fmtTime(dur)}`;
    replayScrubberFill.style.width = dur ? `${(replayAudio.currentTime / dur) * 100}%` : "0%";
    renderTurnUpTo(replayAudio.currentTime);
  });

  replayAudio.addEventListener("ended", () => {
    if (bundle) renderTurnUpTo(bundle.total_seconds);
  });

  replayScrubber.addEventListener("click", (e) => {
    const rect = replayScrubber.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const dur = replayAudio.duration || (bundle && bundle.total_seconds) || 0;
    replayAudio.currentTime = ratio * dur;
    resetPlayback();
    renderTurnUpTo(replayAudio.currentTime);
  });
})();
