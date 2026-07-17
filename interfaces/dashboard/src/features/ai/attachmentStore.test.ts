import { describe, it, expect, beforeEach } from 'vitest';
import {
  addPendingAttachment,
  bindConversationAttachments,
  clearConversationAttachments,
  loadConversationAttachments,
  loadPendingAttachments,
  removeConversationAttachment,
  type PendingAttachment,
} from './attachmentStore';

const KEY = '4truck:ai-attachments:v1';

function att(name: string, content = 'a,b\n1,2', t = Date.now()): PendingAttachment {
  return { name, content, kind: 'sheet', size: content.length, t };
}

describe('attachmentStore lifecycle', () => {
  beforeEach(() => localStorage.removeItem(KEY));

  it('migrates the v1 bare-array shape into pending', () => {
    localStorage.setItem(KEY, JSON.stringify([att('old.csv')]));
    const pending = loadPendingAttachments();
    expect(pending).toHaveLength(1);
    expect(pending[0].name).toBe('old.csv');
    expect(loadConversationAttachments(7)).toEqual([]);
  });

  it('binds sent files to a conversation and keeps newest 3 by name', () => {
    const files = [att('a.csv', 'x', 1), att('b.csv', 'y', 2)];
    expect(bindConversationAttachments(7, files)).toHaveLength(2);
    // Re-sending a.csv replaces it; two more push the oldest out.
    bindConversationAttachments(7, [att('a.csv', 'x2', 3)]);
    bindConversationAttachments(7, [att('c.csv', 'z', 4), att('d.csv', 'w', 5)]);
    const bound = loadConversationAttachments(7);
    expect(bound.map((f) => f.name)).toEqual(['a.csv', 'c.csv', 'd.csv']);
    // Other conversations are untouched.
    expect(loadConversationAttachments(8)).toEqual([]);
  });

  it('removes one bound file / clears the whole thread', () => {
    bindConversationAttachments(7, [att('a.csv'), att('b.csv')]);
    expect(removeConversationAttachment(7, 'a.csv').map((f) => f.name)).toEqual(['b.csv']);
    clearConversationAttachments(7);
    expect(loadConversationAttachments(7)).toEqual([]);
  });

  it('pending and convo share one budget — convo evicts first', () => {
    const big = 'x'.repeat(950_000);   // two of these exceed the 1.8MB budget
    bindConversationAttachments(7, [att('bound.csv', big, 1)]);
    const res = addPendingAttachment([], 'new.csv', big);
    expect('list' in res && res.list).toHaveLength(1);
    // The conversation-bound file (re-derivable) was evicted, not the
    // still-composing pending one.
    expect('evicted' in res && res.evicted).toContain('bound.csv');
    expect(loadConversationAttachments(7)).toEqual([]);
  });
});
