// The Stand — vanilla-JS room UI. Talks to /ws/{session_id} using the JSON
// message contract documented at the top of server/app.py. No framework:
// per the brief, this is exactly the amount of frontend a one-room product
// needs, not a "kein Framework-Ausbau" violation.

// ---------- DOM refs ----------
const caseSelectHeader = document.getElementById("caseSelectHeader");
const caseSelectView = document.getElementById("caseSelectView");
const roomHeader = document.getElementById("roomHeader");
const roomView = document.getElementById("roomView");
const debriefHeader = document.getElementById("debriefHeader");
const debriefView = document.getElementById("debriefView");

const caseGrid = document.getElementById("caseGrid");
const startBtn = document.getElementById("startBtn");
const statusMsg = document.getElementById("statusMsg");
const disclaimerEl = document.getElementById("disclaimer");

const roomContext = document.getElementById("roomContext");
const liveClock = document.getElementById("liveClock");
const avatarInitials = document.getElementById("avatarInitials");
const waveform = document.getElementById("waveform");
const transcriptView = document.getElementById("transcriptView");
const dialSegments = document.getElementById("dialSegments");
const endBtn = document.getElementById("endBtn");
const scoreLines = document.getElementById("scoreLines");
const scoreTotal = document.getElementById("scoreTotal");

const debriefContext = document.getElementById("debriefContext");
const debriefScore = document.getElementById("debriefScore");
const debriefHeadline = document.getElementById("debriefHeadline");
const debriefFocus = document.getElementById("debriefFocus");
const debriefMoments = document.getElementById("debriefMoments");
const debriefNextRep = document.getElementById("debriefNextRep");
const debriefCost = document.getElementById("debriefCost");
const againBtn = document.getElementById("againBtn");

// ---------- state ----------
let ws = null;
let audioCtx = null;
let micStream = null;
let micNode = null;
let playHead = 0;
let runningScore = 0;
let selectedCaseId = null;
let cases = [];
let sessionStartMs = 0;
let clockTimer = null;
let examinerAccum = "";
let witnessAccum = "";
let scoreEventLog = []; // {dxx, triggered, violation} — used to color debrief moments honestly

function showView(name) {
  const map = {
    caseSelect: [caseSelectHeader, caseSelectView],
    room: [roomHeader, roomView],
    debrief: [debriefHeader, debriefView],
  };
  for (const key of Object.keys(map)) {
    const on = key === name;
    map[key][0].hidden = !on;
    map[key][1].hidden = !on;
  }
}

function initialsFor(name) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join("");
}

// ---------- case select ----------
function renderCaseGrid() {
  caseGrid.innerHTML = "";
  for (const c of cases) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "case-card" + (c.case_id === selectedCaseId ? " selected" : "");
    card.setAttribute("aria-pressed", c.case_id === selectedCaseId ? "true" : "false");

    const kicker = c.case_number ? `Case No. ${c.case_number} · ${c.case_type}` : c.case_type;
    const langBadge = c.language
      ? `<div class="badge">${c.language.code.slice(0, 2).toUpperCase()} · ${c.language.name.toUpperCase()}</div>`
      : "";

    card.innerHTML = `
      <div class="topline"></div>
      <div class="body">
        <div class="kicker-row">
          <div class="kicker">${kicker}</div>
          ${langBadge}
        </div>
        <div class="title">${c.display_name}</div>
        <div class="desc">${c.card_summary}</div>
        <div class="witness-row">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="#8a8880" stroke-width="1.3"><circle cx="8" cy="5.5" r="2.6"></circle><path d="M2.8 13.5c0.8-2.7 2.9-4 5.2-4s4.4 1.3 5.2 4"></path></svg>
          <div class="who">${c.witness_name} <span class="disposition">· ${c.witness_short_role}${c.witness_disposition ? " · " + c.witness_disposition : ""}</span></div>
        </div>
      </div>
    `;
    card.onclick = () => {
      selectedCaseId = c.case_id;
      renderCaseGrid();
    };
    caseGrid.appendChild(card);
  }
}

async function loadCases() {
  const res = await fetch("/api/cases");
  const data = await res.json();
  disclaimerEl.textContent = data.disclaimer;
  cases = data.cases;
  selectedCaseId = cases[0]?.case_id || null;
  renderCaseGrid();
}

// ---------- audio plumbing (unchanged mechanism — human-verified voice path) ----------
function base64FromInt16(int16arr) {
  const bytes = new Uint8Array(int16arr.buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

function int16FromBase64(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Int16Array(bytes.buffer);
}

let micChunkCount = 0;
let micByteCount = 0;

async function startMic() {
  micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const source = audioCtx.createMediaStreamSource(micStream);
  // ScriptProcessorNode is deprecated but needs no separate worklet file —
  // the pragmatic choice for a hackathon-timeboxed capture path.
  micNode = audioCtx.createScriptProcessor(4096, 1, 1);
  const inputRate = audioCtx.sampleRate;
  const targetRate = 16000;
  micChunkCount = 0;
  micByteCount = 0;
  micNode.onaudioprocess = (e) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const input = e.inputBuffer.getChannelData(0);
    const ratio = inputRate / targetRate;
    const outLen = Math.floor(input.length / ratio);
    const out = new Int16Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const s = Math.max(-1, Math.min(1, input[Math.floor(i * ratio)]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    ws.send(JSON.stringify({ type: "audio", data: base64FromInt16(out) }));
    micChunkCount++;
    micByteCount += out.length * 2;
    if (micChunkCount === 1 || micChunkCount % 50 === 0) {
      console.log(`[mic] sent ${micChunkCount} chunks, ${micByteCount} bytes total (last chunk ${out.length * 2} bytes)`);
    }
  };
  source.connect(micNode);
  // ScriptProcessorNode only fires onaudioprocess while its output reaches
  // audioCtx.destination (directly or indirectly) — per the Web Audio API
  // spec, an unconnected downstream node silently stops processing entirely.
  // Route through a zero-gain node so we get a live graph without audible
  // mic monitoring/feedback.
  const silentSink = audioCtx.createGain();
  silentSink.gain.value = 0;
  micNode.connect(silentSink);
  silentSink.connect(audioCtx.destination);
}

function stopMic() {
  if (micNode) micNode.disconnect();
  if (micStream) micStream.getTracks().forEach((t) => t.stop());
  micNode = null;
  micStream = null;
}

function playPcm16(base64data, sampleRate = 24000) {
  const int16 = int16FromBase64(base64data);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 0x8000;
  const buffer = audioCtx.createBuffer(1, float32.length, sampleRate);
  buffer.copyToChannel(float32, 0);
  const src = audioCtx.createBufferSource();
  src.buffer = buffer;
  src.connect(audioCtx.destination);
  const now = audioCtx.currentTime;
  const startAt = Math.max(now, playHead);
  src.start(startAt);
  playHead = startAt + buffer.duration;
}

// ---------- room rendering ----------
function elapsedLabel() {
  const s = Math.max(0, Math.round((Date.now() - sessionStartMs) / 1000));
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

function tickClock() {
  liveClock.textContent = "LIVE · " + elapsedLabel();
}

function renderTranscript() {
  let html = "";
  if (examinerAccum) html += `<div class="examiner-line sans">YOU — "${examinerAccum}"</div>`;
  if (witnessAccum) html += `<div class="witness-line">"${witnessAccum}"</div>`;
  transcriptView.innerHTML = html + `<div class="interrupt-status sans" id="interruptStatus"></div>`;
}

function showInterrupted() {
  const el = document.getElementById("interruptStatus");
  if (el) el.textContent = "Interrupted — witness yields";
}

function addScoreLine(evt) {
  scoreEventLog.push({
    dxx: evt.dxx,
    triggered: !!evt.triggered,
    violation: !!evt.violation,
    note: evt.note,
    criterion: evt.criterion,
    ts: elapsedLabel(),
  });
  const div = document.createElement("div");
  div.className = "score-line " + (evt.violation ? "violation" : evt.triggered ? "triggered" : "");
  div.innerHTML = `
    <div class="row">
      <div class="note">${evt.note}</div>
      <div class="ts">${elapsedLabel()}</div>
    </div>
    <div class="cite">[${evt.dxx}] ${evt.criterion}</div>
  `;
  scoreLines.prepend(div);
  runningScore += evt.score_delta;
  scoreTotal.textContent = runningScore;
}

// ---------- debrief rendering ----------
// honest lookup: every moment's [D-xx] traces back to this session's own
// score events (note/criterion/timestamp/polarity), never fabricated.
function momentScoreEvent(dxx) {
  return [...scoreEventLog].reverse().find((e) => e.dxx === dxx);
}

function renderDebrief(d) {
  stopMic();
  clearInterval(clockTimer);
  showView("debrief");

  const selected = cases.find((c) => c.case_id === selectedCaseId);
  debriefContext.textContent = `Session closed · ${selected ? selected.display_name : ""} · ${elapsedLabel().replace(":", " min ")} s`;

  debriefScore.innerHTML = `${d.amta_score}<span class="scale"> / 10</span>`;
  debriefHeadline.textContent = d.headline;
  debriefFocus.textContent = "";

  debriefMoments.innerHTML = "";
  for (const m of d.moments || []) {
    const hit = momentScoreEvent(m.dxx);
    const polarity = hit ? (hit.violation ? "negative" : hit.triggered ? "positive" : "") : "";
    const labelText = hit && hit.ts ? `Moment · ${hit.ts} — ${hit.note}` : `Moment — [${m.dxx}]`;
    const citeText = hit && hit.criterion ? hit.criterion : m.dxx;
    const card = document.createElement("div");
    card.className = "moment-card" + (polarity ? " " + polarity : "");
    card.innerHTML = `
      <div class="row sans">
        <div class="label">${labelText}</div>
        <div class="cite">${citeText}</div>
      </div>
      <div class="excerpt">"${m.excerpt}"</div>
      <div class="why sans">${m.why_it_matters}</div>
    `;
    debriefMoments.appendChild(card);
  }

  debriefNextRep.textContent = d.practice_focus;

  const cost = d.cost;
  if (cost) {
    const tokensIn = cost.witness_tokens.prompt_tokens + cost.scorer_tokens.prompt_tokens + cost.debrief_tokens.prompt_tokens;
    const tokensOut = cost.witness_tokens.candidates_tokens + cost.scorer_tokens.candidates_tokens + cost.debrief_tokens.candidates_tokens;
    debriefCost.textContent = `Session cost: ${tokensIn.toLocaleString()} tokens in · ${tokensOut.toLocaleString()} out · est. $${cost.text_calls_usd_estimate.toFixed(2)} (text scoring calls, published rates)`;
  } else {
    debriefCost.textContent = "";
  }
}

function showStatus(text) {
  statusMsg.textContent = text;
}

// ---------- websocket ----------
// Connects the room WebSocket and resolves once it's open (or rejects with a
// connection-specific error) so callers can tell a failed connection apart
// from a denied microphone instead of lumping both into one vague message.
function connectWS() {
  return new Promise((resolve, reject) => {
    const sessionId = "sess_" + Math.random().toString(36).slice(2);
    // wss:// is required when the page itself is served over https — a
    // hardcoded ws:// silently fails as mixed content on the deployed
    // (https) Cloud Run URL even though it works on http://localhost.
    const wsProtocol = location.protocol === "https:" ? "wss:" : "ws:";
    let opened = false;
    try {
      ws = new WebSocket(`${wsProtocol}//${location.host}/ws/${sessionId}`);
    } catch (err) {
      reject(new Error("Couldn't open a connection: " + (err.message || err)));
      return;
    }
    ws.onopen = () => {
      opened = true;
      ws.send(
        JSON.stringify({
          type: "start",
          case_id: selectedCaseId,
          pressure_level: currentDialLevel,
        })
      );
      resolve();
    };
    ws.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);
      if (msg.type === "audio") {
        playPcm16(msg.data);
        waveform.classList.add("live");
      } else if (msg.type === "transcript") {
        if (msg.role === "examiner") {
          if (witnessAccum) {
            // a fresh examiner turn starting — clear the previous witness
            // line so the pair always reflects the current exchange.
            witnessAccum = "";
          }
          examinerAccum += msg.text;
        } else if (msg.role === "witness") {
          witnessAccum += msg.text;
        }
        renderTranscript();
      } else if (msg.type === "interrupted") {
        showInterrupted();
      } else if (msg.type === "score") {
        msg.events.forEach(addScoreLine);
      } else if (msg.type === "debrief") {
        renderDebrief(msg);
      } else if (msg.type === "error") {
        showStatus("Room error: " + msg.message);
      }
    };
    ws.onerror = () => {
      if (!opened) reject(new Error("Couldn't reach the room's connection."));
    };
    ws.onclose = () => {
      stopMic();
      if (!opened) reject(new Error("The room's connection closed before it opened."));
    };
  });
}

// ---------- dial ----------
let currentDialLevel = 1;

function renderDial() {
  [...dialSegments.children].forEach((btn) => {
    btn.classList.toggle("on", parseInt(btn.dataset.level, 10) <= currentDialLevel);
  });
}

dialSegments.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-level]");
  if (!btn) return;
  currentDialLevel = parseInt(btn.dataset.level, 10);
  renderDial();
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "dial", level: currentDialLevel }));
  }
});

// ---------- start / end ----------
async function beginSession() {
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  playHead = audioCtx.currentTime;
  showStatus("");
  try {
    await connectWS();
  } catch (err) {
    showStatus(
      "Reconnecting — the witness keeps his composure. (" + (err && err.message ? err.message : err) + ")"
    );
    return;
  }
  try {
    await startMic();
  } catch (err) {
    showStatus(
      "Couldn't reach your microphone — check the site's mic permission and try again. (" +
        (err && err.message ? err.message : err) +
        ")"
    );
    if (ws) ws.close();
    return;
  }

  const selected = cases.find((c) => c.case_id === selectedCaseId);
  const verb = selected && selected.case_type && selected.case_type.includes("Sparring") ? "discovery call with" : "cross-examination of";
  roomContext.textContent = selected ? `${selected.case_name} · ${verb} ${selected.witness_name}` : "";
  avatarInitials.textContent = selected ? initialsFor(selected.witness_name) : "--";

  examinerAccum = "";
  witnessAccum = "";
  scoreEventLog = [];
  runningScore = 0;
  scoreTotal.textContent = "0";
  scoreLines.innerHTML = "";
  transcriptView.innerHTML = "";
  currentDialLevel = 1;
  renderDial();

  sessionStartMs = Date.now();
  tickClock();
  clockTimer = setInterval(tickClock, 1000);

  showView("room");
}

startBtn.onclick = beginSession;

endBtn.onclick = () => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "end_session" }));
  }
  stopMic();
};

againBtn.onclick = () => {
  showView("caseSelect");
};

loadCases();
