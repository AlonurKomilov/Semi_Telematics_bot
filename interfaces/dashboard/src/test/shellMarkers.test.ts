/**
 * The stylesheet selects on markers the shell has to actually carry.
 *
 * `index.css` styles parts of the shell by NAME rather than by shape —
 * `.chrome-ground` for the surface a wallpaper paints,
 * `[data-ambient-recede]` for the chrome that steps back in ambient
 * mode — and AppShell's own comment says why: "Marked rather than
 * selected by shape, so a shell refactor cannot silently take the
 * mode's meaning with it."
 *
 * Nothing checked. A marker dropped in a refactor takes its whole
 * feature with it and every test stays green, because each half is
 * proven on its own: the CSS is measured against the tokens, the axis
 * is stored and stamped, the panel writes it — and the surface it was
 * all for is simply no longer named. That is exactly what a mutation
 * removing `chrome-ground` did, and it passed.
 *
 * So the SUBJECT comes from the stylesheet, not a list here: every
 * marker `index.css` selects on must exist in the shell. A new marker
 * is covered the day it is styled.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');
const CSS = readFileSync(join(SRC, 'index.css'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');

/**
 * Everything the app renders, rather than a list of shell files.
 *
 * A list would be the second inventory this codebase keeps learning
 * about: the shell has moved twice, and a guard pointed at the file
 * a marker USED to live in fails for the wrong reason or, worse,
 * silently checks a file that no longer carries anything. What the
 * stylesheet needs is that SOMETHING renders the marker; where is the
 * shell's business.
 */
function walk(dir: string, acc: string[] = []): string[] {
  for (const e of readdirSync(dir)) {
    const full = join(dir, e);
    if (statSync(full).isDirectory()) { walk(full, acc); continue; }
    if (/\.tsx$/.test(full) && !full.includes('.test.')) acc.push(full);
  }
  return acc;
}
/** Comments out, first — and this file's own history is the argument.
 *  A mutation removing `chrome-ground` from the shell PASSED, because
 *  the JSX comment explaining the marker still said the word. A guard
 *  that reads prose is measuring the explanation, not the code. */
const code = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const rendered = walk(SRC).map((f) => code(readFileSync(f, 'utf8'))).join('\n');

/** Class and attribute markers this app invented — not Tailwind's, and
 *  not the token-driven `[data-*]` axes stamped on `<html>`. */
const HTML_AXES = new Set([
  'data-accent', 'data-theme', 'data-radius', 'data-material', 'data-motion',
  'data-font', 'data-wallpaper', 'data-mod-accent', 'data-ambient', 'data-surface',
  'data-size', 'data-density', 'data-corners',
]);

/**
 * Markers a LIBRARY renders, which our components correctly do not.
 *
 * `index.css` styles sonner's toasts through the attributes sonner puts
 * on its own DOM. Those are a contract with the library, not with the
 * shell, and the thing that would break them is a sonner upgrade rather
 * than a refactor of ours — so they are named here with the reason
 * instead of widening the rule until it stops meaning anything.
 */
const FOREIGN: Record<string, string> = {
  'data-sonner-toaster': 'sonner renders it on its own container',
  'data-sonner-toast': 'sonner renders it on each toast',
  'data-styled': 'sonner marks its own styled toasts with it',
  'data-button': 'sonner marks its action button with it',
};

function markersInCss(): { attrs: string[]; classes: string[] } {
  const attrs = [...new Set(
    [...CSS.matchAll(/\[(data-[a-z-]+)(?:[\]=])/g)].map((m) => m[1]),
  )].filter((a) => !HTML_AXES.has(a) && !(a in FOREIGN));
  const classes = [...new Set(
    [...CSS.matchAll(/\[data-wallpaper="[^"]*"\]\s+\.([a-z][a-z0-9-]*)/g)].map((m) => m[1]),
  )];
  return { attrs, classes };
}

describe('every marker the stylesheet selects on exists in the shell', () => {
  const { attrs, classes } = markersInCss();

  it('finds markers to check', () => {
    // A parser that found none would make both assertions below pass on
    // nothing, which is the failure mode this whole file is about.
    expect([...attrs, ...classes].length, 'no shell markers parsed out of index.css')
      .toBeGreaterThan(1);
    expect(classes, 'the wallpaper surface stopped being styled by name')
      .toContain('chrome-ground');
    expect(attrs, 'ambient stopped marking the chrome that recedes')
      .toContain('data-ambient-recede');
  });

  it('something in the app carries each of them', () => {
    const missing = [
      ...attrs.filter((a) => !rendered.includes(a)),
      ...classes.filter((c) => !new RegExp(`\\b${c}\\b`).test(rendered)),
    ];
    expect(missing,
      'styled by name and carried by nothing — the feature is silently off')
      .toEqual([]);
  });

  it('and the check can fail', () => {
    // The control: the same containment test against a marker nothing
    // carries must report it.
    expect(rendered.includes('data-nothing-carries-this')).toBe(false);
    // And a marker that appears only in a COMMENT does not count as
    // carried — the exact way this guard passed a mutation that had
    // removed the thing it guards.
    expect(code('{/* chrome-ground is where a wallpaper paints */}\n<div />')
      .includes('chrome-ground'), 'a comment counted as carrying the marker')
      .toBe(false);
  });

  it('and every foreign exception is still a marker the stylesheet uses', () => {
    // An exception for an attribute nobody styles any more is a hole
    // kept open for nothing — and the next one added under the same
    // name would be waved through.
    for (const [attr, why] of Object.entries(FOREIGN))
      expect(CSS.includes(`[${attr}`), `${attr} is exempt (${why}) but no longer styled`)
        .toBe(true);
  });
});
