# Minutes frontend

React + TypeScript + Vite. Setup and page/API mapping: [project README](../README.md).

```powershell
npm install
npm run dev
# Your verification steps:
npm run test
npm run build
```

Node 22.12+; development URL http://localhost:5173. Set VITE_API_BASE_URL in .env.local if the backend is elsewhere (production uses https://capstone-api.shivsharan.me; see ../DEPLOYMENT.md). Never put provider keys or MongoDB credentials in frontend environment variables. Start the required Atlas-backed FastAPI server separately. No sample responses are used as application data.

The frontend uses saved backend data throughout; no mock data is used in the application.


Meetings are displayed newest first by actual meeting date/time, with valid minutes metadata as a fallback. Missing or invalid dates sort after dated meetings. Existing search, filters, links and selection are preserved.

The meeting-detail export controls now offer PDF, Excel and the existing DOCX. All use /api/v1/minutes/{meeting_id}/export/{format}; Excel uses xlsx. The latest approved revision is the default, and the existing explicit draft checkbox selects the latest version instead. The browser uses the sanitized filename supplied by the backend. PDFs contain concise saved minutes and an action-item table; workbooks organize metadata, minutes/decisions and tasks into separate sheets.

The Meeting Assistant keeps the selected-meeting dropdown and existing POST /api/meetings/{meeting_id}/query contract. Basic questions use saved data without LLM calls; other questions retain grounded retrieval. Changing the selected meeting clears the previous answers. Missing information is stated explicitly, and inferred task details are labeled.


Targeted workspace update verification: **89 backend tests and 18 frontend tests passed; the TypeScript/Vite production build passed.** Tests reopened generated PDF/XLSX files and verified contents, long task-table wrapping, formula safety, selected-meeting assistant isolation, sorting/filter/navigation behavior and browser download-handler logic. Browser handling used the frontend test environment; a manual live-browser/Atlas workflow was not performed for this update.


Audio meetings use the existing New meeting dialog: upload MP3/WAV/M4A (50 MB and 30 minutes maximum), wait for local transcription, listen and correct text, rename/assign speaker labels, then Process meeting. Unknown speakers stay unassigned. Add a segment if multiple speaker turns occur within one block; new timestamps stay unknown. The existing meeting details, tasks, dependencies, assistant and exports are reused. Audio upload calls only the new `POST /api/meetings/transcribe` multipart endpoint; reviewed entries then use the existing process and minutes-generation APIs. No speech API key goes in the frontend. Initial model download and backend setup are documented in [the backend guide](../task-backend/MINUTES_MODULE_README.md#local-audio-meeting-upload). Raw audio is not archived; saved transcript/results use the existing database.

Final audio update checks: **99 backend tests and 23 frontend tests passed; TypeScript and the Vite production build passed.** The existing bundle-size warning remains.
