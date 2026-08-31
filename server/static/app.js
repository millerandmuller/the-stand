// The Stand — vanilla-JS room UI. Talks to /ws/{session_id} using the JSON
// message contract documented at the top of server/app.py. No framework:
// per the brief, this is exactly the amount of frontend a one-room product
// needs, not a "kein Framework-Ausbau" violation.

// ---------- BUG 3 (round 3) diagnostics: ?diag=1 ----------
// Two prior rounds "fixed" End-session silence by reading this file and
// patching what looked wrong. Both times it read correctly and both times it
// still talked. This block measures instead: it counts AudioContexts, taps
// the real output graph with a pass-through AnalyserNode (never connected
// onward, so production audio routing is byte-for-byte unchanged), samples
// RMS every 20ms, and records every playPcm16/stop call with the state that
// was actually live at that moment. Off unless ?diag=1 is in the URL.
const DIAG = new URLSearchParams(location.search).get("diag") === "1";
const diag = {
  ctxCount: 0,
  ctxIds: [],
  plays: [],
  stops: [],
  clicks: [],
  rms: [], // [tMs, rms]
  wsAudio: [], // arrival time of every inbound audio frame
};
function diagNow() {
  return Math.round(performance.now());
}
function diagLog(bucket, entry) {
  if (!DIAG) return;
  entry.t = diagNow();
  diag[bucket].push(entry);
}
if (DIAG) {
  // Count every AudioContext ever constructed (hypothesis (a): a second
  // context playing while stop() hits nodes of the first).
  const NativeCtx = window.AudioContext || window.webkitAudioContext;
  const Wrapped = function (...args) {
    const ctx = new NativeCtx(...args);
    diag.ctxCount += 1;
    ctx.__standId = diag.ctxCount;
    diag.ctxIds.push({ id: diag.ctxCount, t: diagNow(), state: ctx.state });
    return ctx;
  };
  Wrapped.prototype = NativeCtx.prototype;
  window.AudioContext = Wrapped;
  window.webkitAudioContext = Wrapped;
  window.__standDiag = diag;
}
let diagAnalyser = null;
let diagRmsTimer = null;
function diagAttachMeter(ctx) {
  if (!DIAG || !ctx) return;
  diagAnalyser = ctx.createAnalyser();
  diagAnalyser.fftSize = 2048;
  const buf = new Float32Array(diagAnalyser.fftSize);
  clearInterval(diagRmsTimer);
  diagRmsTimer = setInterval(() => {
    diagAnalyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    diag.rms.push([diagNow(), Math.sqrt(sum / buf.length)]);
  }, 20);
}

// ---------- DOM refs ----------
const caseSelectHeader = document.getElementById("caseSelectHeader");
const caseSelectView = document.getElementById("caseSelectView");
const roomHeader = document.getElementById("roomHeader");
const roomView = document.getElementById("roomView");
const debriefHeader = document.getElementById("debriefHeader");
const debriefView = document.getElementById("debriefView");

const gridCourtroom = document.getElementById("gridCourtroom");
const gridBoardroom = document.getElementById("gridBoardroom");
const gridOwn = document.getElementById("gridOwn");
const uploadInput = document.getElementById("uploadInput");
const startBtn = document.getElementById("startBtn");
const startHint = document.getElementById("startHint");
const statusMsg = document.getElementById("statusMsg");
const disclaimerEl = document.getElementById("disclaimer");

const roomContext = document.getElementById("roomContext");
const liveClock = document.getElementById("liveClock");
const avatarInitials = document.getElementById("avatarInitials");
const waveform = document.getElementById("waveform");
const transcriptView = document.getElementById("transcriptView");
const dialSegments = document.getElementById("dialSegments");
const pressureCaption = document.getElementById("pressureCaption");
const refocusInput = document.getElementById("refocusInput");
const refocusBtn = document.getElementById("refocusBtn");
const refocusStatus = document.getElementById("refocusStatus");
const endBtn = document.getElementById("endBtn");
const backToCasesBtn = document.getElementById("backToCasesBtn");
const whisperToggle = document.getElementById("whisperToggle");
const whisperLine = document.getElementById("whisperLine");
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
const downloadTranscriptBtn = document.getElementById("downloadTranscriptBtn");
// FEATURE 8: second entry point for the same download, in the technique column
const downloadTranscriptSidebarBtn = document.getElementById("downloadTranscriptSidebarBtn");
const debriefScoreLines = document.getElementById("debriefScoreLines");
const debriefSidebarLabel = document.getElementById("debriefSidebarLabel");
const debriefSidebarScore = document.getElementById("debriefSidebarScore");
const debriefSidebarScale = document.getElementById("debriefSidebarScale");
const debriefSidebarFooterText = document.getElementById("debriefSidebarFooterText");

// ---------- state ----------
let ws = null;
let audioCtx = null;
let micStream = null;
let micNode = null;
let playHead = 0;
let runningScore = 0;
let selectedCaseId = null;
let cases = [];
let activeRoleWord = "witness"; // BUG 4: the room's word for "whoever's in the other chair", set per case
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

// ---------- BUG 3 / Back-fix: immediate audio silence ----------
// Every AudioBufferSourceNode currently scheduled to play is tracked here so
// "End session" (and Browser-Back out of the room) can stop them all in the
// same tick instead of letting whatever's already buffered play out.
let activeAudioSources = [];
let sessionEnded = false; // set the instant End/Back fires; blocks any audio still in flight from playing

function stopAllAudioImmediately() {
  diagLog("stops", {
    sources: activeAudioSources.length,
    ctxId: audioCtx ? audioCtx.__standId : null,
    ctxState: audioCtx ? audioCtx.state : null,
    ctxTime: audioCtx ? Number(audioCtx.currentTime.toFixed(3)) : null,
    playHead: Number(playHead.toFixed(3)),
    wasEnded: sessionEnded,
  });
  sessionEnded = true;
  for (const src of activeAudioSources) {
    try {
      src.stop();
    } catch (err) {
      // already stopped/ended — fine
    }
  }
  activeAudioSources = [];
  if (audioCtx) playHead = audioCtx.currentTime;
  waveform.classList.remove("live");
}

// ---------- FEATURE 1: last debrief, kept for "Download transcript" ----------
let lastDebrief = null;
let lastSessionMeta = null; // { caseName, role, startedAt }

// ---------- FEATURE 2: Whisper mode ----------
let whisperEnabled = false; // default OFF (leitplanke a) — resets every session

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

// ---------- case select: three sections (Courtroom / Boardroom / Your Own Case) ----------
// Routed by case_type rather than a hardcoded id list, so a case new to a
// section (e.g. a future Boardroom sparring case) is placed correctly
// without a client change. "Your Own Case" also always gets the upload card
// (see renderUploadCard) and any uploaded case, regardless of its type.
function gridForCase(c) {
  const type = (c.case_type || "").toLowerCase();
  if (c.uploaded || type.includes("defense")) return gridOwn;
  if (type.includes("sparring")) return gridBoardroom;
  return gridCourtroom; // Civil, Deposition, ... — the cross-examination cases
}

// BUG 4 (round 4): "witness" is the Courtroom's word, not a generic term for
// whoever's in the other chair — a Boardroom discovery call has a buyer, a
// dissertation defense has a committee. One word per case type, defined
// here (same branching as gridForCase, so a case is never routed to one
// section but labeled for another), used everywhere the room refers to the
// other party generically rather than by their specific case-file role.
function roleWordFor(c) {
  if (!c) return "witness"; // nothing selected yet — the Courtroom default
  const type = (c.case_type || "").toLowerCase();
  if (c.uploaded || type.includes("defense")) return "committee";
  if (type.includes("sparring")) return "buyer";
  return "witness";
}

function capitalize(word) {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

function renderCaseGrid() {
  gridCourtroom.innerHTML = "";
  gridBoardroom.innerHTML = "";
  gridOwn.innerHTML = "";
  for (const c of cases) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "case-card" + (c.case_id === selectedCaseId ? " selected" : "");
    card.setAttribute("aria-pressed", c.case_id === selectedCaseId ? "true" : "false");
    card.dataset.caseId = c.case_id; // FEATURE 6: lets the start block find its card

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
              // FEATURE 7: an upload you can't remove is the privacy bug that
              // already bit us once. Server enforces ownership; this is just
              // the door.
              c.uploaded
                ? `<button type="button" class="briefing-link delete-case-link" data-case-id="${c.case_id}">Delete</button>`
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
    const deleteBtn = card.querySelector(".delete-case-link");
    if (deleteBtn) {
      deleteBtn.onclick = (e) => {
        e.stopPropagation();
        deleteUploadedCase(c);
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
    gridForCase(c).appendChild(card);
  }
  if (uploadModes.length) renderUploadCard();
  placeStartBlock();
  // BUG 4: the pre-session hint word follows whichever case is selected
  // (defaults to "witness" — the Courtroom word — with nothing selected).
  if (startHint) {
    const selected = cases.find((c) => c.case_id === selectedCaseId);
    startHint.textContent = `Voice session · you can interrupt the ${roleWordFor(selected)} at any time`;
  }
}

// ---------- FEATURE 6: "Take the stand." follows the selected card ----------
// The button used to sit permanently below the whole three-section grid, so
// picking a card gave no hint about what to do next. Now it is moved into the
// grid, onto the row directly beneath the selected card and aligned to that
// card's column; with no selection it returns to its original place under the
// grid, exactly as before.
const startBlock = document.querySelector(".start-block");
const startBlockHome = startBlock ? startBlock.parentNode : null;
const startBlockAnchor = startBlock ? startBlock.nextSibling : null;

function gridColumnCount(grid) {
  const tpl = getComputedStyle(grid).gridTemplateColumns;
  return Math.max(1, tpl.split(" ").filter(Boolean).length);
}

function placeStartBlock() {
  if (!startBlock) return;
  document.querySelectorAll(".start-row").forEach((r) => r.remove());

  const card = selectedCaseId
    ? document.querySelector(`.case-card[data-case-id="${CSS.escape(selectedCaseId)}"]`)
    : null;
  if (!card) {
    // No selection (or the selected case is gone) — back to the page bottom.
    if (startBlockHome && startBlock.parentNode !== startBlockHome) {
      startBlockHome.insertBefore(startBlock, startBlockAnchor);
    }
    return;
  }

  const grid = card.parentNode;
  const cards = [...grid.children];
  const cols = gridColumnCount(grid);
  const index = cards.indexOf(card);
  const col = (index % cols) + 1;
  // Insert after the LAST card of this card's row, so the row above stays
  // complete and the button lands directly underneath.
  const rowEnd = Math.min(cards.length, (Math.floor(index / cols) + 1) * cols);

  const row = document.createElement("div");
  row.className = "start-row";
  row.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
  const cell = document.createElement("div");
  cell.style.gridColumn = String(col);
  cell.appendChild(startBlock);
  row.appendChild(cell);
  grid.insertBefore(row, cards[rowEnd] || null);
}

// Column count is breakpoint-dependent, so a resize can change which column
// the selected card sits in.
window.addEventListener("resize", () => {
  if (!caseSelectView.hidden) placeStartBlock();
});

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
    // BUG 4: the non-reverse kicker used to hardcode "Witness" even in the
    // Boardroom/Own-Case briefing panel.
    const roleWord = capitalize(roleWordFor(cases.find((c) => c.case_id === caseId)));
    const focusBlock = b.focus
      ? `<div class="bp-section"><div class="kicker">Your focus</div><div class="body-text">${b.focus}${b.focus_note ? `<br><span class="muted">${b.focus_note}</span>` : ""}</div></div>`
      : "";
    briefingBody.innerHTML = `
      <div class="bp-section"><div class="kicker">Case</div><div class="body-text">${b.case_name}</div><div class="body-text muted">${b.summary}</div></div>
      <div class="bp-section"><div class="kicker">Your role</div><div class="body-text">${b.user_role || "—"}</div></div>
      <div class="bp-section"><div class="kicker">${b.reverse ? "Who you're up against" : roleWord}</div><div class="body-text">${b.counterpart_role}${b.counterpart_disposition ? ` <span class="muted">· ${b.counterpart_disposition}</span>` : ""}</div></div>
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
  gridOwn.appendChild(card);
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
  const card = gridOwn.querySelector(".upload-card");
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
      const err = new Error(body.detail || `upload failed (${res.status})`);
      // BUG 1: 422 is the "this document has no readable text" refusal. Its
      // detail is already a complete, plain sentence meant for the user —
      // show it verbatim instead of wrapping it in another apology.
      err.verbatim = res.status === 422;
      throw err;
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
    const p = gridOwn.querySelector(".upload-card #uploadProgress");
    if (p) {
      p.classList.add("error");
      const msg = err && err.message ? err.message : String(err);
      p.textContent = err && err.verbatim ? msg : "Couldn't read your case — " + msg;
    }
    return;
  }
  uploading = false;
}

// ---------- FEATURE 7: delete an uploaded case ----------
async function deleteUploadedCase(c) {
  if (uploading) return;
  if (!window.confirm(`Delete "${c.display_name}"? This removes the case for good.`)) return;
  try {
    const res = await fetch(`/api/cases/${encodeURIComponent(c.case_id)}`, {
      method: "DELETE",
      headers: { "X-Owner-Token": getOwnerToken() },
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `delete failed (${res.status})`);
    }
  } catch (err) {
    showStatus("Couldn't delete that case — " + (err && err.message ? err.message : err));
    return;
  }
  cases = cases.filter((x) => x.case_id !== c.case_id);
  if (selectedCaseId === c.case_id) {
    selectedCaseId = null;
    reverseSelected = false;
  }
  showStatus("");
  renderCaseGrid();
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
  // BUG 3: audio frames already in flight when "End session" fires must
  // never start playing — sessionEnded flips the instant that happens.
  if (sessionEnded) {
    diagLog("plays", { blocked: true, sessionEnded: true });
    return;
  }
  const int16 = int16FromBase64(base64data);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 0x8000;
  const buffer = audioCtx.createBuffer(1, float32.length, sampleRate);
  buffer.copyToChannel(float32, 0);
  const src = audioCtx.createBufferSource();
  src.buffer = buffer;
  src.connect(audioCtx.destination);
  if (diagAnalyser) src.connect(diagAnalyser); // pure tap, no onward output
  const now = audioCtx.currentTime;
  const startAt = Math.max(now, playHead);
  src.start(startAt);
  playHead = startAt + buffer.duration;
  activeAudioSources.push(src);
  diagLog("plays", {
    blocked: false,
    ctxId: audioCtx.__standId,
    ctxState: audioCtx.state,
    now: Number(now.toFixed(3)),
    startAt: Number(startAt.toFixed(3)),
    dur: Number(buffer.duration.toFixed(3)),
    queueAhead: Number((startAt - now).toFixed(3)),
    sources: activeAudioSources.length,
  });
  src.onended = () => {
    activeAudioSources = activeAudioSources.filter((s) => s !== src);
  };
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
  // BUG 4: the transcript now scrolls inside its own box instead of growing
  // the room — so the newest words have to be scrolled to, or a long turn
  // would appear frozen on its first line.
  transcriptView.scrollTop = transcriptView.scrollHeight;
}

function showInterrupted() {
  const el = document.getElementById("interruptStatus");
  if (el) el.textContent = `Interrupted — ${activeRoleWord} yields`;
}

// ---------- FEATURE 2: Whisper mode ("counsel's whisper") ----------
// BUG 1 (round 4): whisperLine is always in the DOM flow now (no more
// `hidden` toggling display:none) so its reserved slot never collapses —
// see the .whisper-line CSS. Showing/clearing just changes its text/class.
let whisperPendingTimer = null;
// BUG 3 (round 4): comfortably above the ~2-3s scorer latency the brief
// documents (rubric_scorer/scorer.py's module docstring) — a real measured
// number wasn't feasible this round without a live mic session (see
// DIRECTORS_NOTES, logged as a GAP rather than guessed).
const WHISPER_PENDING_TIMEOUT_MS = 6000;

function showWhisper(text) {
  if (!whisperLine) return;
  clearTimeout(whisperPendingTimer);
  whisperLine.classList.remove("pending");
  whisperLine.textContent = text || "";
}

function clearWhisper() {
  if (!whisperLine) return;
  clearTimeout(whisperPendingTimer);
  whisperLine.classList.remove("pending");
  whisperLine.textContent = "";
}

// BUG 3: a quiet placeholder in the reserved whisper slot while this
// exchange's scoring call is presumably in flight. Auto-clears on a timeout
// because a turn with nothing rubric-worthy never sends a whisper message
// at all — there's no positive "done, nothing to say" signal to wait for.
function armWhisperPending() {
  if (!whisperLine || !whisperEnabled) return;
  clearTimeout(whisperPendingTimer);
  whisperLine.classList.add("pending");
  whisperPendingTimer = setTimeout(() => {
    whisperLine.classList.remove("pending");
  }, WHISPER_PENDING_TIMEOUT_MS);
}

function renderWhisperToggle() {
  if (!whisperToggle) return;
  whisperToggle.classList.toggle("on", whisperEnabled);
  whisperToggle.setAttribute("aria-pressed", whisperEnabled ? "true" : "false");
}

if (whisperToggle) {
  whisperToggle.onclick = () => {
    whisperEnabled = !whisperEnabled;
    renderWhisperToggle();
    if (!whisperEnabled) clearWhisper();
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "whisper", enabled: whisperEnabled }));
    }
  };
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
  lastDebrief = d; // FEATURE 1: kept for "Download transcript"

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

  // FEATURE 8: carry the technique/rubric column into the debrief so the
  // annotations stay readable and the transcript download sits where the
  // user looked for it. Same lines, same labels, same file — no re-render
  // of the score events, just the nodes the room already built.
  if (debriefScoreLines) {
    debriefScoreLines.innerHTML = scoreLines.innerHTML;
    debriefSidebarLabel.textContent = sidebarLabel ? sidebarLabel.textContent : "Rubric";
    debriefSidebarScore.textContent = scoreTotal.textContent;
    debriefSidebarScale.textContent = sidebarScoreScale ? sidebarScoreScale.textContent : "/ 10 · AMTA scale";
    debriefSidebarFooterText.textContent = sidebarFooter ? sidebarFooter.textContent : "";
  }
}

function showStatus(text) {
  statusMsg.textContent = text;
}

// ---------- FEATURE 1: Download transcript ----------
// Client-side Blob download — no new endpoint, everything here already lives
// in the browser (server sent the deduplicated transcript_lines in the
// "debrief" message, see server/app.py).
function buildTranscriptFile(d, meta) {
  const lines = [];
  lines.push(`The Stand — session transcript`);
  lines.push(`Case: ${(d && d.case_name) || (meta && meta.caseName) || "—"}`);
  lines.push(`Date: ${new Date().toISOString()}`);
  lines.push(`Role: ${(d && d.role) || (meta && meta.role) || "normal"}`);
  lines.push("");
  lines.push("## Transcript");
  const transcriptLines = (d && d.transcript_lines) || [];
  if (transcriptLines.length) {
    lines.push(...transcriptLines);
  } else {
    lines.push("(no exchanges recorded)");
  }
  lines.push("");
  lines.push("## Score / technique events");
  if (scoreEventLog.length) {
    for (const e of scoreEventLog) {
      const kind = e.violation ? "violation" : e.triggered ? "triggered" : "note";
      lines.push(`- [${e.ts}] [${e.dxx}] ${e.criterion} (${kind}) — ${e.note}`);
    }
  } else {
    lines.push("(none)");
  }
  lines.push("");
  lines.push("## Debrief");
  if (d) {
    lines.push(`AMTA score: ${d.amta_score} / 10`);
    lines.push(d.headline || "");
    if (d.focus) lines.push(`Requested focus: ${d.focus}`);
    for (const m of d.moments || []) {
      lines.push("");
      lines.push(`Moment [${m.dxx}]: "${m.excerpt}"`);
      lines.push(`Why it matters: ${m.why_it_matters}`);
    }
    lines.push("");
    lines.push(`Practice focus: ${d.practice_focus || ""}`);
  } else {
    lines.push("(session not yet closed)");
  }
  return lines.join("\n");
}

function downloadTranscript() {
  if (!lastDebrief) return;
  const text = buildTranscriptFile(lastDebrief, lastSessionMeta);
  const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const caseSlug = ((lastDebrief.case_name || "session") + "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
  a.href = url;
  a.download = `the-stand-${caseSlug || "session"}-transcript.md`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

if (downloadTranscriptBtn) {
  downloadTranscriptBtn.onclick = downloadTranscript;
}
if (downloadTranscriptSidebarBtn) {
  downloadTranscriptSidebarBtn.onclick = downloadTranscript;
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
        diagLog("wsAudio", { bytes: msg.data.length, sessionEnded });
        playPcm16(msg.data);
        waveform.classList.add("live");
      } else if (msg.type === "transcript") {
        // BUG 2: msg.replace means the server reconciled a corrected re-send
        // of the whole turn — swap the text instead of appending it, which is
        // what printed every corrected turn twice.
        if (msg.role === "examiner") {
          if (witnessAccum) {
            // a fresh examiner turn starting — clear the previous witness
            // line so the pair always reflects the current exchange.
            witnessAccum = "";
          }
          clearWhisper(); // FEATURE 2: a whisper is only relevant to the exchange it was suggested for
          examinerAccum = msg.replace ? msg.text : examinerAccum + msg.text;
        } else if (msg.role === "witness") {
          // BUG 3: first content of a fresh witness turn — the exchange is
          // now underway, so a whisper for it may arrive shortly.
          if (!witnessAccum) armWhisperPending();
          witnessAccum = msg.replace ? msg.text : witnessAccum + msg.text;
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
      } else if (msg.type === "whisper") {
        showWhisper(msg.text);
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
  // BUG 4: computed once, up front, so the room's copy (pressure caption,
  // interrupt status, reconnect message) all agree on the same word for the
  // whole session — set before the first place that can need it.
  activeRoleWord = roleWordFor(cases.find((c) => c.case_id === selectedCaseId));
  closeBriefing();
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  playHead = audioCtx.currentTime;
  diagAttachMeter(audioCtx);
  showStatus("");
  try {
    await connectWS();
  } catch (err) {
    showStatus(
      "Reconnecting — the " + activeRoleWord + " keeps their composure. (" + (err && err.message ? err.message : err) + ")"
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
  // BUG 4: "Witness pressure" only in the Courtroom — Boardroom gets
  // "Buyer pressure", Defense/BYOC gets "Committee pressure".
  if (pressureCaption) pressureCaption.textContent = `${capitalize(activeRoleWord)} pressure`;

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

  // BUG 3 / Back-fix: fresh session, nothing ended yet, no audio queued.
  sessionEnded = false;
  activeAudioSources = [];
  // FEATURE 2: whisper always starts OFF (leitplanke a) — re-arm the toggle
  // with the server each session instead of carrying a stale preference.
  whisperEnabled = false;
  renderWhisperToggle();
  clearWhisper();
  lastDebrief = null;
  lastSessionMeta = { caseName: selected ? selected.case_name : "", role: sessionReverse ? "reverse" : "normal" };

  sessionStartMs = Date.now();
  tickClock();
  clockTimer = setInterval(tickClock, 1000);

  showView("room");
  pushAppState(); // Back-fix: a hardware/browser Back now lands on our own handler, never off the SPA
}

startBtn.onclick = beginSession;

function endSessionNow() {
  diagLog("clicks", { handler: "endSessionNow" });
  stopAllAudioImmediately(); // BUG 3: silence in the same tick as the click, before the network round-trip
  if (ws && ws.readyState === WebSocket.OPEN) {
    try {
      ws.send(JSON.stringify({ type: "end_session" }));
    } catch (err) {
      // socket already going away — fine, we've already gone silent
    }
  }
  stopMic();
}

endBtn.onclick = endSessionNow;

// ---------- Back-fix: History API traps Browser-Back inside the SPA ----------
// A bare click on a case card doesn't change the URL, so the only history
// entry pushed is the one at room-entry (pushAppState() in beginSession).
// popstate always re-pushes after handling, keeping the stack depth roughly
// constant — Back never falls through to a real previous page / white screen.
function pushAppState() {
  try {
    history.pushState({ theStand: true }, "", location.href);
  } catch (err) {
    // pushState unavailable (rare) — Back-fix degrades to default browser Back
  }
}

function leaveRoomToCaseSelect() {
  // Idempotent-safe whether called from a live room or an already-finished
  // debrief: stopAllAudioImmediately/stopMic/ws.close are all no-ops on an
  // already-silent/closed session.
  endSessionNow();
  if (ws) {
    try {
      ws.close();
    } catch (err) {
      // already closing — fine
    }
  }
  clearInterval(clockTimer);
  showStatus("");
  showView("caseSelect");
}

window.addEventListener("popstate", () => {
  if (briefingPanel.classList.contains("open")) {
    closeBriefing();
    pushAppState();
    return;
  }
  if (!roomView.hidden) {
    leaveRoomToCaseSelect();
    pushAppState();
    return;
  }
  if (!debriefView.hidden) {
    showView("caseSelect");
    pushAppState();
    return;
  }
  // Already on case-select — re-arm so the next Back stays trapped here too.
  pushAppState();
});

if (backToCasesBtn) {
  // Visible "← All cases" affordance shares the exact popstate codepath —
  // a real Back navigation and a click on this button behave identically.
  backToCasesBtn.onclick = () => history.back();
}

againBtn.onclick = () => {
  showView("caseSelect");
  pushAppState();
};

// Back-fix: an initial state so the very first Back press (before any
// session has ever started) is also trapped inside the SPA, not the browser
// history that predates this page load.
pushAppState();
loadCases();
