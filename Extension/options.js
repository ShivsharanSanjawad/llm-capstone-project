const endpoint = document.getElementById("endpoint");
const status = document.getElementById("status");

const fallbackEndpoint = (window.EXTENSION_CONFIG && window.EXTENSION_CONFIG.processingEndpoint) 
  || "http://127.0.0.1:8000/api/meetings/process";

chrome.storage.local.get(["processingEndpoint"]).then((settings) => {
  endpoint.value = settings.processingEndpoint || fallbackEndpoint;
});

document.getElementById("grantMic").addEventListener("click", async () => {
  status.className = "";
  status.textContent = "Requesting microphone permission...";

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((track) => track.stop());
    status.className = "success";
    status.textContent = "Microphone permission granted. Return to the side panel to record.";
  } catch (err) {
    status.className = "error";
    status.textContent = `Microphone permission not granted: ${err.message}. Allow microphone access for the extension in Chrome settings, then try again.`;
  }
});

// The hosts the extension is allowed to call (see host_permissions in manifest.json).
function isAllowedEndpoint(value) {
  try {
    const url = new URL(value);
    const host = url.hostname;
    if (url.protocol === "http:" || url.protocol === "https:") {
      if (["localhost", "127.0.0.1", "0.0.0.0"].includes(host)) return true;
    }
    return url.protocol === "https:" && (
      host === "shivsharan.me" || host.endsWith(".shivsharan.me") ||
      host.endsWith(".run.app") || host === "vizura-backend.vercel.app");
  } catch (_) {
    return false;
  }
}

document.getElementById("save").addEventListener("click", async () => {
  const value = endpoint.value.trim() || fallbackEndpoint;
  if (!isAllowedEndpoint(value)) {
    status.className = "error";
    status.textContent = "Use http://localhost or 127.0.0.1 (local backend), or an https address on shivsharan.me, run.app or vizura-backend.vercel.app. Any other host must also be added to host_permissions in manifest.json.";
    return;
  }
  await chrome.storage.local.set({ processingEndpoint: value });
  status.className = "success";
  status.textContent = "Settings saved successfully.";
});

document.getElementById("test").addEventListener("click", async () => {
  const urlStr = endpoint.value.trim() || fallbackEndpoint;
  status.className = "";
  status.textContent = `Testing connection to ${urlStr}...`;

  try {
    const url = new URL(urlStr);
    const healthUrl = `${url.protocol}//${url.host}/health`;
    
    const response = await fetch(healthUrl, { method: "GET" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    
    const data = await response.json();
    status.className = "success";
    status.textContent = `✓ Backend online! Provider: ${data.llm_provider || 'Active'} • Database: ${data.database_mode}`;
  } catch (err) {
    status.className = "error";
    status.textContent = `Connection failed: ${err.message}. Check the address, and for a local backend make sure it is running.`;
  }
});
