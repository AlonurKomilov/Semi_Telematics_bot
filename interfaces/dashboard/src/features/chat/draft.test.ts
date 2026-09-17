import { describe, expect, it, vi } from 'vitest';
import { DraftBuffer } from './draft';
import { ApiError } from './api';
import { deferred, draft } from './fixtures';
import type { Draft } from './types';
describe('draft compare-and-swap', () => {
  it('serializes typing during an in-flight save and never reuses the old version', async () => {
    const first = deferred<Draft>(); const save = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValueOnce({ ...draft, body: 'second', version: 2 });
    const buffer = new DraftBuffer(draft, save, () => {});
    buffer.update({ body: 'first', reply_to_id: null }); const pending = buffer.flush();
    buffer.update({ body: 'second', reply_to_id: null }); first.resolve({ ...draft, body: 'first', version: 1 }); await pending;
    expect(save.mock.calls.map(([p]) => [p.body, p.expected_version])).toEqual([['first',0], ['second',1]]); expect(buffer.status).toBe('saved');
  });
  it('a remote clear conflicts with dirty text instead of resurrecting it', async () => {
    const save = vi.fn(); const buffer = new DraftBuffer({ ...draft, body: 'old', version: 4 }, save, () => {});
    buffer.update({ body: 'unsaved', reply_to_id: null }); buffer.receive({ ...draft, version: 5 });
    expect(buffer.value.body).toBe('unsaved'); expect(buffer.status).toBe('conflict');
    await expect(buffer.flush()).rejects.toThrow(); expect(save).not.toHaveBeenCalled();
    buffer.useRemote({ ...draft, version: 5 }); expect(buffer.value.body).toBe(''); expect(buffer.status).toBe('saved');
  });
  it('an own-save journal echo arriving before HTTP is not a conflict', async () => {
    const response = deferred<Draft>(); const buffer = new DraftBuffer(draft, () => response.promise, () => {});
    buffer.update({ body: 'hello', reply_to_id: null }); const pending = buffer.flush();
    buffer.receive({ ...draft, body: 'hello', version: 1 }); response.resolve({ ...draft, body: 'hello', version: 1 }); await pending;
    expect(buffer.status).toBe('saved');
  });
  it('a failed CAS is never retried until the user resolves the conflict', async () => {
    const save = vi.fn().mockRejectedValue(new ApiError(409, 'stale', 'version_conflict'));
    const buffer = new DraftBuffer(draft, save, () => {}); buffer.update({ body: 'mine', reply_to_id: null });
    await expect(buffer.flush()).rejects.toThrow(); await expect(buffer.flush()).rejects.toThrow();
    expect(save).toHaveBeenCalledTimes(1); expect(buffer.value.body).toBe('mine');
  });
});
