import { describe, expect, it } from 'vitest';
import { canMarkRead, mergeMessages, sameScope, sendPayload } from './model';
import { chatSocketURL } from './api';
import { message } from './fixtures';
describe('current visible message projections', () => {
  it('replay overlap cannot resurrect an edit or tombstone', () => {
    const deleted = message(1, { version: 3, body: null, deleted_at: '2026-09-16' });
    expect(mergeMessages([deleted], [message(1), message(1, { version: 2, body: 'old edit' })])).toEqual([deleted]);
  });
  it('updates or removes cached quotes even when the reply version is unchanged', () => {
    const reply = message(2, { reply: { status: 'available', id: 1, author_id: 2, body: 'old' } });
    expect(mergeMessages([reply], [message(1, { version: 2, body: 'new' })])[1].reply).toMatchObject({ body: 'new' });
    expect(mergeMessages([reply], [message(1, { version: 3, body: null, deleted_at: 'today' })])[1].reply).toEqual({ status: 'unavailable' });
  });
  it('filters a new visibility boundary and bounds mounted history', () => {
    const rows = Array.from({ length: 250 }, (_, i) => message(i + 1));
    expect(mergeMessages([], rows)).toHaveLength(100);
    expect(mergeMessages(rows, [], 240).map(m => m.id)).toEqual([241,242,243,244,245,246,247,248,249,250]);
  });
  it('membership interval changes count even when the boundary does not', () => {
    expect(sameScope({ membership_id: 1, visible_after_message_seq: 0 }, { membership_id: 2, visible_after_message_seq: 0 })).toBe(false);
  });
});
describe('send and read semantics', () => {
  it('freezes a normalized idempotency payload without mutating the mention selection', () => {
    const ids = [9, 2, 9]; const p = sendPayload('  e\u0301  ', 4, ids, 'retry-key');
    expect(p).toEqual({ body: 'é', reply_to_id: 4, mentions: [2, 9], client_message_id: 'retry-key' }); expect(ids).toEqual([9,2,9]);
  });
  it('only marks visible, focused, bottom-of-latest history as read', () => {
    expect(canMarkRead(true, true, true, false)).toBe(true);
    for (const args of [[false,true,true,false],[true,false,true,false],[true,true,false,false],[true,true,true,true]]) expect(canMarkRead(...args as [boolean,boolean,boolean,boolean])).toBe(false);
  });
  it('uses the configured API root with cookie-only socket URLs', () => {
    expect(chatSocketURL('/api', 'https://fleet.test')).toBe('wss://fleet.test/api/chat/ws');
    expect(chatSocketURL('https://api.test', 'https://fleet.test')).toBe('wss://api.test/chat/ws');
  });
});
