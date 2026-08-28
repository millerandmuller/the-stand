// The Stand — vanilla-JS room UI. Talks to /ws/{session_id} using the JSON
// message contract documented at the top of server/app.py. No framework:
// per the brief, this is exactly the amount of frontend a one-room product
// needs, not a "kein Framework-Ausbau" violation.

const casePicker = document.getElementById("casePicker");
const startBtn = document.getElementById("startBtn");
const endBtn = document.getElementById("endBtn");
const dialControl = document.getElementById("dialControl");
const dialSlider = document.getElementById("dialSlider");
const transcriptView = document.getElementById("transcriptView");
const scoreLines = document.getElementById("scoreLines");
const scoreTotal = document.getElementById("scoreTotal");
const debriefView = document.getElementById("debrief");
const disclaimerEl = document.getElementById("disclaimer");

let ws = null;
let audioCtx = null;
let micStream = null;
let micNode = null;
let playHead = 0;
let runningScore = 0;
let selectedCaseId = null;

async function loadCases() {
  const res = await fetch("/api/cases");
  const data = await res.json();
  disclaimerEl.textContent = data.disclaimer;
  const select = document.createElement("select");
  for (const c of data.cases) {
    const opt = document.createElement("option");
    opt.value = c.case_id;
    opt.textContent = `${c.case_name} — witness: ${c.witness_name}`;
    select.appendChild(opt);
  }
  select.onchange = () => (selectedCaseId = select.value);
  selectedCaseId = data.cases[0]?.case_id || null;
  casePicker.appendChild(select);
}

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

async function startMic() {
  micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const source = audioCtx.createMediaStreamSource(micStream);
  // ScriptProcessorNode is deprecated but needs no separate worklet file —
  // the pragmatic choice for a hackathon-timeboxed capture path.
  micNode = audioCtx.createScriptProcessor(4096, 1, 1);
  const inputRate = audioCtx.sampleRate;
  const targetRate = 16000;
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
  };
  source.connect(micNode);
  micNode.connect(audioCtx.destination === null ? audioCtx.destination : audioCtx.createGain());
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

function addScoreLine(evt) {
  const div = document.createElement("div");
  div.className = "score-line " + (evt.violation ? "violation" : evt.triggered ? "triggered" : "");
  div.innerHTML = `<span class="note">${evt.note}</span><span class="cite">[${evt.dxx}] ${evt.criterion}</span>`;
  scoreLines.prepend(div);
  runningScore += evt.score_delta;
  scoreTotal.textContent = runningScore;
}

function renderDebrief(d) {
  document.getElementById("roomView").querySelectorAll("*:not(#debrief)").forEach((el) => {
    if (el.id !== "debrief") el.style.display = "none";
  });
  debriefView.style.display = "block";
  const moments = d.moments
    .map(
      (m) =>
        `<div class="moment"><em>${m.excerpt}</em><br>${m.why_it_matters} <span class="status">[${m.dxx}]</span></div>`
    )
    .join("");
  debriefView.innerHTML = `
    <h2>${d.headline}</h2>
    <div class="score">${d.amta_score}/10</div>
    ${moments}
    <p>${d.practice_focus}</p>
  `;
}

function connect() {
  const sessionId = "sess_" + Math.random().toString(36).slice(2);
  ws = new WebSocket(`ws://${location.host}/ws/${sessionId}`);
  ws.onopen = () => {
    ws.send(
      JSON.stringify({
        type: "start",
        case_id: selectedCaseId,
        pressure_level: parseInt(dialSlider.value, 10),
      })
    );
  };
  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);
    if (msg.type === "audio") {
      playPcm16(msg.data);
    } else if (msg.type === "transcript") {
      transcriptView.innerHTML = `<span class="${msg.role}">${msg.role === "examiner" ? "You: " : "Witness: "}${msg.text}</span>`;
    } else if (msg.type === "score") {
      msg.events.forEach(addScoreLine);
    } else if (msg.type === "debrief") {
      renderDebrief(msg);
    } else if (msg.type === "error") {
      console.error("server error:", msg.message);
    }
  };
  ws.onclose = () => stopMic();
}

startBtn.onclick = async () => {
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  playHead = audioCtx.currentTime;
  connect();
  await startMic();
  startBtn.style.display = "none";
  casePicker.style.display = "none";
  dialControl.style.display = "flex";
  endBtn.style.display = "inline-block";
};

endBtn.onclick = () => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "end_session" }));
  }
  stopMic();
  endBtn.style.display = "none";
  dialControl.style.display = "none";
};

dialSlider.oninput = () => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "dial", level: parseInt(dialSlider.value, 10) }));
  }
};

loadCases();
