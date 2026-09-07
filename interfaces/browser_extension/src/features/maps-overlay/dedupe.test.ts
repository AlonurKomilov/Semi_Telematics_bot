import { describe, expect, it, vi } from 'vitest';
import { makeShared } from './dedupe';

const TOKEN = 'jwt-for-the-connected-account';
const OTHER = 'jwt-for-somebody-else';

function clock(): { now: () => number; advance: (ms: number) => void } {
  let t = 1000;
  return { now: () => t, advance: (ms) => { t += ms; } };
}

describe('makeShared', () => {
  it('joins askers that arrive while the request is in the air', async () => {
    let resolve!: (v: string) => void;
    const fetcher = vi.fn(() => new Promise<string>((r) => { resolve = r; }));
    const shared = makeShared<string>(1000);

    const all = Promise.all([shared('k', TOKEN, fetcher), shared('k', TOKEN, fetcher), shared('k', TOKEN, fetcher)]);
    resolve('positions');
    expect(await all).toEqual(['positions', 'positions', 'positions']);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('reuses an answer inside its window and asks again after it', async () => {
    const c = clock();
    const fetcher = vi.fn(async () => 'positions');
    const shared = makeShared<string>(4000, c.now);

    await shared('k', TOKEN, fetcher);
    c.advance(3999);
    await shared('k', TOKEN, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(1);
    c.advance(2);
    await shared('k', TOKEN, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('keeps separate questions apart', async () => {
    const fetcher = vi.fn(async () => 'x');
    const shared = makeShared<string>(9999);
    await shared('live', TOKEN, fetcher);
    await shared('list', TOKEN, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('never remembers a failure, and hands it to everyone waiting', async () => {
    let reject!: (e: Error) => void;
    const fetcher = vi.fn(() => new Promise<string>((_, r) => { reject = r; }));
    const shared = makeShared<string>(9999);

    const a = shared('k', TOKEN, fetcher), b = shared('k', TOKEN, fetcher);
    reject(new Error('offline'));
    await expect(a).rejects.toThrow('offline');
    await expect(b).rejects.toThrow('offline');

    // The next poll asks again rather than replaying the failure.
    const ok = vi.fn(async () => 'positions');
    expect(await shared('k', TOKEN, ok)).toBe('positions');
    expect(ok).toHaveBeenCalledTimes(1);
  });

  it('never hands one connection an answer fetched for another', async () => {
    const c = clock();
    const fetcher = vi.fn(async () => 'positions');
    const shared = makeShared<string>(9999, c.now);

    await shared('k', TOKEN, fetcher);
    // Same instant, still well inside the window — but a different
    // connection, so the memory is not theirs to read.
    await shared('k', OTHER, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(2);
    // ...and the first connection's copy is gone, not merely bypassed.
    await shared('k', TOKEN, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it('does not record an answer whose connection ended while it was in the air', async () => {
    let resolve!: (v: string) => void;
    const slow = vi.fn(() => new Promise<string>((r) => { resolve = r; }));
    const fast = vi.fn(async () => 'theirs');
    const shared = makeShared<string>(9999);

    const inAir = shared('k', TOKEN, slow);
    // The panel disconnected and reconnected while that was running.
    await shared('k', OTHER, fast);
    resolve('mine');
    expect(await inAir).toBe('mine');          // its own asker still gets it

    // The new connection's memory is untouched by the old answer.
    expect(await shared('k', OTHER, fast)).toBe('theirs');
    expect(fast).toHaveBeenCalledTimes(1);
  });

  it('an ended flight does not evict the new connection\'s own request', async () => {
    let resolveOld!: (v: string) => void;
    const old = vi.fn(() => new Promise<string>((r) => { resolveOld = r; }));
    let resolveNew!: (v: string) => void;
    const fresh = vi.fn(() => new Promise<string>((r) => { resolveNew = r; }));
    const shared = makeShared<string>(9999);

    const before = shared('k', TOKEN, old);
    const after = shared('k', OTHER, fresh);
    resolveOld('mine');
    await before;
    // A third tab joins the request already in the air rather than
    // starting a second one.
    const joined = shared('k', OTHER, fresh);
    resolveNew('theirs');
    expect(await joined).toBe('theirs');
    expect(await after).toBe('theirs');
    expect(fresh).toHaveBeenCalledTimes(1);
  });
});
