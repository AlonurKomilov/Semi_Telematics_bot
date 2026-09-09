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
import { readFileSync, readdirSync } from 'node:fs';
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

  /**
   * Only these may name the viewport, each with the reason it is not
   * inside the shell. A `h-screen` child of the shell is exactly 100vh
   * however many rows sit above it, so its foot lands outside the box
   * and the shell's own `overflow-hidden` cuts it off — that is how the
   * sidebar lost its last nav items the moment a banner appeared, with
   * its own scroller powerless because the overflow was the root's.
   *
   * A list rather than a folder rule: `pages/` and `features/` hold both
   * kinds, and which side of the shell a route renders on is decided in
   * `router.tsx`, not by where the file lives.
   */
  const VIEWPORT_OK: Record<string, string> = {
    'shells/AppShell.tsx': 'the shell root — the one box that IS the viewport',
    'App.tsx': 'the pre-shell states (checking session, loading, error) render INSTEAD of a shell',
    'components/ui/dialog.tsx': 'a max-height on a fixed overlay, not a box in the document',
    'features/inspections/MediaGallery.tsx': 'the lightbox is a fixed dialog filling the window',
    'features/applications/ApplyPreview.tsx': 'routed OUTSIDE the shell so it renders exactly like the real form',
    'features/applications/public/ApplyStatus.tsx': 'a public page, no shell',
    'features/applications/public/PublicApply.tsx': 'a public page, no shell',
    'features/carrier-directory/PublicCarrierIntake.tsx': 'a public page, no shell',
    'pages/CompleteSetup.tsx': 'signed out or mid-setup, no shell',
    'pages/ExtensionConnect.tsx': 'a consent page outside the shell',
    'pages/ForgotPassword.tsx': 'signed out, no shell',
    'pages/Login.tsx': 'signed out, no shell',
    'pages/ResetPassword.tsx': 'signed out, no shell',
    'pages/VerifyEmail.tsx': 'signed out, no shell',
  };

  const CLAIM = /\b(h-screen|min-h-screen)\b|100vh|100dvh/;

  const walk = (dir: string, acc: string[] = []): string[] => {
    for (const e of readdirSync(join(SRC, dir), { withFileTypes: true })) {
      const rel = dir ? `${dir}/${e.name}` : e.name;
      if (e.isDirectory()) walk(rel, acc);
      else if (/\.tsx?$/.test(e.name) && !e.name.includes('.test.')) acc.push(rel);
    }
    return acc;
  };

  it('only what renders outside the shell names the viewport', () => {
    const files = walk('');
    expect(files.length, 'no files walked — this would pass on nothing').toBeGreaterThan(300);
    const claimers = files.filter((f) => CLAIM.test(src(f)));
    expect(claimers.length, 'no viewport claims found at all — the pattern stopped matching')
      .toBeGreaterThan(5);
    expect(claimers.filter((f) => !(f in VIEWPORT_OK)),
      'these render inside the shell and claim the whole viewport — size to the parent instead')
      .toEqual([]);
  });

  it('and every exemption is still real', () => {
    // An exemption for a file that no longer names the viewport is a
    // hole nobody is watching.
    expect(Object.keys(VIEWPORT_OK).filter((f) => !CLAIM.test(src(f))),
      'listed as allowed to name the viewport, but no longer does — drop the entry')
      .toEqual([]);
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
