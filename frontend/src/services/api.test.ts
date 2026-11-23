import { afterEach, describe, it, expect, vi } from 'vitest';
import { api, ApiError, recovery } from './api';
afterEach(() => vi.unstubAllGlobals());
describe('typed API integration', () => {
  it('submits a real processing payload to the existing endpoint', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ meeting_id: 'm-1', status: 'success' }), {
          headers: { 'content-type': 'application/json' },
        })
      );
    vi.stubGlobal('fetch', fetch);
    expect(
      (await api.process({ transcript_text: 'A real transcript.' })).meeting_id
    ).toBe('m-1');
    expect(fetch.mock.calls[0][0]).toContain('/api/meetings/process');
    expect(JSON.parse(fetch.mock.calls[0][1].body).transcript_text).toBe(
      'A real transcript.'
    );
  });
  it('preserves saved meeting IDs on partial failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({
              detail: {
                code: 'task_processing_partial_failure',
                meeting_id: 'saved',
              },
            }),
            { status: 502, headers: { 'content-type': 'application/json' } }
          )
        )
    );
    try {
      await api.process({ transcript_text: 'source' });
      throw new Error('Expected request to fail');
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).detail.meeting_id).toBe('saved');
    }
  });
  it('sends optimistic concurrency with task updates', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        new Response('{}', { headers: { 'content-type': 'application/json' } })
      );
    vi.stubGlobal('fetch', fetch);
    await api.status('meeting', 'T1', 'completed', 3);
    expect(fetch.mock.calls[0][1].method).toBe('PATCH');
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toMatchObject({
      expected_revision: 3,
      status: 'completed',
    });
  });
  it('shows connection errors without returning mock data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')));
    await expect(api.meetings()).rejects.toThrow('Cannot reach the backend');
  });
});

it('calls the persisted meeting deletion endpoint', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue(
      new Response('{"status":"deleted","meeting_id":"m"}', {
        headers: { 'content-type': 'application/json' },
      })
    );
  vi.stubGlobal('fetch', fetch);
  await api.deleteMeeting('m');
  expect(fetch.mock.calls[0][0]).toContain('/api/meetings/m');
  expect(fetch.mock.calls[0][1].method).toBe('DELETE');
});

it('downloads the selected XLSX using its server filename and revokes the blob URL', async () => {
  vi.useFakeTimers();
  const create = vi.fn().mockReturnValue('blob:meeting');
  const revoke = vi.fn();
  const createDescriptor = Object.getOwnPropertyDescriptor(
    URL,
    'createObjectURL'
  );
  const revokeDescriptor = Object.getOwnPropertyDescriptor(
    URL,
    'revokeObjectURL'
  );
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    value: create,
  });
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    value: revoke,
  });
  let filename = '';
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, 'click')
    .mockImplementation(function (this: HTMLAnchorElement) {
      filename = this.download;
    });
  const fetch = vi
    .fn()
    .mockResolvedValue(
      new Response(new Uint8Array([80, 75, 3, 4]), {
        headers: {
          'content-type':
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          'content-disposition':
            'attachment; filename="minutes-selected-r3-approved.xlsx"',
        },
      })
    );
  vi.stubGlobal('fetch', fetch);
  try {
    await api.download('selected meeting', 'xlsx', false);
    expect(fetch.mock.calls[0][0]).toContain(
      '/selected%20meeting/export/xlsx?draft=false'
    );
    expect(filename).toBe('minutes-selected-r3-approved.xlsx');
    expect(create).toHaveBeenCalledOnce();
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledWith('blob:meeting');
  } finally {
    click.mockRestore();
    vi.useRealTimers();
    if (createDescriptor)
      Object.defineProperty(URL, 'createObjectURL', createDescriptor);
    else Reflect.deleteProperty(URL, 'createObjectURL');
    if (revokeDescriptor)
      Object.defineProperty(URL, 'revokeObjectURL', revokeDescriptor);
    else Reflect.deleteProperty(URL, 'revokeObjectURL');
  }
});

it('uploads multipart audio without overriding the browser boundary', async () => {
  const fetch = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        transcript: [
          {
            text: 'Hello',
            speaker: null,
            timestamp: '00:00:01',
            source: 'audio',
          },
        ],
        speaker_mode: 'manual',
      }),
      { headers: { 'content-type': 'application/json' } }
    )
  );
  vi.stubGlobal('fetch', fetch);
  const file = new File(['audio'], 'meeting.mp3', { type: 'audio/mpeg' });
  const result = await api.transcribe(file);
  expect(result.transcript[0].speaker).toBeNull();
  expect(fetch.mock.calls[0][0]).toContain('/api/meetings/transcribe');
  const options = fetch.mock.calls[0][1];
  expect(options.body).toBeInstanceOf(FormData);
  expect(options.body.get('file')).toBe(file);
  expect(options.headers['Content-Type']).toBeUndefined();
});

it('shows an actionable audio error without starting meeting processing', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: 'audio_no_speech',
            message: 'No speech was detected.',
          },
        }),
        { status: 422 }
      )
    )
  );
  await expect(api.transcribe(new File([''], 'silent.wav'))).rejects.toThrow(
    'No speech was detected.'
  );
});

describe('long minutes generation', () => {
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
  const route = (handlers: Array<(url: string, init?: RequestInit) => Response | Promise<Response>>) => {
    const calls: string[] = [];
    let i = 0;
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push((init?.method || 'GET') + ' ' + url.replace(/^.*\/api\/v1\/minutes/, ''));
      return handlers[Math.min(i++, handlers.length - 1)](url, init);
    });
    vi.stubGlobal('fetch', fetch);
    return calls;
  };

  it('waits for the saved minutes when the connection drops, without re-sending the request', async () => {
    recovery.pollMs = 1;
    const calls = route([
      () => json({ detail: { code: 'minutes_not_found' } }, 404),       // nothing saved yet
      () => { throw new TypeError('Failed to fetch'); },                // the long request is dropped
      () => json({ detail: { code: 'minutes_not_found' } }, 404),       // still working
      () => json({ meeting_id: 'm1', revision: 1 }),                    // saved
    ]);
    const result = await api.generate('m1');
    expect(result.revision).toBe(1);
    expect(calls.filter((c) => c.startsWith('POST'))).toHaveLength(1);
  });

  it('does not treat a normal server error as a dropped connection', async () => {
    recovery.pollMs = 1;
    route([
      () => json({ detail: { code: 'minutes_not_found' } }, 404),
      () => json({ detail: { code: 'invalid_transcript_or_configuration' } }, 422),
    ]);
    await expect(api.generate('m1')).rejects.toMatchObject({ status: 422 });
  });

  it('regenerate returns only a revision newer than the one it started from', async () => {
    recovery.pollMs = 1;
    route([
      () => json({ meeting_id: 'm1', revision: 3 }),                    // existing minutes
      () => { throw new TypeError('Failed to fetch'); },
      () => json({ meeting_id: 'm1', revision: 3 }),                    // not new yet
      () => json({ meeting_id: 'm1', revision: 4 }),                    // the regenerated one
    ]);
    expect((await api.regenerate('m1')).revision).toBe(4);
  });

  it('shows a real provider error at once instead of waiting for minutes that will never come', async () => {
    recovery.pollMs = 1;
    const calls = route([
      () => json({ detail: { code: 'minutes_not_found' } }, 404),
      () => json({ detail: { code: 'provider_authentication' } }, 502),         // our server answered: a real failure
    ]);
    await expect(api.generate('m1')).rejects.toMatchObject({ status: 502 });
    expect(calls).toHaveLength(2);                                              // no polling happened
  });

  it('gives up after the wait limit and shows the original error', async () => {
    recovery.pollMs = 1;
    recovery.maxWaitMs = 30;
    route([
      () => json({ detail: { code: 'minutes_not_found' } }, 404),
      () => { throw new TypeError('Failed to fetch'); },
      () => json({ detail: { code: 'minutes_not_found' } }, 404),
    ]);
    await expect(api.generate('m1')).rejects.toBeInstanceOf(ApiError);
    recovery.maxWaitMs = 10 * 60 * 1000;
  });
});
