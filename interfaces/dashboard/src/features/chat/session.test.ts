import { afterEach, describe, expect, it, vi } from 'vitest';
import { waitFor } from '@testing-library/react';
import { ChatSession } from './session';
import { ApiError } from './api';
import { batch, conversation, deferred, fakeApi, message } from './fixtures';
import type { MessagePage } from './types';
const stores: ChatSession[] = [];
function store(api = fakeApi()) { const s = new ChatSession(api, null); stores.push(s); return s; }
afterEach(() => { stores.splice(0).forEach(s => s.stop()); vi.restoreAllMocks(); });
describe('snapshot and replay lifecycle', () => {
  it('checkpoints before reading snapshots, then replays the gap and deduplicates by current version', async () => {
    const order: string[] = [];
    const api = fakeApi({ events: vi.fn(async (_id, after) => { order.push(after === undefined ? 'checkpoint' : `replay:${after}`); return after === undefined ? batch({ cursor: 10, resync_required: true }) : batch({ cursor: 12, items: [{ id: 12, event_seq: 12, kind: 'message_edited', resource_version: 1, message: message(2, { version: 3, body: 'latest' }) }] }); }), messages: vi.fn(async () => { order.push('snapshot'); return { items: [message(2)], next_before: null }; }) });
    const s = store(api); s.select('room-a');
    await waitFor(() => expect(s.getSnapshot().room?.cursor).toBe(12));
    expect(order).toEqual(['checkpoint','snapshot','replay:10']); expect(s.getSnapshot().room?.messages).toEqual([message(2, { version: 3, body: 'latest' })]);
  });
  it('ignores a slow snapshot from a previously selected room', async () => {
    const old = deferred<MessagePage>(); const api = fakeApi({ messages: vi.fn((id: string) => id === 'room-a' ? old.promise : Promise.resolve({ items: [message(9)], next_before: null })) });
    const s = store(api); s.select('room-a'); await waitFor(() => expect(api.messages).toHaveBeenCalled()); s.select('room-b');
    old.resolve({ items: [message(1, { body: 'private old room' })], next_before: null });
    await waitFor(() => expect(s.getSnapshot().room?.conversation.id).toBe('room-b'));
    expect(s.getSnapshot().room?.messages.map(m => m.id)).toEqual([9]);
  });
  it('discards the old scope before rebuilding a rejoined membership', async () => {
    const s = store(); s.select('room-a'); await waitFor(() => expect(s.getSnapshot().room).not.toBeNull());
    const cp = batch({ cursor: 1, scope: { membership_id: 2, visible_after_message_seq: 1 } });
    const newPage = deferred<MessagePage>(); vi.mocked(s.api.messages).mockReturnValueOnce(newPage.promise);
    vi.mocked(s.api.events).mockResolvedValue(cp);
    const pending = s['apply'](cp, s['generation']); await waitFor(() => expect(s.getSnapshot().room).toBeNull());
    newPage.resolve({ items: [message(), message(2)], next_before: null }); await pending;
    expect(s.getSnapshot().room?.messages.map(m => m.id)).toEqual([2]); expect(s.getSnapshot().room?.epoch).toBe(2);
  });
  it('commits empty filtered scan cursors and updates caller rights without message events', async () => {
    const s = store(); s.select('room-a'); await waitFor(() => expect(s.getSnapshot().room).not.toBeNull());
    await s['apply'](batch({ cursor: 40, caller: { group_role: 'member', actions: [] } }), s['generation']);
    expect(s.getSnapshot().room?.cursor).toBe(40); expect(s.getSnapshot().room?.conversation.caller.actions).toEqual([]);
  });
  it('does not commit a cursor until dependent pin/state/draft reads succeed', async () => {
    const s = store(); s.select('room-a'); await waitFor(() => expect(s.getSnapshot().room).not.toBeNull());
    vi.mocked(s.api.draft).mockRejectedValueOnce(new Error('offline'));
    await expect(s['apply'](batch({ cursor: 1, items: [{ id: 1, event_seq: 1, kind: 'draft_changed', resource_version: 1 }] }), s['generation'])).rejects.toThrow();
    expect(s.getSnapshot().room?.cursor).toBe(0);
  });
  it('removes all protected content on denied authorization, including delayed inbox responses', async () => {
    const s = store(); s.select('room-a'); await waitFor(() => expect(s.getSnapshot().room).not.toBeNull());
    const pending = deferred<Awaited<ReturnType<typeof s.api.bootstrap>>>(); vi.mocked(s.api.bootstrap).mockReturnValueOnce(pending.promise);
    const request = s.refreshInbox();
    await expect(s.action(async () => { throw new ApiError(403, 'forbidden'); })).rejects.toThrow();
    pending.resolve({ items: [{ ...conversation, state: s.getSnapshot().room?.state!, unread_count: 0, unread_mentions: 0, has_draft: false, latest_message: message() }], next_after: null }); await request;
    expect(s.getSnapshot().room).toBeNull(); expect(s.getSnapshot().summaries).toEqual([]); expect(s.getSnapshot().error).toBe('unavailable');
  });
  it('does not apply an old write response to a new room or stopped actor', async () => {
    const s = store(); s.select('room-a'); await waitFor(() => expect(s.getSnapshot().room).not.toBeNull());
    const response = deferred<number>(); const apply = vi.fn(() => ({})); const write = s.action(() => response.promise, apply);
    s.select('room-b'); response.resolve(1); await expect(write).rejects.toMatchObject({ name: 'AbortError' }); expect(apply).not.toHaveBeenCalled();
    s.stop(); await expect(s.action(async () => 1)).rejects.toMatchObject({ name: 'AbortError' });
  });
  it('limits historical pages while maintaining a latest-history escape hatch', async () => {
    const s = store(fakeApi({ messages: vi.fn(async (_id, before) => ({ items: [message(before === undefined ? 10 : 2)], next_before: before === undefined ? 5 : null })) }));
    s.select('room-a'); await waitFor(() => expect(s.getSnapshot().room).not.toBeNull());
    await s.history(); expect(s.getSnapshot().room?.browsingHistory).toBe(true); expect(s.getSnapshot().room?.messages.map(m => m.id)).toEqual([2,10]);
    await s.history(true); expect(s.getSnapshot().room?.browsingHistory).toBe(false); expect(s.getSnapshot().room?.messages.map(m => m.id)).toEqual([10]);
  });
});

describe('socket transport and fallback', () => {
  class Socket {
    readyState = 1;
    onmessage: ((event: { data: string }) => void) | null = null;
    onclose: (() => void) | null = null;
    onerror: (() => void) | null = null;
    send = vi.fn();
    close = vi.fn(() => { this.readyState = 3; this.onclose?.(); });
    frame(value: object) { this.onmessage?.({ data: JSON.stringify(value) }); }
  }
  function connected() {
    const socket = new Socket(), api = fakeApi();
    const create = vi.fn((_url: string) => socket as unknown as WebSocket);
    const s = new ChatSession(api, create); stores.push(s); s.start();
    socket.frame({ type: 'ready', protocol: 1 }); return { s, api, socket, create };
  }
  it('subscribes before catch-up and falls back to HTTP after a socket failure', async () => {
    const { s, api, socket, create } = connected(); s.select('room-a');
    expect(JSON.parse(socket.send.mock.calls[socket.send.mock.calls.length - 1][0])).toEqual({ type: 'subscribe', conversations: [{ id: 'room-a' }] });
    expect(create.mock.calls[0]).toHaveLength(1); expect(create.mock.calls[0][0]).not.toMatch(/token|bearer|\?/i);
    await waitFor(() => expect(s.getSnapshot().room).not.toBeNull());
    socket.close(); await waitFor(() => expect(api.sync).toHaveBeenCalled()); expect(s.getSnapshot().connection).toBe('polling');
  });
  it('handles an empty live grant update and removes content immediately on removal', async () => {
    const { s, socket } = connected(); s.select('room-a'); await waitFor(() => expect(s.getSnapshot().room).not.toBeNull());
    socket.frame({ type: 'events', ...batch({ caller: { group_role: 'member', actions: [] } }) });
    await waitFor(() => expect(s.getSnapshot().room?.conversation.caller.actions).toEqual([]));
    socket.frame({ type: 'removed', conversation_id: 'room-a' });
    expect(s.getSnapshot().room).toBeNull(); expect(s.getSnapshot().summaries).toEqual([]);
  });
  it('pauses hidden tabs and removes listeners, sockets and timers when the actor unmounts', async () => {
    const { s, api, socket } = connected();
    const visibility = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    document.dispatchEvent(new Event('visibilitychange')); expect(socket.close).toHaveBeenCalled();
    const count = vi.mocked(api.sync).mock.calls.length; await s['poll'](true); expect(api.sync).toHaveBeenCalledTimes(count);
    s.stop(); visibility.mockReturnValue(false); document.dispatchEvent(new Event('visibilitychange'));
    expect(api.sync).toHaveBeenCalledTimes(count);
  });
});

it('a tombstone removes a cached draft quote even though draft version did not change', async () => {
  const s = store(fakeApi({ draft: vi.fn(async () => ({ body: 'unfinished', version: 1, reply: { status: 'available' as const, id: 1, author_id: 2, body: 'removed text' } })) }));
  s.select('room-a'); await waitFor(() => expect(s.getSnapshot().room).not.toBeNull());
  await s['apply'](batch({ cursor: 1, items: [{ id: 1, event_seq: 1, kind: 'message_deleted', resource_version: 2, message: message(1, { version: 2, body: null, deleted_at: 'today' }) }] }), s['generation']);
  expect(s.getSnapshot().room?.draft.reply).toEqual({ status: 'unavailable' });
  expect(s.getSnapshot().room?.draft.version).toBe(1);
});


it('clears transient errors after a recovered read but preserves access removal', async () => {
  const api = fakeApi(); vi.mocked(api.bootstrap).mockRejectedValueOnce(new TypeError('offline'));
  const s = store(api); await s.refreshInbox();
  expect(s.getSnapshot().error).toBe('connectionError');
  await s.refreshInbox(); expect(s.getSnapshot().error).toBeNull();
  s.select('room-a'); await waitFor(() => expect(s.getSnapshot().room).not.toBeNull());
  s['fail'](new TypeError('offline'));
  await s['apply'](batch(), s['generation']); expect(s.getSnapshot().error).toBeNull();
  s['remove'](); await s.refreshInbox();
  expect(s.getSnapshot().error).toBe('unavailable'); expect(s.getSnapshot().room).toBeNull();
});
