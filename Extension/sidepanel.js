/**
 * sidepanel.js
 *
 * Three capture modes:
 *  1. GOOGLE_MEET  – reads captions injected by meet-captions.js via background port
 *                   (all participants, with speaker names)
 *  2. TAB_AUDIO   – chrome.tabCapture + Web Speech Recognition
 *                   (all participants, no speaker names)
 *  3. MICROPHONE  – getUserMedia + Web Speech Recognition
 *                   (your voice only)
 */

// ── Constants ─────────────────────────────────────────────────────────────────
const MODE = { NONE: "none", GOOGLE_MEET: "meet", TAB: "tab", MIC: "mic" };
const PROCESSING_ENDPOINT_DEFAULT =
  (window.EXTENSION_CONFIG && window.EXTENSION_CONFIG.processingEndpoint) ||
  "http://127.0.0.1:8000/api/meetings/process";

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  mode: MODE.NONE,
  isRecording: false,
  isProcessing: false,
  meetingId: null,
  transcriptSnapshot: "",
  startedAt: null,
  entries: [],
  // Audio / speech
  mediaRecorder: null,
  audioChunks: [],
  audioBlob: null,
  recognition: null,
  interimText: "",
  // Background port (for Google Meet captions relay)
  bgPort: null,
  // Auto-detected participants and last processed meeting
  participants: [],
  meetingId: null,
  minutesText: "",
};

// ── DOM refs ──────────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const startButton       = $("startButton");
const stopButton        = $("stopButton");
const statusBadge       = $("statusBadge");
const transcriptEl      = $("transcript");
const processStatus     = $("processStatus");
const processButton     = $("processButton");
const extractTab        = $("extractTab");
const analyzeTab        = $("analyzeTab");
const factualPanel      = $("factualPanel");
const analysisPanel     = $("analysisPanel");
const analyzeMoreButton = $("analyzeMoreButton");
const resultsMeetingTitle = $("resultsMeetingTitle");
const graphCard         = $("graphCard");
const factualTasksList  = $("factualTasksList");
const analysisTasksList = $("analysisTasksList");
const factualResults    = $("factualResults");
const factualSummary    = $("factualSummary");
const factualEmptyState = $("factualEmptyState");
const analysisResults   = $("analysisResults");
const analysisSummary   = $("analysisSummary");
const analysisEmptyState = $("analysisEmptyState");
const analysisStatus    = $("analysisStatus");
const captureBanner     = $("captureBanner");
const captureBannerTitle  = $("captureBannerTitle");
const captureBannerDesc   = $("captureBannerDesc");
const captureBannerIcon   = $("captureBannerIcon");
const meetCaptionNotice = $("meetCaptionNotice");

// ── Helpers ───────────────────────────────────────────────────────────────────
function escapeHtml(v) {
  if (!v) return "";
  return String(v).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c])
  );
}

function formatTime(ms) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60).toString().padStart(2, "0");
  const r = (s % 60).toString().padStart(2, "0");
  return `${m}:${r}`;
}

function getISTTimestamp() {
  return new Date().toLocaleTimeString("en-IN", {
    timeZone: "Asia/Kolkata",
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function elapsed() {
  return state.startedAt ? Date.now() - state.startedAt : 0;
}

// ── Transcript ────────────────────────────────────────────────────────────────
function addEntry(text, timestamp) {
  const cleaned = (text || "").trim();
  if (!cleaned) return;
  state.entries.push({
    timestamp: timestamp || getISTTimestamp(),
    text: cleaned,
    source: state.mode,
  });
  state.interimText = "";
  renderTranscript();
}

// Auto-fill the Participants field with speaker names captured from Meet
const PARTICIPANT_UI_TEXT = /\b(pin|unpin|mute|unmute|reframe|remove|spotlight|present|presenting|turn (on|off)|more options|raise hand|captions?|camera|microphone|main screen|someone else|can't|cannot|you can|options|menu|settings|meeting|everyone|annotations?|can see|see your|your (screen|video|audio)|recording|breakout|polls?|whiteboard|host controls?)\b/i;
function addParticipant(name) {
  const n = (name || "").trim();
  if (!n || /^you$/i.test(n)) return;
  if (n.split(/\s+/).length > 5 || PARTICIPANT_UI_TEXT.test(n)) return; // Meet button text, not a person
  if (state.participants.some((x) => x.toLowerCase() === n.toLowerCase())) return;
  state.participants.push(n);
  const el = $("participantsDetected");
  if (el) el.textContent = `Participants: ${state.participants.join(", ")}`;
}

function updateOrAddTurnEntry(turnId, speaker, fullText, turnTimestamp) {
  const cleaned = (fullText || "").trim();
  if (!cleaned) return;

  // 1. If this turnId already exists, update in-place as speech grows
  if (turnId) {
    const existingIndex = state.entries.findIndex((e) => e.turnId === turnId);
    if (existingIndex !== -1) {
      if (state.entries[existingIndex].text !== cleaned) {
        state.entries[existingIndex].text = cleaned;
        renderTranscript();
      }
      return;
    }
  }

  // 2. If no turnId was supplied, check if the last entry is from the same speaker and simply extended
  if (!turnId) {
    const last = state.entries[state.entries.length - 1];
    if (last && last.speaker === speaker && (cleaned.startsWith(last.text) || cleaned.includes(last.text.slice(-20)))) {
      last.text = cleaned;
      renderTranscript();
      return;
    }
  }

  // 3. Brand new speaker turn!
  addParticipant(speaker);
  state.entries.push({
    turnId: turnId || `turn-${Date.now()}-${Math.random().toString(36).substr(2, 4)}`,
    speaker: speaker || "",
    timestamp: turnTimestamp || getISTTimestamp(),
    text: cleaned,
    source: state.mode,
  });
  state.interimText = "";
  renderTranscript();
}

function renderTranscript() {
  if (state.transcriptSnapshot && state.transcriptSnapshot !== transcriptText()) {
    state.meetingId = null;
    state.transcriptSnapshot = "";
    factualResults.style.display = "none";
    analysisResults.style.display = "none";
    factualEmptyState.hidden = false;
    factualEmptyState.textContent = "Transcript changed. Extract again to refresh these results.";
    analysisEmptyState.hidden = false;
    analysisEmptyState.textContent = "Analyze more will extract the updated transcript before running analysis.";
    processStatus.textContent = "";
    analysisStatus.textContent = "";
  }

  transcriptEl.className = state.entries.length
    ? "transcript"
    : "transcript empty-state";

  transcriptEl.innerHTML = state.entries.length
    ? state.entries
        .map(
          (e) =>
            `<article class="entry"><time>${escapeHtml(e.timestamp)}</time>` +
            `<p>${escapeHtml(e.text)}</p></article>`
        )
        .join("")
    : "<p>Start a capture mode or add text to see the transcript here.</p>";

  $("transcriptCount").textContent = `${state.entries.length} ${
    state.entries.length === 1 ? "entry" : "entries"
  }`;

  if (state.interimText) {
    transcriptEl.insertAdjacentHTML(
      "beforeend",
      `<article class="entry interim"><time>Listening…</time><p>${escapeHtml(
        state.interimText
      )}</p></article>`
    );
  }
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

// ── Banner ────────────────────────────────────────────────────────────────────
function setBanner(type, title, desc) {
  captureBanner.className = `capture-banner banner-${type}`;
  captureBannerIcon.textContent =
    type === "recording" ? "🔴" :
    type === "success"   ? "✓"  :
    type === "error"     ? "⚠"  : "🎙";
  captureBannerTitle.textContent = title;
  captureBannerDesc.textContent  = desc;
}

// ── Mode selection UI ─────────────────────────────────────────────────────────
function setModeButton(mode) {
  ["modeGoogleMeet", "modeTabAudio", "modeMic"].forEach((id) =>
    $(id).classList.remove("active")
  );
  if (mode === MODE.GOOGLE_MEET) $("modeGoogleMeet").classList.add("active");
  if (mode === MODE.TAB)         $("modeTabAudio").classList.add("active");
  if (mode === MODE.MIC)         $("modeMic").classList.add("active");

  meetCaptionNotice.style.display = mode === MODE.GOOGLE_MEET ? "block" : "none";
  startButton.disabled = false;
}

$("modeGoogleMeet").addEventListener("click", () => {
  if (state.isRecording) return;
  state.mode = MODE.GOOGLE_MEET;
  setModeButton(MODE.GOOGLE_MEET);
  setBanner("idle", "Google Meet mode selected",
    "Enable captions in Google Meet (⋮ → Turn on captions), then click Start recording.");
  $("recordingNotice").textContent =
    "Open Google Meet, enable captions (Ctrl+Shift+C), then click Start recording.";
});

$("modeTabAudio").addEventListener("click", () => {
  if (state.isRecording) return;
  state.mode = MODE.TAB;
  setModeButton(MODE.TAB);
  setBanner("idle", "Tab Audio mode selected",
    "Captures all voices from the active browser tab. Speaker names are NOT available.");
  $("recordingNotice").textContent =
    "Captures all audio from the active tab (all voices, no speaker names).";
});

$("modeMic").addEventListener("click", () => {
  if (state.isRecording) return;
  state.mode = MODE.MIC;
  setModeButton(MODE.MIC);
  setBanner("idle", "Microphone mode selected",
    "Captures only YOUR voice. Use Google Meet Captions for all participants.");
  $("recordingNotice").textContent =
    "Records YOUR microphone only. Use Google Meet Captions to capture all participants.";
});

// ── Recording UI ──────────────────────────────────────────────────────────────
function setRecordingUi(recording) {
  state.isRecording = recording;
  startButton.disabled = recording;
  stopButton.disabled  = !recording;
  statusBadge.textContent = recording ? "Recording" : "Ready";
  statusBadge.className   = `status-badge ${recording ? "recording" : "idle"}`;
  // Disable mode buttons while recording
  ["modeGoogleMeet", "modeTabAudio", "modeMic"].forEach(
    (id) => ($(id).disabled = recording)
  );
}

// ── Background Port (Meet captions relay) ─────────────────────────────────────
function connectToBackground() {
  try {
    state.bgPort = chrome.runtime.connect({ name: "sidepanel" });
    state.bgPort.onMessage.addListener(handleBackgroundMessage);
    state.bgPort.onDisconnect.addListener(() => {
      state.bgPort = null;
      // Show reconnecting indicator only while recording
      if (state.isRecording && state.mode === MODE.GOOGLE_MEET) {
        setBanner("error", "Connection to background lost – reconnecting…",
          "Captions will resume automatically. If they don't, reload the extension.");
      }
      // Reconnect immediately, then with backoff
      setTimeout(connectToBackground, 500);
    });
  } catch (e) {
    setTimeout(connectToBackground, 2000);
  }
}

function handleBackgroundMessage(msg) {
  if (!msg || !msg.type) return;

  if (msg.type === "meet-participants" && Array.isArray(msg.names)) {
    msg.names.forEach(addParticipant);
    return;
  }

  if (msg.type === "meet-caption" && state.isRecording && state.mode === MODE.GOOGLE_MEET) {
    updateOrAddTurnEntry(msg.turnId, msg.speaker, msg.text, msg.timestamp);
    // Restore recording banner if we had a disconnect warning
    if (captureBanner.classList.contains("banner-error")) {
      setBanner("recording", "Google Meet captions active",
        "All participants' speech is being captured.");
    }
    return;
  }

  if (msg.type === "meet-captions-ready") {
    if (state.mode === MODE.GOOGLE_MEET) {
      setBanner("recording", "Google Meet captions are live",
        "Captions from all participants will appear in the transcript.");
    }
    return;
  }

  if (msg.type === "meet-captions-enable-prompt" && state.mode === MODE.GOOGLE_MEET) {
    setBanner("error", "Enable captions in Google Meet",
      "Click ⋮ → 'Turn on captions'  or press Ctrl+Shift+C in the Meet tab.");
    return;
  }

  if (msg.type === "meet-tab-detected") {
    const t = (msg.title || "").replace(/^Meet\s*[-–]\s*/i, "").replace(/\s*[-–]\s*Google Meet$/i, "").trim();
    const titleEl = $("meetingTitle");
    if (titleEl && t && !/^(meet|google meet)$/i.test(t) && !titleEl.value.trim()) {
      titleEl.value = t;
    }
    if (state.mode === MODE.NONE || state.mode === MODE.GOOGLE_MEET) {
      state.mode = MODE.GOOGLE_MEET;
      setModeButton(MODE.GOOGLE_MEET);
      setBanner("idle", "Google Meet detected",
        "Enable captions in Meet, then click Start recording.");
    }
    return;
  }
}

// ── Start recording ───────────────────────────────────────────────────────────
startButton.addEventListener("click", async () => {
  if (state.mode === MODE.NONE) {
    $("recordingNotice").textContent = "Please select a capture mode first.";
    return;
  }
  state.startedAt = Date.now();
  state.audioChunks = [];
  state.audioBlob = null;

  if (state.mode === MODE.GOOGLE_MEET) await startGoogleMeetMode();
  else if (state.mode === MODE.TAB)    await startTabAudioMode();
  else                                 await startMicMode();
});

// ── Stop recording ────────────────────────────────────────────────────────────
stopButton.addEventListener("click", stopRecording);

function stopRecording() {
  if (state.recognition) {
    state.recognition.onend = null;
    state.recognition.stop();
    state.recognition = null;
  }
  if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
    state.mediaRecorder.stop();
  }
  state.interimText = "";
  setRecordingUi(false);
  setBanner("idle", "Recording stopped",
    `${state.entries.length} entries captured. Click 'Process with Backend' to analyse.`);
  $("recordingNotice").textContent = "Recording stopped.";
  renderTranscript();
}

// ── Google Meet mode ──────────────────────────────────────────────────────────
async function startGoogleMeetMode() {
  setRecordingUi(true);
  setBanner("recording", "Waiting for Google Meet captions…",
    "Speak in your Meet call. Captions from all participants will appear here automatically.");
  $("recordingNotice").textContent =
    "Listening via Google Meet captions. All participants' speech will be captured with names.";
}

// ── Tab Audio mode ────────────────────────────────────────────────────────────
async function startTabAudioMode() {
  try {
    // chrome.tabCapture must be called from an extension page context
    const stream = await new Promise((resolve, reject) => {
      chrome.tabCapture.capture({ audio: true, video: false }, (s) => {
        if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
        else if (!s) reject(new Error("No stream returned by tabCapture"));
        else resolve(s);
      });
    });

    state.mediaRecorder = new MediaRecorder(stream);
    state.mediaRecorder.ondataavailable = (e) => {
      if (e.data.size) state.audioChunks.push(e.data);
    };
    state.mediaRecorder.onstop = () => {
      state.audioBlob = new Blob(state.audioChunks, { type: "audio/webm" });
      stream.getTracks().forEach((t) => t.stop());
      $("exportAudioButton").disabled = false;
    };
    state.mediaRecorder.start(1000);

    // Run Web Speech Recognition on the captured tab audio
    startSpeechRecognition(stream);
    setRecordingUi(true);
    setBanner("recording", "Tab audio capture active",
      "All voices in the active tab are being transcribed. Speaker names not available in this mode.");
    $("recordingNotice").textContent =
      "Capturing all audio from the active tab (all voices, no speaker names).";
  } catch (err) {
    setBanner("error", "Tab capture failed", err.message);
    $("recordingNotice").textContent = `Tab capture failed: ${err.message}`;
  }
}

// ── Microphone mode ───────────────────────────────────────────────────────────
async function startMicMode() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.mediaRecorder = new MediaRecorder(stream);
    state.mediaRecorder.ondataavailable = (e) => {
      if (e.data.size) state.audioChunks.push(e.data);
    };
    state.mediaRecorder.onstop = () => {
      state.audioBlob = new Blob(state.audioChunks, { type: "audio/webm" });
      stream.getTracks().forEach((t) => t.stop());
      $("exportAudioButton").disabled = false;
    };
    state.mediaRecorder.start(1000);
    startSpeechRecognition(stream);
    setRecordingUi(true);
    setBanner("recording", "Microphone active",
      "Recording YOUR voice only. Use Google Meet Captions mode to capture all participants.");
    $("recordingNotice").textContent = "Recording your microphone.";
  } catch (err) {
    setBanner("error", "Microphone access denied", err.message);
    $("recordingNotice").textContent =
      `Microphone error: ${err.message}. Use the Options page to grant permission.`;
  }
}

// ── Web Speech Recognition (shared for Mic and Tab Audio modes) ───────────────
function startSpeechRecognition(stream) {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    $("recordingNotice").textContent += " (Speech recognition not available in this browser.)";
    return;
  }
  const recog = new SpeechRecognition();
  recog.continuous      = true;
  recog.interimResults  = true;
  recog.lang            = "en-US";

  recog.onresult = (event) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      const text   = result[0].transcript;
      if (result.isFinal) {
        addEntry(text);
      } else {
        state.interimText = text;
        renderTranscript();
      }
    }
  };
  recog.onerror = (e) => {
    if (e.error !== "no-speech")
      $("recordingNotice").textContent = `Speech recognition error: ${e.error}`;
  };
  recog.onend = () => {
    if (state.isRecording && state.recognition === recog) {
      try { recog.start(); } catch (_) {}
    }
  };
  recog.start();
  state.recognition = recog;
}

// ── Payload ───────────────────────────────────────────────────────────────────
function meetingPayload() {
  return {
    transcript: state.entries,
  };
}

// ── Download helper ───────────────────────────────────────────────────────────
function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  if (typeof chrome !== "undefined" && chrome.downloads) {
    chrome.downloads.download({ url, filename, saveAs: true }, () =>
      URL.revokeObjectURL(url)
    );
  } else {
    const a = document.createElement("a");
    a.href = url; a.download = filename; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

// ── Results rendering ─────────────────────────────────────────────────────────
function renderTasks(tasks, enriched) {
  const taskList = enriched ? analysisTasksList : factualTasksList;
  taskList.innerHTML = tasks.length
    ? tasks.map((task) => {
        const taskId = escapeHtml(task.task_id || "Task");
        const description = escapeHtml(task.description || "");
        if (!enriched) {
          const owner = task.explicit_owner
            ? escapeHtml(task.explicit_owner)
            : "Not specified";
          const deadline = task.explicit_deadline
            ? (task.deadline_label ? `<strong>${escapeHtml(task.deadline_label)}</strong> (${escapeHtml(task.explicit_deadline)})` : escapeHtml(task.explicit_deadline))
            : "Not specified";
          const prerequisites = task.explicit_prerequisites || [];
          return `<article class="task-item factual-task">
            <div class="task-top"><span class="task-id">${taskId}</span></div>
            <p class="task-desc">${description}</p>
            <div class="task-meta">
              <span class="task-meta-item"><strong>Explicit owner:</strong> ${owner}</span>
              <span class="task-meta-item"><strong>Explicit deadline:</strong> ${deadline}</span>
            </div>
            ${prerequisites.length
              ? `<div class="task-prereqs"><strong>Explicit prerequisites:</strong>
                 ${prerequisites.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>`
              : ""}
          </article>`;
        }

        const allowedPriorities = ["Low", "Medium", "High"];
        const priority = allowedPriorities.includes(task.priority) ? task.priority : "Medium";
        const status = escapeHtml(task.status || "pending");
        const owner = task.owner
          ? escapeHtml(task.owner)
          : task.missing_owner ? "<em>Unassigned</em>" : "—";
        const deadline = task.deadline
          ? (task.deadline_label ? `<strong>${escapeHtml(task.deadline_label)}</strong> (${escapeHtml(task.deadline)})` : escapeHtml(task.deadline))
          : task.missing_deadline ? "<em>No deadline</em>" : "—";
        const prereqs = [
          ...(task.explicit_prerequisites || []),
          ...(task.inferred_prerequisites || []),
        ];
        return `<article class="task-item priority-${priority}">
          <div class="task-top">
            <span class="task-id">${taskId}</span>
            <div class="task-badges">
              <span class="badge badge-${priority}">${priority}${Number.isFinite(task.priority_score) ? ` · ${task.priority_score}/100` : ""}</span>
              <span class="badge badge-status">${status}</span>
            </div>
          </div>
          <p class="task-desc">${description}</p>
          <div class="task-meta">
            <span class="task-meta-item"><strong>Owner:</strong> ${owner}</span>
            <span class="task-meta-item"><strong>Due:</strong> ${deadline}</span>
          </div>
          ${prereqs.length
            ? `<div class="task-prereqs"><strong>Prerequisites:</strong>
               ${prereqs.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>`
            : ""}
        </article>`;
      }).join("")
    : `<p class="hint">No ${enriched ? "" : "explicit "}tasks found in this transcript.</p>`;
}

function transcriptText() {
  return state.entries
    .map((entry) => {
      const speaker = entry.speaker && !entry.text.startsWith(`${entry.speaker}:`)
        ? `${entry.speaker}: `
        : "";
      return `${entry.timestamp ? `[${entry.timestamp}] ` : ""}${speaker}${entry.text}`;
    })
    .join("\n");
}

function setWorkflowTab(tab) {
  const showAnalysis = tab === "analysis";
  extractTab.classList.toggle("active", !showAnalysis);
  analyzeTab.classList.toggle("active", showAnalysis);
  extractTab.setAttribute("aria-selected", String(!showAnalysis));
  analyzeTab.setAttribute("aria-selected", String(showAnalysis));
  factualPanel.hidden = showAnalysis;
  analysisPanel.hidden = !showAnalysis;
}

function renderFactualResults(data, snapshot = transcriptText()) {
  const meeting = data.data;
  const tasks = meeting.tasks || [];
  state.meetingId = data.meeting_id;
  state.transcriptSnapshot = snapshot;
  resultsMeetingTitle.textContent = meeting.meeting_title || "Untitled meeting";
  factualSummary.textContent = `Meeting ID: ${state.meetingId} · ${tasks.length} factual task${tasks.length === 1 ? "" : "s"}`;
  renderTasks(tasks, false);
  factualResults.style.display = "block";
  factualEmptyState.hidden = true;
  analysisResults.style.display = "none";
  analysisEmptyState.hidden = false;
  analysisEmptyState.textContent = "Select Analyze more to review context, dependencies, and potential blockers.";
  analysisStatus.textContent = "";
  graphCard.innerHTML = "";
  analysisTasksList.innerHTML = "";
  setWorkflowTab("factual");
}

function renderAnalysisResults(result) {
  const analysis = result.data || {};
  const tasks = analysis.tasks || [];
  const graph = analysis.graph_analysis || {};
  const isFeasible = graph.is_feasible !== false;
  const bottlenecks = graph.bottlenecks || [];
  // Separate cycles, not links: both links of a two-task cycle count once.
  const countCycles = (edges) => {
    const parent = new Map();
    const find = (x) => { while (parent.get(x) !== x) x = parent.get(x); return x; };
    for (const e of edges) {
      const a = e.from_task_id || "", b = e.to_task_id || "";
      if (!a || !b) continue;
      if (!parent.has(a)) parent.set(a, a);
      if (!parent.has(b)) parent.set(b, b);
      parent.set(find(a), find(b));
    }
    return new Set([...parent.keys()].map(find)).size;
  };
  const circular = graph.circular_dependencies || [];
  const cycleDetails = graph.cycle_details || [];
  const initialCycles = graph.initial_circular_dependencies || [];
  const resolutions = graph.resolved_dependencies || [];
  const hasResolution = initialCycles.length > 0 && resolutions.length > 0;

  let gHtml = "";

  if (initialCycles.length) {
    gHtml += `<div class="cycle-history">
      <div class="cycle-history-header">
        <strong>Initial circular dependencies detected</strong>
        <span class="badge badge-blocked">${countCycles(initialCycles)}</span>
      </div>
      <ul class="cycle-list">
        ${initialCycles.map((cycle) => `
          <li>
            <strong>${escapeHtml(cycle.from_task_id || "Task")} ➔ ${escapeHtml(cycle.to_task_id || "Task")}</strong>
            ${cycle.description ? `<span>Transcript evidence: ${escapeHtml(cycle.description)}</span>` : ""}
          </li>
        `).join("")}
      </ul>
    </div>`;
  }

  if (resolutions.length) {
    gHtml += `<div class="cycle-resolution">
      <strong>Meeting resolution</strong>
      <ul class="resolution-list">
        ${resolutions.map((resolution) => `
          <li>
            ${resolution.description ? escapeHtml(resolution.description) : escapeHtml(JSON.stringify(resolution))}
          </li>
        `).join("")}
      </ul>
    </div>`;
  }

  gHtml += `<div class="${isFeasible ? "graph-feasible" : "graph-infeasible"}">
    ${isFeasible ? "✓ Schedule Feasible – No Circular Deadlocks" : "⚠ Infeasible – Circular Dependency Detected!"}
  </div>`;

  if (cycleDetails.length) {
    gHtml += `<div class="cycle-history">
      <strong>Detected cycles and supporting transcript statements</strong>
      <ul class="cycle-list">
        ${cycleDetails.map((cycle, index) => `
          <li>
            <strong>Cycle ${index + 1}: ${cycle.task_ids.map(escapeHtml).join(" ➔ ")}</strong>
            <ul>
              ${cycle.dependencies.map((dependency) => `
                <li>${escapeHtml(dependency.prerequisite_task_id)} ➔ ${escapeHtml(dependency.task_id)}:
                  ${dependency.evidence ? `“${escapeHtml(dependency.evidence)}”` : "No transcript evidence was returned for this dependency."}
                </li>
              `).join("")}
            </ul>
          </li>
        `).join("")}
      </ul>
    </div>`;
  } else if (circular.length) {
    gHtml += `<p style="font-size:12px;color:#b91c1c;margin:4px 0"><strong>Cycle:</strong>
      ${circular.map((c) => `${escapeHtml(c.from)} ➔ ${escapeHtml(c.to)}`).join(", ")}</p>`;
  }

  if (bottlenecks.length) {
    gHtml += `<div style="margin-top:6px;"><strong style="font-size:12px;">Bottlenecks:</strong>
      <ul class="bottlenecks-list">
        ${bottlenecks
          .map(
            (b) =>
              `<li><strong>${escapeHtml(b.entity_id)}</strong>
              ${b.is_external ? '<span class="badge badge-blocked">External</span>' : ""}
              → blocks <strong>${escapeHtml(b.blocks_count)}</strong> task(s)</li>`
          )
          .join("")}
      </ul></div>`;
  } else {
    gHtml += `<p style="font-size:12px;color:#64748b;margin:4px 0 0">No critical bottlenecks.</p>`;
  }
  graphCard.innerHTML = gHtml;
  analysisSummary.textContent = `Meeting ID: ${state.meetingId} · ${tasks.length} task${tasks.length === 1 ? "" : "s"} analyzed`;
  renderTasks(tasks, true);
  analysisResults.style.display = "block";
  analysisEmptyState.hidden = true;
  setWorkflowTab("analysis");
}

async function getApiBaseUrl() {
  const settings = await chrome.storage.local.get(["processingEndpoint"]);
  const endpoint = settings.processingEndpoint || PROCESSING_ENDPOINT_DEFAULT;
  const url = new URL(endpoint);
  url.pathname = url.pathname.replace(/\/api\/meetings\/process\/?$/, "").replace(/\/$/, "");
  url.search = "";
  url.hash = "";
  return url;
}

// A serverless backend can be asleep. POSTs must not be blindly retried (they could create duplicates),
// so wake the backend with a harmless GET first (at most once a minute) so the real POST hits a warm instance.
let lastBackendWarm = 0;
async function warmBackend(anyApiUrl) {
  if (Date.now() - lastBackendWarm < 60000) return;
  const health = new URL(anyApiUrl);
  const apiAt = health.pathname.indexOf("/api/");
  const prefix = apiAt >= 0 ? health.pathname.slice(0, apiAt) : health.pathname.replace(/\/$/, "");
  health.pathname = `${prefix}/health`;
  health.search = "";
  for (let attempt = 1; attempt <= 4; attempt++) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 15000);
      const response = await fetch(health, { signal: controller.signal });
      clearTimeout(timer);
      if (response.ok) { lastBackendWarm = Date.now(); return; }
    } catch (_) { /* network blip or cold start: try again */ }
    await new Promise((r) => setTimeout(r, 800 * attempt));
  }
}

async function postJson(url, body, { longRunning = false } = {}) {
  await warmBackend(url); // covers every step: extract-factual, infer-context, ...
  const options = { method: "POST" };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  // Retry transient failures (cold start, gateway errors, provider hiccups). Every step here is safe to repeat:
  // extract-factual saves only after it succeeds and infer-context just recomputes the same meeting.
  // A long-running step (the analysis can take minutes on a rate-limited plan) is sent exactly once: re-sending a dropped
  // request would start a second copy of the same work. The caller waits for the saved result instead.
  const attempts = longRunning ? 1 : 3;
  let response;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      response = await fetch(url, options);
    } catch (error) {
      if (longRunning) {
        const dropped = new Error("The connection dropped while the server was still working.");
        dropped.dropped = true;
        throw dropped;
      }
      if (attempt === 3) throw error;
      await new Promise((r) => setTimeout(r, 1500 * attempt));
      continue;
    }
    if (longRunning && [502, 503, 504].includes(response.status)) {
      // A gateway timeout has no JSON answer from our server; an error with a code (quota, authentication, ...) is a real failure.
      const body = await response.clone().json().catch(() => null);
      if (!(body && body.detail && body.detail.code)) {
        const dropped = new Error(`HTTP ${response.status}`);
        dropped.dropped = true;
        throw dropped;
      }
    }
    if ([429, 502, 503, 504].includes(response.status) && attempt < attempts) {
      await new Promise((r) => setTimeout(r, 1500 * attempt));
      continue;
    }
    break;
  }
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const result = await response.json();
      if (result.detail) {
        const d = result.detail;
        detail = typeof d === "string" ? d : (d.message || (d.code ? d.code.replace(/_/g, " ") : JSON.stringify(d)));
      }
    } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

// Run the analysis, and if the connection drops while the server is still working, wait for the saved result.
async function inferWithRecovery(endpoint, baseUrl, meetingId, onWaiting) {
  const meetingUrl = new URL(baseUrl);
  meetingUrl.pathname = `${meetingUrl.pathname.replace(/\/$/, "")}/api/meetings/${encodeURIComponent(meetingId)}`;
  const readMeeting = async () => {
    try {
      const response = await fetch(meetingUrl);
      return response.ok ? await response.json() : null;
    } catch (_) {
      return null;
    }
  };
  const before = await readMeeting();
  try {
    return await postJson(endpoint, undefined, { longRunning: true });
  } catch (err) {
    if (!err.dropped) throw err;
    const deadline = Date.now() + 8 * 60 * 1000;
    while (Date.now() < deadline) {
      if (onWaiting) onWaiting();
      await new Promise((r) => setTimeout(r, 6000));
      const doc = await readMeeting();
      const fresh = doc && doc.status === "completed" && Array.isArray(doc.enriched_tasks) &&
        (!before || doc.updated_at !== before.updated_at || !Array.isArray(before.enriched_tasks));
      if (fresh) return { status: "success", data: { tasks: doc.enriched_tasks, graph_analysis: doc.graph_analysis } };
    }
    throw new Error("The analysis is taking longer than expected. It may still finish: reopen this meeting on the website in a minute, or press Analyze more again.");
  }
}

async function extractFacts() {
  if (!state.entries.length) throw new Error("Capture or add transcript text before processing.");
  const baseUrl = await getApiBaseUrl();
  processStatus.textContent = "Extracting explicitly stated tasks…";
  const snapshot = transcriptText();
  const endpoint = new URL(baseUrl);
  endpoint.pathname = `${endpoint.pathname.replace(/\/$/, "")}/api/v1/extract-factual`;
  const payload = {
    transcript_text: snapshot,
    participants: (state.participants && state.participants.length) ? state.participants : undefined,
    meeting_title: (resultsMeetingTitle && resultsMeetingTitle.textContent.trim()) || $("meetingTitle")?.value.trim() || undefined,
  };
  const result = await postJson(endpoint, payload);
  if (!result.meeting_id || !result.data || !Array.isArray(result.data.tasks)) {
    throw new Error("The backend returned an unexpected factual extraction response.");
  }
  renderFactualResults(result, snapshot);
  processStatus.textContent = `Extracted ${result.data.tasks.length} factual task(s).`;
  processStatus.className = "hint success";
}

extractTab.addEventListener("click", () => setWorkflowTab("factual"));
analyzeTab.addEventListener("click", () => setWorkflowTab("analysis"));

processButton.addEventListener("click", async () => {
  if (state.isProcessing) return;
  state.isProcessing = true;
  processButton.disabled = true;
  analyzeMoreButton.disabled = true;
  processStatus.className = "hint";
  processStatus.textContent = "Extracting explicitly stated tasks…";
  try {
    await extractFacts();
  } catch (err) {
    processStatus.textContent = `Extraction failed: ${err.message}`;
    processStatus.className = "hint error";
  } finally {
    processButton.disabled = false;
    analyzeMoreButton.disabled = false;
    state.isProcessing = false;
  }
});

analyzeMoreButton.addEventListener("click", async () => {
  if (state.isProcessing) return;
  state.isProcessing = true;
  analyzeMoreButton.disabled = true;
  processButton.disabled = true;
  analysisStatus.className = "hint";
  analysisStatus.textContent = "Preparing factual tasks…";
  try {
    if (!state.meetingId || state.transcriptSnapshot !== transcriptText()) {
      await extractFacts();
    }
    analysisStatus.textContent = "Analyzing context, dependencies, and schedule…";
    const baseUrl = await getApiBaseUrl();
    const endpoint = new URL(baseUrl);
    endpoint.pathname = `${endpoint.pathname.replace(/\/$/, "")}/api/v1/infer-context/${encodeURIComponent(state.meetingId)}`;
    const result = await inferWithRecovery(endpoint, baseUrl, state.meetingId, () => {
      analysisStatus.textContent = "Still analysing: the server is working. This can take a few minutes on a rate-limited plan…";
    });
    if (!result.data || !Array.isArray(result.data.tasks)) {
      throw new Error("The backend returned an unexpected analysis response.");
    }
    renderAnalysisResults(result);
    analysisStatus.textContent = "Context and dependency analysis complete.";
    analysisStatus.className = "hint success";
  } catch (err) {
    analysisStatus.textContent = `Analysis failed: ${err.message}`;
    analysisStatus.className = "hint error";
  } finally {
    analyzeMoreButton.disabled = false;
    processButton.disabled = false;
    state.isProcessing = false;
  }
});

// ── Misc event listeners ──────────────────────────────────────────────────────
$("clearButton").addEventListener("click", () => {
  if (state.isRecording) return;
  state.entries = [];
  state.participants = [];
  const el = $("participantsDetected");
  if (el) el.textContent = "Participants: auto-detected from Google Meet";
  renderTranscript();
});

$("exportJsonButton").addEventListener("click", () => {
  downloadBlob(
    new Blob([JSON.stringify(meetingPayload(), null, 2)], { type: "application/json" }),
    "meeting-data.json"
  );
});

$("exportAudioButton").addEventListener("click", () => {
  if (state.audioBlob) downloadBlob(state.audioBlob, "meeting-recording.webm");
});

// Manual entry
$("manualEntryToggle").addEventListener("click", () => {
  const box = $("manualInputBox");
  box.style.display = box.style.display === "none" ? "block" : "none";
  if (box.style.display === "block") $("manualText").focus();
});

$("addManualBtn").addEventListener("click", () => {
  const text = $("manualText").value.trim();
  if (text) {
    addEntry(text, formatTime(elapsed()));
    $("manualText").value = "";
    $("manualInputBox").style.display = "none";
  }
});

// Demo transcript (uses realistic multi-speaker format)
$("sampleButton").addEventListener("click", () => {
  state.participants = ["Alice", "Bob", "Charlie"];
  const el = $("participantsDetected");
  if (el) el.textContent = `Participants: ${state.participants.join(", ")}`;
  state.entries = [
    { timestamp: "00:05", text: "Alice: Welcome everyone. Bob, please build the API database migration by Wednesday.", source: "demo" },
    { timestamp: "00:22", text: "Bob: Sure, but Charlie has to finish the schema definition first.", source: "demo" },
    { timestamp: "00:38", text: "Charlie: I can finish the schema definition by tomorrow evening.", source: "demo" },
    { timestamp: "00:51", text: "Alice: Great. We are also waiting on AWS support to approve our quota before deployment.", source: "demo" },
    { timestamp: "01:10", text: "Bob: Once Alice approves the architecture doc, I can begin the performance testing.", source: "demo" },
  ];
  renderTranscript();
});

// Title editing
if ($("editMeetingTitleBtn")) {
  $("editMeetingTitleBtn").addEventListener("click", () => {
    const current = $("resultsMeetingTitle").textContent.trim();
    $("editMeetingTitleInput").value = current;
    $("titleEditContainer").style.display = "flex";
    $("editMeetingTitleInput").focus();
  });
}

if ($("cancelMeetingTitleBtn")) {
  $("cancelMeetingTitleBtn").addEventListener("click", () => {
    $("titleEditContainer").style.display = "none";
  });
}

if ($("saveMeetingTitleBtn")) {
  $("saveMeetingTitleBtn").addEventListener("click", async () => {
    const newTitle = $("editMeetingTitleInput").value.trim();
    if (!newTitle) return;
    $("resultsMeetingTitle").textContent = newTitle;
    $("titleEditContainer").style.display = "none";
    if (state.meetingId) {
      try {
        const baseUrl = await getApiBaseUrl();
        const endpoint = new URL(baseUrl);
        endpoint.pathname = `${endpoint.pathname.replace(/\/$/, "")}/api/meetings/${encodeURIComponent(state.meetingId)}`;
        await fetch(endpoint, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ meeting_title: newTitle }),
        });
      } catch (err) {
        console.warn("Could not persist title update to backend:", err);
      }
    }
  });
}

// ── Background keepalive heartbeat ───────────────────────────────────────────
// Chrome MV3 service workers terminate after ~30 s of inactivity.
// We ping the background every 25 s to prevent that.
function keepBackgroundAlive() {
  chrome.runtime.sendMessage({ type: "keepalive" }).catch(() => {
    // Background SW was already terminated — reconnect port
    connectToBackground();
  });
}
setInterval(keepBackgroundAlive, 25000);

// ── Init ──────────────────────────────────────────────────────────────────────
connectToBackground();
renderTranscript();
