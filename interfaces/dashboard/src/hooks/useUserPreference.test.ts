import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { useUserPreference } from './useUserPreference';
import { apiJSON } from '../api/client';

// The hook's only I/O is apiJSON (GET/PUT/DELETE) + localStorage (jsdom).
vi.mock('../api/client', () => ({ apiJSON: vi.fn() }));
const mockApi = apiJSON as unknown as Mock;

const putCalls = () =>
  mockApi.mock.calls.filter((c) => (c[1] as { method?: string } | undefined)?.method === 'PUT');

describe('useUserPreference — first-mutation persistence', () => {
  beforeEach(() => {
    localStorage.clear();
    mockApi.mockReset();
    // GET (no opts) → the server value; writes (opts.method) → ok.
    mockApi.mockImplementation((_url: string, opts?: { method?: string }) =>
      Promise.resolve(opts?.method ? {} : { value: '' }));
  });

  it('PUTs the FIRST mutation when the server had no prior entry (the saved-tab regression)', async () => {
    // Server returns no entry — exactly a grid that has never persisted a tab.
    const { result } = renderHook(() =>
      useUserPreference<{ id: string }[]>('table.x.views', []),
    );
    // Let the initial fetch settle (this is where the skip flag must clear).
    await waitFor(() => expect(result.current.hydrated).toBe(true));

    act(() => { result.current.setValue([{ id: 't1' }]); });

    // The debounced PUT must reach the server — before the fix this was
    // swallowed by the stale initial skip flag and only hit localStorage.
    await waitFor(() => {
      const put = putCalls()[0];
      expect(put).toBeTruthy();
      expect(put![0]).toBe('/user/preferences/ui/table.x.views');
      expect(JSON.parse((put![1] as { body: string }).body).value)
        .toBe(JSON.stringify([{ id: 't1' }]));
    });
  });

  it('still swallows the echo of a freshly-applied server value, then persists the next real edit', async () => {
    // Server already holds a value that differs from the empty fallback.
    mockApi.mockImplementation((_url: string, opts?: { method?: string }) =>
      Promise.resolve(opts?.method ? {} : { value: JSON.stringify([{ id: 'server' }]) }));

    const { result } = renderHook(() =>
      useUserPreference<{ id: string }[]>('table.z.views', []),
    );
    await waitFor(() => expect(result.current.value).toEqual([{ id: 'server' }]));

    // A consumer echoing the just-fetched value back must NOT PUT.
    act(() => { result.current.setValue([{ id: 'server' }]); });
    await new Promise((r) => setTimeout(r, 500));
    expect(putCalls().length).toBe(0);

    // …but the genuine edit right after does persist.
    act(() => { result.current.setValue([{ id: 'edited' }]); });
    await waitFor(() => expect(putCalls().length).toBeGreaterThan(0));
  });
});
