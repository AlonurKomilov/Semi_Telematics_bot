import { describe, it, expect, beforeEach } from 'vitest';
import {
  thoughtKey,
  saveThought,
  getThought,
  deleteThoughtsForConversation,
  clearThoughtStore,
} from './thoughtStore';

// Thought logs are browser-local by policy — the server stores only text
// + tier label.  This store re-attaches them to messages loaded from the
// server via a content-derived key.

describe('thoughtStore', () => {
  beforeEach(() => clearThoughtStore());

  it('round-trips reasoning + process by key', () => {
    const key = thoughtKey(12, 'Truck 228 is overdue by 780 miles.');
    saveThought(key, {
      reasoning: 'We are given fleet data…',
      process: [{ type: 'tool', name: 't', label: 'T', result: '{"n":1}' }],
    });
    const got = getThought(key)!;
    expect(got.reasoning).toBe('We are given fleet data…');
    expect(got.process![0].label).toBe('T');
  });

  it('round-trips suggestion chips (suggestions-only entries too)', () => {
    const key = thoughtKey(5, 'answer with chips');
    saveThought(key, { suggestions: ['Show due soon tasks', 'Fuel cost overview'] });
    expect(getThought(key)!.suggestions).toEqual(
      ['Show due soon tasks', 'Fuel cost overview'],
    );
  });

  it('key is stable for the same conversation + answer, distinct otherwise', () => {
    const a = thoughtKey(12, 'same answer text');
    expect(thoughtKey(12, 'same answer text')).toBe(a);
    expect(thoughtKey(13, 'same answer text')).not.toBe(a);
    expect(thoughtKey(12, 'different answer')).not.toBe(a);
  });

  it('key ignores text beyond the DB truncation window', () => {
    // The server caps stored answers at 2000 chars; the key hashes only
    // the first 300 so live-vs-loaded text always agree.
    const head = 'x'.repeat(300);
    expect(thoughtKey(1, head + 'live tail')).toBe(thoughtKey(1, head + 'truncated'));
  });

  it('empty payloads are not stored', () => {
    const key = thoughtKey(1, 'a');
    saveThought(key, {});
    expect(getThought(key)).toBeNull();
  });

  it('deleteThoughtsForConversation removes only that thread', () => {
    saveThought(thoughtKey(1, 'answer one'), { reasoning: 'r1' });
    saveThought(thoughtKey(2, 'answer two'), { reasoning: 'r2' });
    deleteThoughtsForConversation(1);
    expect(getThought(thoughtKey(1, 'answer one'))).toBeNull();
    expect(getThought(thoughtKey(2, 'answer two'))).not.toBeNull();
  });

  it('LRU-prunes past the entry cap instead of growing forever', () => {
    for (let i = 0; i < 230; i++) {
      saveThought(thoughtKey(9, `answer ${i}`), { reasoning: `r${i}` });
    }
    // Newest survive, oldest evicted (cap is 200).
    expect(getThought(thoughtKey(9, 'answer 229'))).not.toBeNull();
    expect(getThought(thoughtKey(9, 'answer 0'))).toBeNull();
  });
});
