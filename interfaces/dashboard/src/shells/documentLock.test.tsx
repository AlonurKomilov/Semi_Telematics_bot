/**
 * The dashboard document does not scroll, and nothing in flow sits
 * above the shell.
 *
 * Two halves of one guarantee. The LOCK: while a shell is mounted the
 * document element carries the class the stylesheet hides overflow on,
 * and it lets go when the shell unmounts — the public pages render no
 * shell and must scroll like ordinary documents. The SHAPE: the shell
 * is exactly one viewport tall, so anything in flow above it makes the
 * document taller than the window; and a document scroll, once picked
 * up, survives every client-side navigation after it, because this app
 * has no scroll restoration. That is how the header and the sidebar's
 * logo row ended up above the fold twice in one day.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { DocumentLock, DOCUMENT_LOCK_CLASS } from './DocumentLock';

const SRC = join(__dirname, '..');
const src = (rel: string) =>
  readFileSync(join(SRC, rel), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

afterEach(() => {
  cleanup();
  document.documentElement.classList.remove(DOCUMENT_LOCK_CLASS);
});

describe('the lock', () => {
  it('holds the document while a shell is mounted, and lets go after', () => {
    expect(document.documentElement.classList.contains(DOCUMENT_LOCK_CLASS)).toBe(false);
    const view = render(<DocumentLock />);
    expect(document.documentElement.classList.contains(DOCUMENT_LOCK_CLASS),
      'the document is not locked while the shell is up').toBe(true);
    view.unmount();
    expect(document.documentElement.classList.contains(DOCUMENT_LOCK_CLASS),
      'the lock outlived the shell — the public pages would stop scrolling').toBe(false);
  });

  it('clears a position the document already held', () => {
    // Hiding overflow does not undo a scroll: a shell mounting at 200
    // would sit there with its header off-screen and no way back.
    const scrolled: number[] = [];
    const spy = (x: number, y: number) => { scrolled.push(y); };
    const original = window.scrollTo;
    (window as unknown as { scrollTo: typeof spy }).scrollTo = spy;
    render(<DocumentLock />);
    (window as unknown as { scrollTo: typeof original }).scrollTo = original;
    expect(scrolled, 'the lock never resets the position it inherited').toEqual([0]);
  });

  it('and the stylesheet answers for the class', () => {
    const css = readFileSync(join(SRC, 'index.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const rule = new RegExp(
      `html\\.${DOCUMENT_LOCK_CLASS},\\s*html\\.${DOCUMENT_LOCK_CLASS} body \\{([^}]*)\\}`,
    ).exec(css)?.[1] ?? '';
    expect(rule, `no rule for .${DOCUMENT_LOCK_CLASS} — the class would be decoration`).not.toBe('');
    expect(rule).toMatch(/overflow:\s*hidden/);
    expect(rule).toMatch(/height:\s*100%/);
    // Never global: the apply, status and carrier-intake pages are
    // ordinary documents, and they render no shell.
    expect(css, 'the document is locked for every page, shell or not')
      .not.toMatch(/^\s*(html|body)\s*\{[^}]*overflow:\s*hidden/m);
  });
});

describe('nothing in flow sits above the shell', () => {
  it('the shell mounts the lock and carries the banner itself', () => {
    const shell = src('shells/AppShell.tsx');
    expect(shell, 'the shell does not mount the lock').toMatch(/<DocumentLock \/>/);
    expect(shell, 'the invite banner is not inside the shell').toMatch(/<PendingInviteBanner \/>/);
    // A column: the banner is a ROW of the viewport box, not something
    // stacked on top of a box that is already the whole viewport.
    expect(shell, 'the shell root is not a column — a banner row would push the work out of view')
      .toMatch(/className="flex flex-col h-screen overflow-hidden/);
  });

  it('nothing inside the shell claims the viewport — the shell owns it', () => {
    // A `h-screen` child is exactly 100vh no matter what rows sit above
    // it, so with the banner up its foot lands outside the shell and the
    // shell's own `overflow-hidden` cuts it off. Everything inside sizes
    // to its parent; only the shell root names the viewport.
    const INSIDE = ['shells/AppShell.tsx', 'components/Sidebar.tsx', 'components/PendingInviteBanner.tsx'];
    for (const f of INSIDE) {
      const hits = [...src(f).matchAll(/\b(h-screen|min-h-screen|h-\[100vh\])\b/g)].map((m) => m[1]);
      const allowed = f === 'shells/AppShell.tsx' ? 1 : 0;
      expect(hits.length, `${f} claims the viewport ${hits.length}× (allowed ${allowed}): ${hits.join(', ')}`)
        .toBe(allowed);
    }
  });

  it('App renders no element of its own height beside the router', () => {
    const app = src('App.tsx');
    // The authed return only — the guard above it answers `<Login />`.
    const gate = app.indexOf('if (!user) return <Login />;');
    const authed = app.slice(app.indexOf('return (', gate));
    const tags = [...authed.matchAll(/<([A-Z][A-Za-z]*)\s*\/>/g)].map((m) => m[1]).sort();
    // LiveAlertWatcher renders null and raises toasts; the router is the
    // shell. Anything else added here has to prove it costs no height,
    // which is exactly the mistake this test exists to catch.
    expect(tags, 'a new sibling of the router — put it inside the shell instead')
      .toEqual(['AppRouter', 'LiveAlertWatcher']);
  });
});
