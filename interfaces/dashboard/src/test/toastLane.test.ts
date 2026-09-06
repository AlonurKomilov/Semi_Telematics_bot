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
