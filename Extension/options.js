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

document.getElementById("save").addEventListener("click", async () => {
  const value = endpoint.value.trim() || fallbackEndpoint;
  if (value && !/^https?:\/\/(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?(?:\/|$)/.test(value)) {
    status.className = "error";
    status.textContent = "Please use a valid localhost or 127.0.0.1 backend endpoint.";
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
    status.textContent = `Connection failed: ${err.message}. Make sure the backend is running at http://127.0.0.1:8000.`;
  }
});
