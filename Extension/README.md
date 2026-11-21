# Meeting Minutes Companion (Chrome Extension)

This folder contains the browser extension for **Meeting Minutes & Task Intelligence**.

## Capabilities

- Chrome / Edge Manifest V3 side panel.
- Real-time speech recognition with timestamped transcript logging.
- Microphone audio recording exportable to WebM.
- Two-stage processing with the local FastAPI backend.
- Direct visualization of:
  - Factual extraction results with the meeting title and explicit task descriptions.
  - Optional contextual analysis with priorities, assignees, and deadlines.
  - Direct and indirect circular-dependency checks, with transcript evidence for each detected dependency edge.
  - Schedule feasibility checks and bottlenecks from the graph engine.
- Configurable backend endpoint with built-in connection testing.

## Install for Development

1. Open `chrome://extensions` or `edge://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked** and select this `Extension` folder.
4. Click the extension action in the toolbar to open the side panel.
5. Click **Start meeting** to capture live speech.
6. When finished, click **Extract tasks** to display explicitly stated tasks.
7. Click **Analyze more** to run contextual inference and graph analysis. It checks the complete dependency graph for direct and indirect cycles and displays supporting transcript statements for each cycle edge. If extraction has not run for the current transcript, the extension extracts it first automatically.

## Settings & Backend Contract

The extension defaults to `http://127.0.0.1:8000/api/meetings/process`. It uses this configured backend URL to call `POST /api/v1/extract-factual`, then calls `POST /api/v1/infer-context/{meeting_id}` when **Analyze more** is selected. You can change the URL in **Processing settings** (`options.html`) and test the connection at any time.

Factual extraction receives the transcript as plain text:

```json
{
  "transcript_text": "[00:05] Bob, please fix the database migration by Friday."
}
```

The extension displays the title and factual tasks from the extraction response. The optional analysis request uses its `meeting_id` and displays the enriched tasks and graph results.
