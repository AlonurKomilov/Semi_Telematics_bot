/**
 * Every stylesheet a pack ships PARSES — and until this file existed,
 * nothing in the suite ever opened one.
 *
 * `tsc` does not read CSS and `vitest` does not either: a pack's
 * stylesheet reaches a parser for the first and only time inside
 * `vite build`. So a broken one is invisible to every check that runs
 * before a build, and a build in this tree IS a deploy — which means
 * the first thing to notice would have been the deploy, or a teammate.
 * It was a teammate. A stray comment-close, landed by an edit that
 * inserted a paragraph into the middle of an existing comment, left
 * twelve lines of prose sitting in CSS and `main` un-buildable across
 * two commits while the full suite stayed green both times. (Written
 * as words rather than as the delimiter itself, because spelling it
 * here ends THIS comment — which is how it happened over there.)
 *
 * The rule is therefore not "glass.css is valid" but the one the miss
 * actually exposed: the suite must READ what it ships. Parsing is the
 * cheapest possible form of that and it is the exact parser the build
 * uses, so a file that passes here cannot fail there for syntax.
 */
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import postcss from 'postcss';

/** Every `.css` under a directory, at any depth. */
function sheets(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return sheets(p);
    return name.endsWith('.css') ? [p] : [];
  });
}

/** The parse error postcss would raise, or null. Its message already
 *  names the file and the line, which is the whole point of using the
 *  real parser rather than counting delimiters. */
function parseFault(file: string): string | null {
  try {
    postcss.parse(readFileSync(file, 'utf8'), { from: file });
    return null;
  } catch (e) {
    return (e as Error).message;
  }
}

describe('every stylesheet this app ships parses', () => {
  const PACKS = join(__dirname, 'store', 'items');
  const found = sheets(PACKS);

  it('finds the pack stylesheets at all', () => {
    // Without this the sweep below passes loudest when it is looking at
    // nothing — a folder rename, and the guard reports every file clean
    // because there are no files.
    expect(found.length, 'no pack stylesheets found — is the path right?')
      .toBeGreaterThan(10);
  });

  it('no pack stylesheet fails the parser the build uses', () => {
    expect(found.filter((f) => parseFault(f) !== null)).toEqual([]);
  });

  it('nor does the app stylesheet', () => {
    expect(parseFault(join(__dirname, '..', 'index.css'))).toBeNull();
  });

  it('and the check can fail — on the exact shape that shipped', () => {
    // The control. A guard that only ever reports "clean" is
    // indistinguishable from one that is not looking, and this whole
    // file exists because a green suite meant nothing. So the case
    // below is the real one: a comment closed EARLY, leaving the rest
    // of its own prose standing in the stylesheet.
    const closedEarly = [
      '@media screen {', '  :root {', '    /* one line', '       two lines */',
      '       three is now prose', '       four */', '    --x: 1;', '  }', '}',
    ].join('\n');
    expect(() => postcss.parse(closedEarly, { from: 'x.css' })).toThrow(/Unknown word/);

    // And the other half of the same slip, for completeness: a comment
    // that never closes takes the rest of the file with it.
    expect(() => postcss.parse('/* never closed\n.a { color: red; }', { from: 'x.css' }))
      .toThrow(/Unclosed comment/);
  });
});
