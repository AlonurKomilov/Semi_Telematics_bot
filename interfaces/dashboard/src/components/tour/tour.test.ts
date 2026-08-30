/**
 * The tour drift guards — what keeps a tour honest over time.
 *
 * A tour points at real DOM elements and speaks nine languages; both
 * halves rot silently.  An anchor whose element was deleted leaves the
 * engine pointing at nothing (it exits gracefully — so nothing ever
 * turns red for the USER, which is exactly why the build must).  A
 * locale that misses a step renders a raw key mid-tour.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { TOUR_CATALOG } from './tourCatalog';

const SRC = join(__dirname, '..', '..');
const LOCALE_DIR = join(SRC, 'locales');

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path, out);
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(path);
  }
  return out;
}

const SOURCES = walk(SRC).map((p) => readFileSync(p, 'utf-8'));
const LOCALES = readdirSync(LOCALE_DIR).filter((f) => f.endsWith('.json'));
const load = (f: string) =>
  JSON.parse(readFileSync(join(LOCALE_DIR, f), 'utf-8')) as Record<string, unknown>;

describe('tour catalog', () => {
  it('every step anchor exists in the source as a data-tour attribute', () => {
    // THE tour-rot guard.  Anchors are declared attributes precisely so
    // this test can see them; a redesign that drops one fails here, at
    // build time, instead of in front of a user mid-tour.
    const missing: string[] = [];
    for (const tour of TOUR_CATALOG) {
      for (const step of tour.steps) {
        for (const anchor of [step.anchor, step.countFrom].filter(Boolean)) {
          const needle = `data-tour="${anchor}"`;
          if (!SOURCES.some((s) => s.includes(needle))) {
            missing.push(`${tour.key}: ${needle}`);
          }
        }
      }
    }
    expect(missing, 'anchors referenced by tours but absent from src').toEqual([]);
  });

  it('keys are namespaced <feature>.<name> and match their feature field', () => {
    for (const tour of TOUR_CATALOG) {
      expect(tour.key).toMatch(/^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/);
      expect(tour.key.split('.')[0]).toBe(tour.feature);
    }
  });

  it('every tour DECLARES whether it ends in a write', () => {
    // The question an author must not be able to skip.  A final step
    // that fires a write gets `commit: true` and the hands-off card —
    // the tour suggested the idea, so it does not also press the
    // trigger.  A benign ending says `commit: false` out loud.  An
    // UNDECLARED final step fails the build, because the difference
    // between "teaching" and "walking a user into 100 real tasks" is
    // exactly one forgotten flag.
    const undeclared: string[] = [];
    const misplaced: string[] = [];
    for (const tour of TOUR_CATALOG) {
      const last = tour.steps[tour.steps.length - 1];
      if (typeof last.commit !== 'boolean') undeclared.push(tour.key);
      for (const step of tour.steps.slice(0, -1)) {
        // A mid-tour write would strand the user between a real
        // consequence and more instructions — a tour ends AT a write
        // or not at all.
        if (step.commit) misplaced.push(`${tour.key}: ${step.anchor}`);
      }
    }
    expect(undeclared, 'final step missing commit: true|false').toEqual([]);
    expect(misplaced, 'commit steps must be final').toEqual([]);
  });

  it('a countFrom step reads a LIVE attribute, not a guess', () => {
    // The consequence line's number comes from data-tour-count on
    // the declared element; the attribute must be written in source or
    // the card silently loses its number on every render.
    const missing: string[] = [];
    for (const tour of TOUR_CATALOG) {
      for (const step of tour.steps) {
        if (!step.countFrom) continue;
        const el = `data-tour="${step.countFrom}"`;
        const withCount = SOURCES.some(
          (s) => s.includes(el) && s.includes('data-tour-count='));
        if (!withCount) missing.push(`${tour.key}: ${step.countFrom}`);
      }
    }
    expect(missing, 'countFrom anchors without a data-tour-count').toEqual([]);
  });

  it('tours stay walkable: 1-6 steps', () => {
    // Above six the tour stops being a shortcut and becomes a course;
    // the cap is a design decision, not a technical one.
    for (const tour of TOUR_CATALOG) {
      expect(tour.steps.length).toBeGreaterThanOrEqual(1);
      expect(tour.steps.length).toBeLessThanOrEqual(6);
    }
  });
});

describe('tour locales', () => {
  const REQUIRED_LABELS = ['show_me', 'skip', 'exit', 'done_title', 'close', 'step_of', 'finish'];

  it('every locale answers every tour completely', () => {
    // title + body + one line per step + done — same questions in all
    // nine languages, so no locale silently shows a raw key.
    const problems: string[] = [];
    for (const f of LOCALES) {
      const spot = load(f).tour as Record<string, Record<string, string>> | undefined;
      if (!spot) { problems.push(`${f}: no tour section`); continue; }
      for (const label of REQUIRED_LABELS) {
        if (!spot.labels?.[label]) problems.push(`${f}: labels.${label}`);
      }
      for (const tour of TOUR_CATALOG) {
        const entry = spot[tour.key];
        if (!entry) { problems.push(`${f}: ${tour.key}`); continue; }
        const last = tour.steps[tour.steps.length - 1];
        const want = ['title', 'body', 'done',
          ...(last.commit ? ['commit'] : []),
          ...tour.steps.map((_, i) => `step${i + 1}`)];
        for (const field of want) {
          if (!entry[field]) problems.push(`${f}: ${tour.key}.${field}`);
        }
        // A field no step reads is a promise the engine won't keep.
        const extra = Object.keys(entry).filter((k) => !want.includes(k));
        for (const k of extra) problems.push(`${f}: ${tour.key}.${k} (nothing reads it)`);
      }
    }
    expect(problems).toEqual([]);
  });

  it('no locale silently ships the English string', () => {
    // Field parity is not translation parity — the callouts guards
    // learned this when six caveats sat in English across eight
    // locales for months.  Product names quoted from the UI ("New
    // task", "Create") legitimately stay English INSIDE a translated
    // sentence; a whole VALUE identical to English means the
    // translation never happened.
    const en = load('en.json').tour as Record<string, Record<string, string>>;
    const problems: string[] = [];
    for (const f of LOCALES) {
      if (f === 'en.json') continue;
      const spot = load(f).tour as Record<string, Record<string, string>>;
      for (const [section, fields] of Object.entries(en)) {
        for (const [field, value] of Object.entries(fields)) {
          if (spot?.[section]?.[field] === value) {
            problems.push(`${f}: tour.${section}.${field}`);
          }
        }
      }
    }
    expect(problems, 'byte-identical to English').toEqual([]);
  });
});
