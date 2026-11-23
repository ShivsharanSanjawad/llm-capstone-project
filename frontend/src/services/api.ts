import type {
  MeetingDoc,
  ProcessInput,
  ProcessResult,
  Revision,
  Minutes,
  Stats,
  Health,
  TaskStatus,
  QueryAnswer,
  Entry,
} from '../types';
const base = (
  import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'
).replace(/\/$/, '');
const minutes = '/api/v1/minutes';
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail: Record<string, unknown> = {}
  ) {
    super(message);
  }
}
const messages: Record<string, string> = {
  self_dependency: 'A task cannot depend on itself.',
  dependencies_not_analyzed:
    'Run the analysis for this meeting first; there are no analysed tasks to edit.',
  dependency_write_failed: 'The dependency change could not be saved. Try again.',
  meeting_delete_failed:
    'Atlas could not confirm deletion. Refresh the meeting list before retrying.',
  incomplete_or_refused_output:
    'The model stopped before returning complete minutes, or refused the request. Check the diagnostic finish_reason; a length limit may need a larger output budget.',
  task_processing_partial_failure:
    'Factual tasks were saved, but enrichment did not finish. Open the saved meeting to continue.',
  task_processing_failed:
    'Task extraction did not finish. Check the backend provider configuration and try again.',
  database_read_failed:
    'Atlas could not read the meeting data. Check the database connection.',
  database_write_failed:
    'Atlas did not confirm the write. Check the database before retrying.',
  storage_unavailable:
    'Minutes could not be saved to Atlas. No successful save was confirmed.',
  minutes_storage_read_failed: 'Minutes could not be read from Atlas.',
  monitoring_storage_unavailable:
    'Monitoring records could not be read from Atlas.',
  provider_authentication:
    'The LLM provider rejected its API key. Check backend credentials.',
  provider_invalid_request:
    'The selected model does not accept this request. Check provider/model compatibility.',
  invalid_structured_output:
    'The provider returned invalid minutes data. No draft was saved.',
  meeting_has_no_transcript: 'This meeting has no saved transcript to search.',
  invalid_transcript_or_configuration:
    'The transcript is empty, too large, or the generation configuration is invalid.',

  provider_not_configured: 'No LLM API key is configured on the backend.',
  request_exceeds_context_budget:
    'This transcript is too long for one analysis call. Shorten it, or raise MINUTES_CONTEXT_TOKENS on the backend.',
  request_exceeds_token_rate_budget:
    'This transcript is larger than the provider tokens-per-minute limit allows. Shorten it, or use a plan or model with a higher limit.',
  llm_call_limit: 'Too many model calls were needed for this request. Try a shorter transcript.',
  database_unavailable:
    'MongoDB Atlas is unavailable. Check the backend connection.',
  minutes_not_found: 'Minutes have not been generated yet.',
  revision_conflict:
    'Someone changed these minutes. Reload before saving your edits.',
  task_revision_conflict: 'Task statuses changed. Refresh and try again.',
  provider_quota_exhausted:
    'The AI provider says its usage quota is used up (a free tier resets daily). Try again later, or switch the provider.',
  generation_timeout:
    'Generation reached its time limit. Check saved results before retrying.',
  provider_transient_failure:
    'The LLM provider is temporarily unavailable or rate limited. Try again later.',
  no_exportable_revision:
    'Approve minutes first, or explicitly export a draft.',
  review_flags_require_acknowledgement:
    'Review and acknowledge the factual verification flags before approving.',
  monitoring_persistence_failed:
    'Minutes may have been saved, but monitoring could not be persisted. Refresh this meeting before retrying.',
};
async function request<T>(
  path: string,
  options: RequestInit = {},
  timeout = 30000,
  decode?: (response: Response) => Promise<T>
): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeout);
  try {
    // Page loads (GET) are idempotent: quietly retry brief network blips and gateway errors,
    // e.g. a cold-starting serverless backend or a DNS/TLS hiccup. Writes are never retried.
    const isRead = !options.method || options.method.toUpperCase() === 'GET';
    const attempts = isRead ? 3 : 1;
    let response!: Response;
    for (let attempt = 1; attempt <= attempts; attempt++) {
      try {
        response = await fetch(base + path, {
          ...options,
          signal: controller.signal,
          headers: {
            ...(options.body instanceof FormData
              ? {}
              : { 'Content-Type': 'application/json' }),
            ...options.headers,
          },
        });
      } catch (error) {
        const aborted = error instanceof DOMException && error.name === 'AbortError';
        if (aborted || attempt === attempts) throw error;
        await new Promise((r) => setTimeout(r, 400 * attempt));
        continue;
      }
      if ([502, 503, 504].includes(response.status) && attempt < attempts) {
        await new Promise((r) => setTimeout(r, 400 * attempt));
        continue;
      }
      break;
    }
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const detail = data.detail;
      const object =
        detail && typeof detail === 'object' && !Array.isArray(detail)
          ? (detail as Record<string, unknown>)
          : {};
      const code = String(object.code || '');
      const message =
        messages[code] ||
        (code.startsWith('audio_') ||
        code === 'unsupported_audio' ||
        code === 'invalid_audio' ||
        code === 'empty_audio'
          ? typeof object.message === 'string'
            ? object.message
            : undefined
          : undefined) ||
        (Array.isArray(detail)
          ? 'Please check the request fields.'
          : typeof detail === 'string'
            ? detail
            : code
              ? code.replaceAll('_', ' ')
              : `Request failed (${response.status}).`);
      const diagnostic = [
        code && `code: ${code}`,
        object.upstream_status && `provider HTTP ${object.upstream_status}`,
        object.parameter && `parameter: ${object.parameter}`,
        object.finish_reason && `finish: ${object.finish_reason}`,
        object.request_id && `request: ${object.request_id}`,
      ]
        .filter(Boolean)
        .join('; ');
      throw new ApiError(
        response.status,
        message + (diagnostic ? ` (${diagnostic})` : ''),
        object
      );
    }
    if (decode) return await decode(response);
    if (response.headers.get('content-type')?.includes('application/json'))
      return (await response.json()) as T;
    return (await response.blob()) as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === 'AbortError')
      throw new ApiError(
        408,
        'Request timed out. It may still be running on the backend; refresh before submitting again.'
      );
    throw new ApiError(
      0,
      'Cannot reach the backend. Start the API server and check its Atlas connection.'
    );
  } finally {
    window.clearTimeout(timer);
  }
}
// A serverless backend may be asleep. Writes must not be blindly retried (they could create duplicates),
// so wake the backend with a harmless GET first, at most once a minute.
let lastWarm = 0;
async function ensureWarm() {
  if (import.meta.env.MODE === 'test' || Date.now() - lastWarm < 60000) return;
  for (let attempt = 1; attempt <= 4; attempt++) {
    try {
      await request('/health', {}, 15000);
      lastWarm = Date.now();
      return;
    } catch {
      await new Promise((r) => setTimeout(r, 800 * attempt));
    }
  }
}
// Start waking the backend as soon as the site opens.
if (typeof window !== 'undefined') void ensureWarm();
const post = async <T>(path: string, body?: unknown) => {
  await ensureWarm();
  return request<T>(
    path,
    {
      method: 'POST',
      body: body === undefined ? undefined : JSON.stringify(body),
    },
    710000
  );
};
const id = encodeURIComponent;
// Writing minutes for a long meeting can take minutes (many model calls, provider rate limits). The browser or an
// intermediate proxy may drop the connection while the server keeps working and saves the result. So when the connection
// drops, check for the saved minutes instead of reporting a failure. This never re-sends the request (no duplicate work).
export const recovery = { pollMs: 6000, maxWaitMs: 10 * 60 * 1000 };
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
// A dropped connection has no answer from our server. A 502/503/504 that carries one of our error codes IS an answer
// (for example the provider quota is used up), so it is a real failure, not something to wait out.
const connectionDropped = (e: unknown) =>
  e instanceof ApiError &&
  (e.status === 0 || e.status === 408 || ([502, 503, 504].includes(e.status) && !e.detail?.code));
async function generationWithRecovery(
  meetingId: string,
  start: () => Promise<Revision>
): Promise<Revision> {
  let before = 0;
  try {
    before = (await api.minutes(meetingId)).revision;
  } catch {
    /* none yet */
  }
  try {
    return await start();
  } catch (e) {
    if (!connectionDropped(e)) throw e;
    const deadline = Date.now() + recovery.maxWaitMs;
    while (Date.now() < deadline) {
      await sleep(recovery.pollMs);
      try {
        const latest = await api.minutes(meetingId);
        if (latest.revision > before) return latest;
      } catch (poll) {
        const waiting = poll instanceof ApiError && (poll.status === 404 || connectionDropped(poll));
        if (!waiting) throw poll;
      }
    }
    throw e;
  }
}
export const api = {
  transcribe: async (file: File) => {
    await ensureWarm();
    const body = new FormData();
    body.append('file', file);
    return request<{
      transcript: Entry[];
      language: string;
      duration_seconds: number;
      speaker_mode: 'manual';
    }>('/api/meetings/transcribe', { method: 'POST', body }, 1800000);
  },
  deleteMeeting: (meetingId: string) =>
    request<{ status: string; meeting_id: string }>(
      '/api/meetings/' + id(meetingId),
      { method: 'DELETE' }
    ),
  updateMeeting: (
    meetingId: string,
    updates: { meeting_title?: string; participants?: string[] }
  ) =>
    request<{ status: string; meeting_id: string; meeting_title?: string; participants?: string[] }>(
      '/api/meetings/' + id(meetingId),
      {
        method: 'PATCH',
        body: JSON.stringify(updates),
      }
    ),
  updateMeetingTitle: (meetingId: string, meeting_title: string) =>
    request<{ status: string; meeting_id: string; meeting_title: string }>(
      '/api/meetings/' + id(meetingId),
      {
        method: 'PATCH',
        body: JSON.stringify({ meeting_title }),
      }
    ),
  editDependencies: (
    meetingId: string,
    changes: {
      add?: { prerequisite: string; task_id: string }[];
      remove?: { prerequisite: string; task_id: string }[];
    }
  ) =>
    request<{ status: string; meeting_id: string }>(
      '/api/meetings/' + id(meetingId) + '/dependencies',
      { method: 'PATCH', body: JSON.stringify(changes) }
    ),
  health: () => request<Health>(minutes + '/health'),
  async meetings(): Promise<MeetingDoc[]> {
    const all: MeetingDoc[] = [];
    let offset = 0;
    while (true) {
      const page = await request<{ meetings: MeetingDoc[] }>(
        `/api/meetings?offset=${offset}&limit=100`
      );
      all.push(...page.meetings);
      if (page.meetings.length < 100) return all;
      offset += 100;
    }
  },
  meeting: (meetingId: string) =>
    request<MeetingDoc>('/api/meetings/' + id(meetingId)),
  async process(input: ProcessInput): Promise<ProcessResult> {
    const transient = (e: unknown) =>
      e instanceof ApiError && [0, 429, 502, 503, 504].includes(e.status);
    for (let attempt = 1; ; attempt++) {
      try {
        return await post<ProcessResult>('/api/meetings/process', input);
      } catch (e) {
        if (!transient(e) || attempt >= 3) throw e;
        const saved = (e as ApiError).detail?.meeting_id;
        // The factual tasks were already saved: finish that meeting instead of creating a duplicate.
        if (typeof saved === 'string' && saved) {
          await new Promise((r) => setTimeout(r, 1500 * attempt));
          try {
            await api.infer(saved);
          } catch {
            throw e; // keep the original error: it carries the saved meeting id for manual retry
          }
          return { status: 'success', meeting_id: saved };
        }
        await new Promise((r) => setTimeout(r, 1500 * attempt));
      }
    }
  },
  infer: (meetingId: string) => post('/api/v1/infer-context/' + id(meetingId)),
  async minutesList(): Promise<Revision[]> {
    const all: Revision[] = [];
    let offset = 0;
    while (true) {
      const page = await request<{ items: Revision[]; total: number }>(
        `${minutes}?offset=${offset}&limit=100`
      );
      all.push(...page.items);
      if (all.length >= page.total || !page.items.length) return all;
      offset += 100;
    }
  },
  minutes: (meetingId: string) =>
    request<Revision>(minutes + '/' + id(meetingId)),
  history: (meetingId: string) =>
    request<Revision[]>(minutes + '/' + id(meetingId) + '/history'),
  generate: (meetingId: string) =>
    generationWithRecovery(meetingId, () =>
      post<Revision>(minutes + '/generate/' + id(meetingId))
    ),
  regenerate: (meetingId: string) =>
    generationWithRecovery(meetingId, () =>
      post<Revision>(minutes + '/' + id(meetingId) + '/regenerate')
    ),
  edit: (
    meetingId: string,
    revision: number,
    content: Minutes,
    reviewer: string
  ) =>
    request<Revision>(minutes + '/' + id(meetingId), {
      method: 'PUT',
      body: JSON.stringify({
        expected_revision: revision,
        minutes: content,
        reviewer,
      }),
    }),
  approve: (
    meetingId: string,
    revision: number,
    reviewer: string,
    acknowledged: boolean
  ) =>
    post<Revision>(minutes + '/' + id(meetingId) + '/approve', {
      expected_revision: revision,
      reviewer,
      acknowledge_review_flags: acknowledged,
    }),
  status: (
    meetingId: string,
    taskId: string,
    status: TaskStatus,
    revision: number
  ) =>
    request(
      '/api/meetings/' + id(meetingId) + '/tasks/' + id(taskId) + '/status',
      {
        method: 'PATCH',
        body: JSON.stringify({ status, expected_revision: revision }),
      }
    ),
  query: (meetingId: string, question: string) =>
    post<QueryAnswer>('/api/meetings/' + id(meetingId) + '/query', {
      question,
    }),
  stats: () => request<Stats>(minutes + '/monitoring/stats'),
  async download(
    meetingId: string,
    format: 'pdf' | 'docx' | 'xlsx',
    draft: boolean
  ) {
    const file = await request<{ blob: Blob; filename: string }>(
      `${minutes}/${id(meetingId)}/export/${format}?draft=${draft}`,
      {},
      30000,
      async (response) => {
        const header = response.headers.get('Content-Disposition') || '';
        const supplied = /filename="([^"]+)"/i.exec(header)?.[1];
        const filename = (
          supplied || `minutes-${draft ? 'latest' : 'approved'}.${format}`
        ).replace(/[\\/<>:"|?*\x00-\x1f]/g, '-');
        return { blob: await response.blob(), filename };
      }
    );
    const url = URL.createObjectURL(file.blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = file.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 30000);
  },
};
export const errorMessage = (error: unknown) =>
  error instanceof Error
    ? error.message
    : 'Something went wrong. Please try again.';
