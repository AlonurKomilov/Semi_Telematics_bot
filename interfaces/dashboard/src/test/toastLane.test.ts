/**
 * One door into the toast lane.
 *
 * `lib/toast` sounds every toast it raises. That only holds while it is
 * the only way to raise one — a file that goes straight to `sonner`
 * gets a silent toast, and nothing goes red: the toast still appears,
 * the suite still passes, and the only symptom is a person who turned
 * interface sound on and hears the app answer some of the time.
 *
 * This is the guard that makes that loud instead. It is why the
 * migration was 76 import lines rather than one clever listener beside
 * the `<Toaster>` — a rule a test can state beats a mechanism nobody
 * can see.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');

/**
 * Who may name sonner, and why.
 *
 * `lib/toast.ts` IS the wrapper. `main.tsx` mounts the `<Toaster>`
 * component, which the wrapper does not re-export because it is a
 * component and not a way to raise anything.
 */
const ALLOWED = new Map([
  ['lib/toast.ts', /import \{ toast as sonnerToast, type ExternalToast \} from 'sonner'/],
  ['main.tsx', /import \{ Toaster \} from 'sonner'/],
]);

/**
 * Every line in `src` that names sonner and is not the exception for
 * that file. Extracted so the detector itself can be shown to work —
 * `offenders.toEqual([])` is satisfied by a check that skips
 * everything, which is how a structural guard turns into decoration.
 */
function offendersIn(file: string, src: string): string[] {
  const out: string[] = [];
  for (const m of src.matchAll(/^.*from '(sonner)'.*$/gm)) {
    const allowed = ALLOWED.get(file);
    if (allowed?.test(m[0])) continue;
    out.push(`${file}: ${m[0].trim()}`);
  }
  return out;
}

function walk(dir: string, acc: string[] = []): string[] {
  for (const e of readdirSync(dir)) {
    const full = join(dir, e);
    if (statSync(full).isDirectory()) { walk(full, acc); continue; }
    if (/\.tsx?$/.test(full)) acc.push(relative(SRC, full));
  }
  return acc;
}

describe('every toast comes through lib/toast', () => {
  const files = walk(SRC).filter((f) => !f.includes('.test.'));

  it('finds files to check', () => {
    // A walker that returns nothing would pass every assertion below.
    expect(files.length).toBeGreaterThan(400);
  });

  it('and the lane it guards is really wired', () => {
    // The rule is worth nothing if the wrapper stopped sounding. Read
    // the two halves it exists for, so a wrapper reduced to a re-export
    // fails HERE and not only in its own file's tests.
    const src = readFileSync(join(SRC, 'lib/toast.ts'), 'utf8');
    expect(src, 'the wrapper no longer sounds anything').toMatch(/playToastCue/);
    expect(src, 'the cue override is gone').toMatch(/cue\?: CueName \| false/);
  });

  /** The positive control. Without it, a detector that skips every line
   *  reports an empty list and this file goes green on a rule it has
   *  stopped enforcing — there is no offending file in the tree to
   *  prove otherwise, which is the whole point of the rule. */
  it('the detector can actually fail', () => {
    expect(offendersIn('features/x/Thing.tsx', "import { toast } from 'sonner';"))
      .toHaveLength(1);
    // And an exception is not a blanket pass for the file that holds it:
    // main.tsx may take the Toaster, not the toast.
    expect(offendersIn('main.tsx', "import { toast } from 'sonner';"))
      .toHaveLength(1);
    expect(offendersIn('main.tsx', "import { Toaster } from 'sonner';"))
      .toHaveLength(0);
  });

  it('nobody else names sonner', () => {
    const offenders = files.flatMap(
      (f) => offendersIn(f, readFileSync(join(SRC, f), 'utf8')));
    expect(offenders,
      "raise it through `lib/toast` — a toast from sonner is a silent one")
      .toEqual([]);
  });

  it('and each exception still names sonner, so none is a stale hole', () => {
    for (const [f] of ALLOWED) {
      const src = readFileSync(join(SRC, f), 'utf8');
      expect(/from 'sonner'/.test(src), `${f} no longer needs its exception`).toBe(true);
    }
  });
});

/**
 * An undo window has to announce itself.
 *
 * The tone cannot pick this cue and never could. A toast that opens an
 * undo window is raised as `success` — "something was saved" — while
 * the moment actually means "and here are your few seconds to take it
 * back". `lib/undoable.ts` says exactly that in a comment and passes
 * the override. Two call sites in the grid did not, and one of them
 * used the BARE callable, which had no `cue` in its type at all: both
 * windows opened, counted down and closed without a sound, over writes
 * the grid's own comments call persisted per-user across devices.
 *
 * The rule is that the question was ANSWERED, not that the answer is
 * `undo`. `cue: false` is legitimate for a caller that already sounded
 * the moment itself — which is what `stagedAction.tsx` does. What is
 * not legitimate is leaving it to the default, because for this one
 * shape the default is known to be wrong.
 */
describe('a toast that offers an Undo names a cue', () => {
  /**
   * Brackets inside a quoted string are text, not structure. Blanked so
   * a message like `'Deleted (3)'` cannot unbalance the scan, and
   * length-preserving so every offset still points where it did.
   */
  const neutralise = (src: string) =>
    src.replace(/'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"/g,
      (s) => s.replace(/[()[\]{}]/g, '_'));

  /** Comments are not code. A commented-out example must not be read as
   *  a call site — only line comments that OWN their line are stripped,
   *  so a `https://` inside a string survives. */
  const decomment = (src: string) =>
    src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '');

  /** Every `toast…(…)` call in a file, as its own source text. */
  const toastCalls = (src: string): string[] => {
    const code = decomment(src);
    const flat = neutralise(code);
    const out: string[] = [];
    for (const m of flat.matchAll(/\btoast(?:\.\w+)?\(/g)) {
      const from = m.index ?? 0;
      let depth = 0;
      let i = from + m[0].length - 1;
      for (; i < flat.length; i++) {
        if (flat[i] === '(') depth++;
        else if (flat[i] === ')' && --depth === 0) break;
      }
      if (depth !== 0) continue;            // unreadable — do not guess
      out.push(code.slice(from, i + 1));
    }
    return out;
  };

  const undoCalls = (src: string) =>
    toastCalls(src).filter((c) => /label:\s*'Undo'/.test(c));

  const silentUndo = (file: string, src: string) =>
    undoCalls(src)
      .filter((c) => !/\bcue:/.test(c))
      .map((c) => `${file}: ${c.replace(/\s+/g, ' ').slice(0, 72)}…`);

  const files = walk(SRC).filter((f) => !f.includes('.test.'));

  it('and that scan can fail', () => {
    const action = "action: { label: 'Undo', onClick: f }";
    expect(silentUndo('x.tsx', `toast('Deleted', { ${action} });`)).toHaveLength(1);
    expect(silentUndo('x.tsx', `toast('Deleted', { cue: 'undo', ${action} });`))
      .toHaveLength(0);
    // Already sounded by the caller — an answered question, so not an
    // offender. If this ever reads as one, `stagedAction.tsx` goes red
    // for doing the right thing.
    expect(silentUndo('x.tsx', `toast.success('Deleted', { cue: false, ${action} });`))
      .toHaveLength(0);
    // A parenthesis in the MESSAGE must not swallow the rest of the
    // file and find somebody else's `cue:`.
    expect(silentUndo('x.tsx',
      `toast('Deleted (3)', { ${action} });\nconst cue: number = 1;`)).toHaveLength(1);
    // A toast with no Undo is none of this rule's business.
    expect(silentUndo('x.tsx', "toast('Tip: right-click a tab.');")).toHaveLength(0);
    // And a commented-out example is not a call site.
    expect(silentUndo('x.tsx', `// toast('Deleted', { ${action} });`)).toHaveLength(0);
  });

  it('there are undo windows in the tree to check', () => {
    // Zero would make the rule below green forever. The scan going
    // blind looks exactly like the codebase being clean.
    const n = files.reduce(
      (a, f) => a + undoCalls(readFileSync(join(SRC, f), 'utf8')).length, 0);
    expect(n, 'no Undo toasts found — the rule below is checking nothing')
      .toBeGreaterThanOrEqual(3);
  });

  it('and every one of them answers the question', () => {
    const offenders = files.flatMap(
      (f) => silentUndo(f, readFileSync(join(SRC, f), 'utf8')));
    expect(offenders,
      'an Undo toast that names no cue takes the tone\'s — "saved", over a window '
      + 'that is about to close — or, from the bare callable, nothing at all')
      .toEqual([]);
  });
});
