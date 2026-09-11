/**
 * Which failures announce an outage, and — far more important — which
 * must not.
 *
 * React Query aborts requests constantly: on every unmount, on every
 * refetch that supersedes an in-flight one. If an abort reached the
 * maintenance channel, a perfectly healthy dashboard would be covered
 * by an outage card several times a minute. That is the regression this
 * file exists to make impossible.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { apiFetch } from './client';

function listen() {
  const seen: string[] = [];
  const on = (e: Event) => {
    seen.push((e as CustomEvent<{ reason?: string }>).detail?.reason ?? 'bare');
  };
  window.addEventListener('4truck:maintenance', on);
  return { seen, stop: () => window.removeEventListener('4truck:maintenance', on) };
}

describe('what reaches the maintenance channel', () => {
  let ear: ReturnType<typeof listen>;
  beforeEach(() => { ear = listen(); });
  afterEach(() => { ear.stop(); vi.unstubAllGlobals(); });

  it('announces a gateway failure as an update', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 502 })));
    await apiFetch('/thing').catch(() => { /* the rejection is the point; the test listens for what was announced */ });
    expect(ear.seen).toEqual(['updating']);
  });

  it('announces a dead connection as unreachable, not as an update', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await apiFetch('/thing').catch(() => { /* the rejection is the point; the test listens for what was announced */ });
    expect(ear.seen).toEqual(['unreachable']);
  });

  it('says NOTHING when a request is merely cancelled', async () => {
    // The dangerous one: React Query does this on every unmount.
    const abort = new DOMException('The operation was aborted.', 'AbortError');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(abort));
    await apiFetch('/thing').catch(() => { /* the rejection is the point; the test listens for what was announced */ });
    expect(ear.seen, 'an abort must never put an outage card over a healthy app').toEqual([]);
  });

  it('says nothing about an ordinary application error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 403 })));
    await apiFetch('/thing').catch(() => { /* the rejection is the point; the test listens for what was announced */ });
    expect(ear.seen).toEqual([]);
  });
});
