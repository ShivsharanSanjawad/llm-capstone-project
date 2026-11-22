// Backend used by the extension. Change both endpoints together, e.g. to
// http://127.0.0.1:8000 for local development, or to a GCP Cloud Run URL (see DEPLOYMENT.md).
// If you change the host, also add it to "host_permissions" in manifest.json.
// A value saved on the extension's Settings page overrides processingEndpoint.
// Audio transcription (/api/meetings/transcribe) only works on a backend that has faster-whisper
// installed (local run or Docker/GCP), not on the Vercel backend.
window.EXTENSION_CONFIG = {
  processingEndpoint: "https://capstone-api.shivsharan.me/api/meetings/process",
  transcriptionEndpoint: "https://capstone-api.shivsharan.me/api/meetings/transcribe",
  processingIntervalMinutes: 5,
};
