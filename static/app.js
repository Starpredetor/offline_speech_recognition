const sourceLanguage = document.getElementById("source-language");
const targetLanguage = document.getElementById("target-language");
const micToggle = document.getElementById("mic-toggle");
const setupArgosBtn = document.getElementById("setup-argos-btn");
const outputWindow = document.getElementById("output-window");
const statusIndicator = document.getElementById("status-indicator");

let running = false;
let lastMessageId = 0;

function appendLine(kind, text) {
  const line = document.createElement("div");
  line.className = `line ${kind}`;
  line.textContent = text;
  outputWindow.appendChild(line);
  outputWindow.scrollTop = outputWindow.scrollHeight;
}

function setStatus(isRunning) {
  running = isRunning;
  statusIndicator.textContent = isRunning ? "Running" : "Idle";
  statusIndicator.classList.toggle("running", isRunning);
  statusIndicator.classList.toggle("idle", !isRunning);

  micToggle.textContent = isRunning ? "Stop Mic" : "Start Mic";
  micToggle.classList.toggle("active", isRunning);
}

async function refreshStatus() {
  const res = await fetch("/api/realtime/status");
  const data = await res.json();
  setStatus(Boolean(data.running));
}

async function pollMessages() {
  const res = await fetch(`/api/realtime/messages?since=${lastMessageId}`);
  const data = await res.json();

  for (const message of data.messages || []) {
    lastMessageId = Math.max(lastMessageId, Number(message.id || 0));
    appendLine(message.kind || "status", message.text || "");
  }
}

async function startRealtime() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    appendLine("warning", "Browser microphone API is unavailable. Use a modern browser.");
  } else {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      appendLine("status", "Browser microphone permission granted.");
      for (const track of stream.getTracks()) {
        track.stop();
      }
    } catch (err) {
      appendLine("error", `Microphone permission denied or unavailable: ${err}`);
      setStatus(false);
      return;
    }
  }

  try {
    const micRes = await fetch("/api/mic/check");
    const micData = await micRes.json();
    if (!micData.ok) {
      appendLine("error", micData.message || "No microphone input device available.");
      setStatus(false);
      return;
    }
  } catch (err) {
    appendLine("warning", `Unable to verify microphone device: ${err}`);
  }

  const res = await fetch("/api/realtime/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source: sourceLanguage.value,
      target: targetLanguage.value,
    }),
  });
  const data = await res.json();
  appendLine(data.ok ? "status" : "error", data.message || "Unknown response");
  setStatus(Boolean(data.running));
}

async function stopRealtime() {
  const res = await fetch("/api/realtime/stop", {
    method: "POST",
  });
  const data = await res.json();
  appendLine(data.ok ? "status" : "warning", data.message || "Unknown response");
  setStatus(Boolean(data.running));
}

micToggle.addEventListener("click", async () => {
  micToggle.disabled = true;
  try {
    if (running) {
      await stopRealtime();
    } else {
      await startRealtime();
    }
  } catch (err) {
    appendLine("error", `Request failed: ${err}`);
  } finally {
    micToggle.disabled = false;
  }
});

setupArgosBtn.addEventListener("click", async () => {
  setupArgosBtn.disabled = true;
  appendLine("status", "Installing Argos translation models...");
  try {
    const res = await fetch("/api/argos/setup", { method: "POST" });
    const data = await res.json();
    appendLine(data.ok ? "status" : "warning", data.message || "Unknown response");
  } catch (err) {
    appendLine("error", `Setup failed: ${err}`);
  } finally {
    setupArgosBtn.disabled = false;
  }
});

setInterval(async () => {
  try {
    await pollMessages();
    await refreshStatus();
  } catch (_err) {
    // Keep polling loop alive even if a request fails.
  }
}, 1000);

refreshStatus();
