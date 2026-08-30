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
const uploadInput = document.getElementById("uploadInput");
const startBtn = document.getElementById("startBtn");
const statusMsg = document.getElementById("statusMsg");
const disclaimerEl = document.getElementById("disclaimer");

const roomContext = document.getElementById("roomContext");
const liveClock = document.getElementById("liveClock");
const avatarInitials = document.getElementById("avatarInitials");
const waveform = document.getElementById("waveform");
const transcriptView = document.getElementById("transcriptView");
const dialSegments = document.getElementById("dialSegments");
const refocusInput = document.getElementById("refocusInput");
const refocusBtn = document.getElementById("refocusBtn");
const refocusStatus = document.getElementById("refocusStatus");
const endBtn = document.getElementById("endBtn");
const scoreLines = document.getElementById("scoreLines");
const scoreTotal = document.getElementById("scoreTotal");
const sidebarLabel = document.querySelector(".sidebar .sidebar-header .label");
const sidebarScoreScale = document.querySelector(".sidebar .sidebar-header .score-scale");
const sidebarFooter = document.querySelector(".sidebar .footer");

const briefingToggle = document.getElementById("briefingToggle");
const briefingPanel = document.getElementById("briefingPanel");
const briefingScrim = document.getElementById("briefingScrim");
const briefingClose = document.getElementById("briefingClose");
const briefingBody = document.getElementById("briefingBody");

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
let uploadModes = []; // F16: which modes the server allows Bring-Your-Own-Case for
let pendingUploadMode = null;
let pendingUploadFocus = ""; // F19
let uploading = false;
let reverseSelected = false; // F18: applies to the currently selectedCaseId
let sessionReverse = false; // F18: locked in for the session in progress
let pendingRefocusCaseId = null; // F19 2c: "Change focus" targets this existing uploaded case

// ---------- P1: anonymous owner token ----------
// An uploaded case (F16) is only ever visible to the browser that uploaded
// it. This id is the only thing that proves ownership — server never sends
// it back (see server/app.py _case_summary). localStorage is wrapped in
// try/catch (private browsing, storage blocked, etc. all throw): on failure
// the token still exists for this page load (in-memory `ownerToken`), it
// just won't survive a reload — an accepted, documented trade-off (an
// upload effectively "lives in the browser tab" that made it).
let ownerToken = null;
function getOwnerToken() {
  if (ownerToken) return ownerToken;
  try {
    const stored = localStorage.getItem("the_stand_owner_token");
    if (stored) {
      ownerToken = stored;
      return ownerToken;
    }
  } catch (err) {
    // storage unavailable — fall through to an in-memory-only token
  }
  ownerToken = (crypto.randomUUID && crypto.randomUUID()) || `ot_${Math.random().toString(36).slice(2)}${Date.now()}`;
  try {
    localStorage.setItem("the_stand_owner_token", ownerToken);
  } catch (err) {
    // couldn't persist — this session still works, just won't survive reload
  }
  return ownerToken;
}

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
    let langBadge = "";
    if (c.language) {
      langBadge = `<div class="badge">${c.language.code.slice(0, 2).toUpperCase()} · ${c.language.name.toUpperCase()}</div>`;
    } else if (c.case_type && c.case_type.toLowerCase().includes("defense")) {
      langBadge = `<div class="badge">DEFENSE</div>`;
    } else if (c.uploaded) {
      langBadge = `<div class="badge">UPLOADED</div>`;
    }

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
        <div class="card-actions">
          <button type="button" class="briefing-link" data-case-id="${c.case_id}">Read the case file</button>
          <div class="card-actions-right">
            ${
              c.uploaded && c.upload_mode
                ? `<button type="button" class="briefing-link change-focus-link" data-case-id="${c.case_id}">Change focus</button>`
                : ""
            }
            ${
              c.reverse_available
                ? `<button type="button" class="reverse-toggle${c.case_id === selectedCaseId && reverseSelected ? " on" : ""}" data-case-id="${c.case_id}" title="${c.reverse_short_role || "Reverse mode"}">Take the other chair</button>`
                : c.uploaded
                  ? `<span class="reverse-toggle disabled" title="This case was uploaded before reverse mode — re-upload it to unlock the other chair">re-upload to unlock the other chair</span>`
                  : ""
            }
          </div>
        </div>
      </div>
    `;
    card.onclick = (e) => {
      if (e.target.closest(".briefing-link") || e.target.closest(".reverse-toggle")) return;
      if (selectedCaseId !== c.case_id) reverseSelected = false;
      selectedCaseId = c.case_id;
      renderCaseGrid();
    };
    card.querySelector(".briefing-link:not(.change-focus-link)").onclick = (e) => {
      e.stopPropagation();
      openBriefing(c.case_id, c.case_id === selectedCaseId && reverseSelected ? "reverse" : "examiner");
    };
    const changeFocusBtn = card.querySelector(".change-focus-link");
    if (changeFocusBtn) {
      changeFocusBtn.onclick = (e) => {
        e.stopPropagation();
        beginChangeFocus(c.case_id);
      };
    }
    const reverseBtn = card.querySelector(".reverse-toggle:not(.disabled)");
    if (reverseBtn) {
      reverseBtn.onclick = (e) => {
        e.stopPropagation();
        if (selectedCaseId !== c.case_id) {
          selectedCaseId = c.case_id;
          reverseSelected = true;
        } else {
          reverseSelected = !reverseSelected;
        }
        renderCaseGrid();
      };
    }
    caseGrid.appendChild(card);
  }
  if (uploadModes.length) renderUploadCard();
}

// ---------- F17: Case-Briefing-Panel ----------
async function openBriefing(caseId, role) {
  briefingPanel.classList.add("open");
  briefingScrim.hidden = false;
  briefingBody.innerHTML = `<div class="body-text muted">Loading…</div>`;
  try {
    const res = await fetch(`/api/cases/${encodeURIComponent(caseId)}/briefing?role=${role === "reverse" ? "reverse" : "examiner"}`, {
      headers: { "X-Owner-Token": getOwnerToken() },
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `couldn't load the briefing (${res.status})`);
    }
    const b = await res.json();
    const focusBlock = b.focus
      ? `<div class="bp-section"><div class="kicker">Your focus</div><div class="body-text">${b.focus}${b.focus_note ? `<br><span class="muted">${b.focus_note}</span>` : ""}</div></div>`
      : "";
    briefingBody.innerHTML = `
      <div class="bp-section"><div class="kicker">Case</div><div class="body-text">${b.case_name}</div><div class="body-text muted">${b.summary}</div></div>
      <div class="bp-section"><div class="kicker">Your role</div><div class="body-text">${b.user_role || "—"}</div></div>
      <div class="bp-section"><div class="kicker">${b.reverse ? "Who you're up against" : "Witness"}</div><div class="body-text">${b.counterpart_role}${b.counterpart_disposition ? ` <span class="muted">· ${b.counterpart_disposition}</span>` : ""}</div></div>
      <div class="bp-section"><div class="kicker">Affidavit</div><div class="body-text">${b.affidavit || "—"}</div></div>
      ${focusBlock}
    `;
  } catch (err) {
    briefingBody.innerHTML = `<div class="body-text muted">${err && err.message ? err.message : err}</div>`;
  }
}

function closeBriefing() {
  briefingPanel.classList.remove("open");
  briefingScrim.hidden = true;
}

briefingClose.onclick = closeBriefing;
// The scrim used to be a full-viewport click target (position:fixed,
// inset:0) that ate every click while the panel was open, including on
// "Take the stand" underneath it — a click there silently closed the panel
// instead of starting the session, needing a second click. Found during
// this round's demo rehearsal (directly threatens the Oh!-Moment beat).
// Fixed by making the scrim purely visual (pointer-events: none in
// index.html) and closing on a document-level "click outside the panel"
// listener instead, so a click on any real control (startBtn included)
// reaches it in the same click that dismisses the panel.
document.addEventListener("click", (e) => {
  if (!briefingPanel.classList.contains("open")) return;
  if (briefingPanel.contains(e.target) || e.target.closest("#briefingToggle") || e.target.closest(".briefing-link")) return;
  closeBriefing();
});
briefingToggle.onclick = () => {
  if (briefingPanel.classList.contains("open")) {
    closeBriefing();
  } else if (selectedCaseId) {
    openBriefing(selectedCaseId, sessionReverse ? "reverse" : "examiner");
  }
};

// ---------- F16: Bring Your Own Case ----------
let pendingUploadFile = null; // set once a file is chosen, cleared once the focus step resolves

function renderUploadCard() {
  const card = document.createElement("div");
  card.className = "case-card upload-card";
  const modeLabel = { defense: "Defense", sales: "Sales" };
  const buttons = uploadModes
    .map((m) => `<button type="button" class="upload-mode-btn" data-mode="${m}">Upload for ${modeLabel[m] || m}</button>`)
    .join("");
  card.innerHTML = `
    <div class="topline"></div>
    <div class="body">
      <div class="kicker-row"><div class="kicker">Your case</div></div>
      <div class="title">Bring your own</div>
      <div class="upload-hint">Upload a PDF or text document — your dissertation, a product briefing — and a case is generated from it in about a minute. Legal cross-exam stays fictional-only; upload works for Defense and Sales.</div>
      <div class="upload-modes">${buttons}</div>
      <div class="upload-progress" id="uploadProgress"></div>
    </div>
  `;
  card.querySelectorAll(".upload-mode-btn").forEach((btn) => {
    btn.onclick = () => {
      if (uploading) return;
      pendingUploadMode = btn.dataset.mode;
      pendingRefocusCaseId = null;
      uploadInput.value = "";
      uploadInput.click();
    };
  });
  caseGrid.appendChild(card);
}

// F19 2a: the focus question is its own visible step, presented only after
// a file is actually chosen — not a small optional field competing with the
// mode buttons for attention (that's what a real user didn't notice). Still
// entirely skippable.
function renderFocusStep(card, { prefill = "" } = {}) {
  card.querySelector(".body").innerHTML = `
    <div class="kicker-row"><div class="kicker">${pendingRefocusCaseId ? "Change focus" : "Your case"}</div></div>
    <div class="title">Where should the committee press you?</div>
    <div class="upload-hint">e.g. "Section 3, methodology" — optional, skip if you don't have one.</div>
    <div class="upload-focus-row"><input type="text" id="uploadFocusInput" placeholder="Optional focus area" autofocus></div>
    <div class="upload-modes">
      <button type="button" class="upload-mode-btn" id="uploadFocusSkip">Skip</button>
      <button type="button" class="upload-mode-btn" id="uploadFocusGo">${pendingRefocusCaseId ? "Re-aim the committee" : "Start reading"}</button>
    </div>
    <div class="upload-progress" id="uploadProgress"></div>
  `;
  const input = card.querySelector("#uploadFocusInput");
  input.value = prefill;
  input.focus();
  const go = (focus) => {
    pendingUploadFocus = focus;
    doUpload(card);
  };
  card.querySelector("#uploadFocusSkip").onclick = () => go("");
  card.querySelector("#uploadFocusGo").onclick = () => go(input.value.trim());
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") go(input.value.trim());
  });
}

uploadInput.addEventListener("change", () => {
  const file = uploadInput.files[0];
  if (!file || !pendingUploadMode) return;
  pendingUploadFile = file;
  const card = caseGrid.querySelector(".upload-card");
  if (card) {
    const refocusCase = pendingRefocusCaseId ? cases.find((c) => c.case_id === pendingRefocusCaseId) : null;
    renderFocusStep(card, { prefill: (refocusCase && refocusCase.focus) || "" });
  }
});

async function doUpload(card) {
  const file = pendingUploadFile;
  const mode = pendingUploadMode;
  const refocusCaseId = pendingRefocusCaseId;
  pendingUploadFile = null;
  pendingRefocusCaseId = null;
  if (!file || !mode) return;
  uploading = true;
  const progressEl = card.querySelector("#uploadProgress");
  if (progressEl) {
    progressEl.classList.remove("error");
    progressEl.textContent = refocusCaseId ? "Re-reading your case with the new focus…" : "Reading your case…";
  }
  try {
    const form = new FormData();
    form.append("mode", mode);
    form.append("file", file);
    form.append("owner_token", getOwnerToken());
    if (pendingUploadFocus) form.append("focus", pendingUploadFocus);
    if (refocusCaseId) form.append("case_id", refocusCaseId);
    const res = await fetch("/api/cases/upload", { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `upload failed (${res.status})`);
    }
    const data = await res.json();
    if (refocusCaseId) {
      cases = cases.map((c) => (c.case_id === data.case.case_id ? data.case : c));
    } else {
      cases = [...cases, data.case];
    }
    selectedCaseId = data.case.case_id;
    renderCaseGrid();
  } catch (err) {
    uploading = false;
    renderCaseGrid();
    const p = caseGrid.querySelector(".upload-card #uploadProgress");
    if (p) {
      p.classList.add("error");
      p.textContent = "Couldn't read your case — " + (err && err.message ? err.message : err);
    }
    return;
  }
  uploading = false;
}

// ---------- F19 2c: change focus on an existing uploaded case ----------
// Document text is never stored server-side, so "changing focus" means
// re-attaching the file — honest re-generation, not a hidden store.
function beginChangeFocus(caseId) {
  if (uploading) return;
  const c = cases.find((x) => x.case_id === caseId);
  if (!c || !c.uploaded || !c.upload_mode) return;
  pendingUploadMode = c.upload_mode;
  pendingRefocusCaseId = caseId;
  uploadInput.value = "";
  uploadInput.click();
}

async function loadCases() {
  const res = await fetch("/api/cases", { headers: { "X-Owner-Token": getOwnerToken() } });
  const data = await res.json();
  disclaimerEl.textContent = data.disclaimer;
  cases = data.cases;
  uploadModes = data.upload_modes || [];
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
  if (sessionReverse) {
    // F18: reverse mode never scores the user (server forces score_delta=0)
    // — keep the tile as a neutral placeholder instead of a running total.
    scoreTotal.textContent = "—";
  } else {
    runningScore += evt.score_delta;
    scoreTotal.textContent = runningScore;
  }
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
  debriefFocus.textContent = d.focus ? `You asked to be pressed on: ${d.focus}` : "";

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
          role: sessionReverse ? "reverse" : "examiner",
          owner_token: getOwnerToken(),
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
      } else if (msg.type === "focus") {
        // F19 2b: server confirms the mid-session shift actually applied.
        refocusStatus.textContent = `Pressing on: ${msg.focus}`;
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

// ---------- F19 2b: in-session refocus ----------
function sendRefocus() {
  const focus = refocusInput.value.trim();
  if (!focus || !ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "refocus", focus }));
  refocusStatus.textContent = "Shifting…";
  refocusInput.value = "";
}
refocusBtn.onclick = sendRefocus;
refocusInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendRefocus();
});

// ---------- start / end ----------
async function beginSession() {
  sessionReverse = reverseSelected;
  closeBriefing();
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
  const verb =
    (selected && selected.session_verb) ||
    (selected && selected.case_type && selected.case_type.includes("Sparring") ? "discovery call with" : "cross-examination of");
  const counterpartName = sessionReverse ? (selected && selected.reverse_short_role) || "reverse persona" : selected && selected.witness_name;
  roomContext.textContent = selected
    ? `${selected.case_name} · ${sessionReverse ? "reverse — " + verb + " " : verb + " "}${counterpartName}`
    : "";
  avatarInitials.textContent = selected ? initialsFor(counterpartName || "--") : "--";

  // F18: reverse mode annotates the AI's technique instead of scoring the
  // user — relabel the sidebar so it reads as a live technique log, not a
  // rubric ballot (server also forces every event's score_delta to 0).
  if (sidebarLabel) sidebarLabel.textContent = sessionReverse ? "Technique" : "Rubric";
  if (sidebarScoreScale) sidebarScoreScale.textContent = sessionReverse ? "live annotation" : "/ 10 · AMTA scale";
  if (sidebarFooter) {
    sidebarFooter.textContent = sessionReverse
      ? "Every line cites its source where one exists. Some techniques have none — marked uncited."
      : "Every line cites its source. Nothing is scored without one.";
  }
  scoreTotal.textContent = sessionReverse ? "—" : "0";

  examinerAccum = "";
  witnessAccum = "";
  scoreEventLog = [];
  runningScore = 0;
  scoreTotal.textContent = "0";
  scoreLines.innerHTML = "";
  transcriptView.innerHTML = "";
  currentDialLevel = 1;
  renderDial();
  refocusInput.value = "";
  refocusStatus.textContent = "";

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
