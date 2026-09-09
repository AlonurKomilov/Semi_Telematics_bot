/**
 * The scrollport helpers, and the lane that keeps everyone on them.
 *
 * `scrollIntoView` moves EVERY scrollable ancestor. Most of this app's
 * are `overflow: hidden` — the shell, the chrome envelope, the content
 * card — and hidden does not mean unscrollable: it means the user
 * cannot scroll it back. One call to reveal a section pushed the whole
 * app up by the header and left it there until a reload, which is what
 * opening `/profile#modifications` from the avatar menu did.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { scrollportOf, scrollIntoScrollport, shellScrollport, SHELL_SCROLLPORT_ATTR } from './scrollport';

const SRC = join(__dirname, '..');
const src = (rel: string) =>
  readFileSync(join(SRC, rel), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

/** jsdom lays nothing out, so the metrics that decide "does this
 *  overflow" are stated here instead of measured. */
function box(overflowY: string, { scrollHeight = 0, clientHeight = 0 } = {}) {
  const el = document.createElement('div');
  el.style.overflowY = overflowY;
  Object.defineProperty(el, 'scrollHeight', { value: scrollHeight, configurable: true });
  Object.defineProperty(el, 'clientHeight', { value: clientHeight, configurable: true });
  return el;
}

afterEach(() => { document.body.innerHTML = ''; });

describe('which box gets scrolled', () => {
  it('is the nearest ancestor that really scrolls', () => {
    const outer = box('auto', { scrollHeight: 900, clientHeight: 300 });
    const inner = box('auto', { scrollHeight: 600, clientHeight: 200 });
    const target = document.createElement('p');
    outer.appendChild(inner); inner.appendChild(target); document.body.appendChild(outer);
    expect(scrollportOf(target)).toBe(inner);
  });

  it('never a hidden ancestor, however much it overflows', () => {
    // The shell, the envelope and the content card are all hidden — and
    // all scrollable by script. This is the whole bug.
    const hidden = box('hidden', { scrollHeight: 2000, clientHeight: 400 });
    const target = document.createElement('p');
    hidden.appendChild(target); document.body.appendChild(hidden);
    expect(scrollportOf(target)).toBeNull();
  });

  it('and nothing at all when nothing scrolls — the document is not a fallback', () => {
    const still = box('auto', { scrollHeight: 300, clientHeight: 300 });
    const target = document.createElement('p');
    still.appendChild(target); document.body.appendChild(still);
    expect(scrollportOf(target)).toBeNull();

    const before = document.documentElement.scrollTop;
    const spy = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
    scrollIntoScrollport(target);
    expect(spy, 'it fell back to scrolling the document').not.toHaveBeenCalled();
    expect(document.documentElement.scrollTop).toBe(before);
    spy.mockRestore();
  });

  it('moves that box and no other', () => {
    const outer = box('auto', { scrollHeight: 900, clientHeight: 300 });
    const inner = box('auto', { scrollHeight: 600, clientHeight: 200 });
    const target = document.createElement('p');
    outer.appendChild(inner); inner.appendChild(target); document.body.appendChild(outer);
    const innerTo = vi.fn(); const outerTo = vi.fn();
    Object.assign(inner, { scrollTo: innerTo }); Object.assign(outer, { scrollTo: outerTo });
    scrollIntoScrollport(target, { block: 'start', behavior: 'auto' });
    expect(innerTo).toHaveBeenCalledTimes(1);
    expect(outerTo, 'an ancestor was scrolled too — the shell moves that way').not.toHaveBeenCalled();
    expect(innerTo.mock.calls[0][0].top, 'a negative offset would fight the clamp').toBeGreaterThanOrEqual(0);
  });

  it('finds the page scrollport by the marker the shell puts on it', () => {
    expect(shellScrollport()).toBeNull();
    const port = box('auto', { scrollHeight: 900, clientHeight: 300 });
    port.setAttribute(SHELL_SCROLLPORT_ATTR, '');
    document.body.appendChild(port);
    expect(shellScrollport()).toBe(port);
    expect(src('shells/AppShell.tsx'), 'the shell stopped naming its scrollport')
      .toMatch(/SHELL_SCROLLPORT_ATTR/);
  });
});

describe('nobody reaches past their own box', () => {
  /** The only files allowed a raw scroll, each with the reason. */
  const RAW_OK: Record<string, string> = {
    'shells/DocumentLock.tsx': 'the reset itself — it clears a position the document already held',
    'features/applications/public/PublicApply.tsx': 'a public page: the document IS the scrollport there',
    'features/carrier-directory/PublicCarrierIntake.tsx': 'a public page, no shell above it',
  };
  const RAW = /\.scrollIntoView\(|window\.scrollTo\(/;

  const walk = (dir: string, acc: string[] = []): string[] => {
    for (const e of readdirSync(join(SRC, dir), { withFileTypes: true })) {
      const rel = dir ? `${dir}/${e.name}` : e.name;
      if (e.isDirectory()) walk(rel, acc);
      else if (/\.tsx?$/.test(e.name) && !e.name.includes('.test.')) acc.push(rel);
    }
    return acc;
  };

  it('every scroll inside the shell goes through the helper', () => {
    const files = walk('');
    expect(files.length).toBeGreaterThan(300);
    const raw = files.filter((f) => RAW.test(src(f)));
    expect(raw.length, 'no raw scrolls found at all — the pattern stopped matching')
      .toBeGreaterThan(1);
    expect(raw.filter((f) => !(f in RAW_OK)),
      'these scroll every ancestor, including the hidden ones the user cannot scroll back — use lib/scrollport')
      .toEqual([]);
  });

  it('and every exemption is still real', () => {
    expect(Object.keys(RAW_OK).filter((f) => !RAW.test(src(f))),
      'listed as allowed a raw scroll, but no longer does one — drop the entry').toEqual([]);
  });
});
