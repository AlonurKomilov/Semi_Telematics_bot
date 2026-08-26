/**
 * apiJSON takes an OBJECT body. Hand it a string and the request breaks.
 *
 * `apiFetch` does two things in one branch:
 *
 *     if (body && typeof body === 'object' && … && typeof body !== 'string') {
 *       headers['Content-Type'] = 'application/json';
 *       body = JSON.stringify(body);
 *     }
 *
 * A body that is ALREADY a string skips both — it leaves as untyped
 * text with no JSON content type, and FastAPI answers 422 "Input should
 * be a valid dictionary or object to extract fields from". The call
 * looks completely reasonable at the site; the mistake is invisible
 * until someone presses the button.
 *
 * Four call sites shipped that way. Two of them were the answers to a
 * device-identity question — "Same truck" and "Different truck…" — so
 * the entire resolution flow had never once worked in production, and
 * nothing said so because nobody had needed to answer one yet. The
 * other two were creating and editing a part.
 *
 * The raw `fetch` calls in the public application flow pre-stringify
 * too, and they are correct: they set Content-Type themselves. So the
 * rule is not "never stringify" — it is "not when the helper is going
 * to do it for you".
 */
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC = join(__dirname, '..');

/**
 * An apiJSON/apiFetch/apiJSONAI call whose options carry a
 * pre-stringified body.
 *
 * Anchored on the helper NAME so the raw-fetch callers (which set
 * their own header, and must keep stringifying) are not swept up.
 */
const CALL = /\bapi(?:JSON|Fetch|JSONAI)\s*(?:<[^>]*>)?\s*\(/g;

/**
 * The options object of the call starting at `from`, brace-balanced.
 *
 * Reading a fixed window instead was the first attempt and it was
 * wrong in the direction that matters: `headers:` usually sits AFTER
 * `body:`, so a window ending at the body never saw the header and the
 * guard accused the one call site that is doing it correctly. A
 * too-long window has the opposite failure — a neighbouring call's
 * header would excuse a real offender — and a guard with false
 * negatives is the kind everyone trusts.
 */
function optionsBlock(src: string, from: number): string {
  // NOT simply the next `{`: these paths are template literals, so the
  // first brace after `apiJSON(` is usually the `${…}` inside the URL.
  // Reading that as the options object made the guard scan `{e.id}`,
  // find no body, and pass — on the exact call that shipped broken.
  // A `${` is the one brace that is never an options object, and its
  // own `}` keeps the balance below correct either way.
  let open = -1;
  for (let i = from; i < src.length; i++) {
    if (src[i] === '{' && src[i - 1] !== '$') { open = i; break; }
  }
  if (open === -1) return '';
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) return src.slice(open, i + 1);
  }
  return src.slice(open);
}

/** Pre-stringified, and not compensating with an explicit header. */
function offends(block: string): boolean {
  return /body:\s*JSON\.stringify\s*\(/.test(block)
    && !/Content-Type/i.test(block);
}

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path, out);
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(path);
  }
  return out;
}

describe('request bodies', () => {
  it('are objects at every apiJSON call site, never pre-stringified', () => {
    const offenders = walk(SRC)
      .filter((f) => {
        const src = readFileSync(f, 'utf8');
        // A call that sets the header itself is doing apiFetch's job
        // deliberately and still sends a parseable request.
        return [...src.matchAll(CALL)]
          .some((m) => offends(optionsBlock(src, m.index!)));
      })
      .map((f) => relative(SRC, f));
    expect(offenders).toEqual([]);
  });

  it('would catch the shape that shipped', () => {
    // The exact call that 422'd, so this guard is not merely asserting
    // the absence of something it cannot recognise.
    const shipped = `
      await apiJSON(\`/vehicles/device-events/\${e.id}/resolve\`, {
        method: 'POST', body: JSON.stringify({ action }),
      });`;
    const hits = (src: string) => [...src.matchAll(CALL)]
      .filter((m) => offends(optionsBlock(src, m.index!))).length;
    expect(hits(shipped)).toBe(1);

    // ...and leaves the deliberate, header-setting version alone —
    // the header sits AFTER the body, which is what the first version
    // of this guard could not see.
    expect(hits(`
      apiJSON(path(key), {
        method: 'PUT',
        body: JSON.stringify({ value: raw }),
        headers: { 'Content-Type': 'application/json' },
      });`)).toBe(0);
  });
});
