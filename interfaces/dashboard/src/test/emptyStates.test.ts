/**
 * An empty state may not name a constraint that is not active.
 *
 * design.md carries the rule with a star: "No loads in this status" is
 * FALSE whenever a date window or filter is also narrowing the view, and
 * an empty state that names only the obvious condition sends someone
 * hunting an old record away believing it is gone.
 *
 * The Vehicles page broke it in the other direction, which is worse: its
 * zero-state said "No vehicles match this filter" at the one moment no
 * filter exists — `vehicles` is the raw server list, and every segment
 * and column filter is applied inside DataGrid after that branch. A
 * brand-new customer's first look at their fleet blamed a filter they
 * had never set.
 *
 * So the rule has two halves, and only one was written down. Name the
 * constraint that emptied the view — and do not invent one.
 *
 * This guard is the second half. It reads the COPY, not the branch:
 * whether a filter is active is a runtime question, but claiming one in
 * words is visible in the source, and that is the half that misleads.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');

/** Words that assert a narrowing the reader may not have applied. */
const CLAIMS_A_CONSTRAINT = /\b(filter|match|matches|search|criteria)\b/i;

/**
 * The app already does this correctly in two places, and they are the
 * shape the rule wants:
 *
 *   title={vehicleFilter ? 'No cameras match this filter' : 'No camera checks yet'}
 *
 * The constraint is named ONLY on the branch where it is active. So the
 * test is not "never say filter" — it is "do not say it unconditionally".
 *
 * The gating is checked PER PROP, not per mount, and that distinction is
 * the whole guard. A first draft asked whether the mount contained a
 * ternary anywhere; Vehicles has one on `action` for the permission
 * gate, so reverting its title to the old "No vehicles match this
 * filter" sailed straight through. A mutation caught it. The constraint
 * has to gate the STRING THAT NAMES IT, not sit somewhere nearby.
 */

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules') continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (name.endsWith('.tsx')) out.push(full);
  }
  return out;
}

/** en.json, flattened enough to resolve a dotted key. */
const EN = JSON.parse(readFileSync(join(SRC, 'locales/en.json'), 'utf8')) as unknown;
function lookup(key: string): string | null {
  let cur: unknown = EN;
  for (const part of key.split('.')) {
    if (typeof cur !== 'object' || cur === null || !(part in cur)) return null;
    cur = (cur as Record<string, unknown>)[part];
  }
  return typeof cur === 'string' ? cur : null;
}

interface Prop { file: string; line: number; copy: string; gated: boolean }

/** Read a `name={...}` expression with balanced braces — the values here
 *  nest ternaries and calls, which a flat regex cannot follow. */
function readProp(block: string, name: string): string | null {
  const at = block.indexOf(`${name}={`);
  if (at < 0) return null;
  let depth = 0;
  const from = at + name.length + 1;
  for (let i = from; i < block.length; i += 1) {
    if (block[i] === '{') depth += 1;
    else if (block[i] === '}') {
      depth -= 1;
      if (depth === 0) return block.slice(from + 1, i);
    }
  }
  return null;
}

/** Every string a prop could render: literals, and t() keys resolved
 *  through en.json plus their inline English fallbacks. */
function stringsIn(expr: string): string[] {
  const out: string[] = [];
  for (const lit of expr.matchAll(/'([^']{4,})'/g)) out.push(lit[1]);
  for (const lit of expr.matchAll(/"([^"]{4,})"/g)) out.push(lit[1]);
  for (const key of expr.matchAll(/t\('([^']+)'/g)) {
    const v = lookup(key[1]);
    if (v) out.push(v);
  }
  return out;
}

function props(): Prop[] {
  const found: Prop[] = [];
  for (const file of walk(SRC)) {
    const s = readFileSync(file, 'utf8');
    for (const m of s.matchAll(/<EmptyState\b(.*?)\/>/gs)) {
      for (const name of ['title', 'description']) {
        // Both JSX forms: an expression, and a plain quoted attribute.
        // The plain form cannot be gated at all — it has nowhere to put
        // a condition — so missing it would hide the clearest offenders.
        const expr = readProp(m[1], name)
          ?? new RegExp(`${name}="([^"]*)"`).exec(m[1])?.[1]?.replace(/^|$/g, '"') ?? null;
        if (expr === null) continue;
        const copy = stringsIn(expr);
        if (copy.length === 0) continue;
        found.push({
          file: relative(SRC, file).replace(/\\/g, '/'),
          line: s.slice(0, m.index).split('\n').length,
          copy: copy.join(' | '),
          // THIS prop's own expression, not the mount's.
          gated: expr.includes('?'),
        });
      }
    }
  }
  return found;
}

/**
 * The classification, tested on synthetic input.
 *
 * The corpus cannot test it any more: all three real offenders are
 * fixed, so forcing `gated` to true changes nothing anyone can see —
 * measured, and it is why this block exists. A rule with no live
 * counter-example needs its own example.
 */
describe('the rule itself', () => {
  const claims = (copy: string) => CLAIMS_A_CONSTRAINT.test(copy);
  const gated = (expr: string) => expr.includes('?');

  it('an unconditional claim is an offender', () => {
    expect(claims('No rules match')).toBe(true);
    expect(gated('"No rules match"')).toBe(false);
  });

  it('the same words on a branch are not', () => {
    const expr = "f ? 'No rules match' : 'No scoring rules yet'";
    expect(claims('No rules match')).toBe(true);
    expect(gated(expr)).toBe(true);
  });

  it('copy that names nothing is never an offender, gated or not', () => {
    expect(claims('No vehicles yet')).toBe(false);
    expect(claims('Nothing has been added to this account.')).toBe(false);
  });
});

describe('an empty state names no constraint it has not got', () => {
  const all = props();

  it('found the empty states — a walk that matched nothing would pass everything', () => {
    // 80 at the time of writing — one entry per title/description that
    // renders words, across 43 mounts, since most carry both. The floor
    // asserts the walk still works, not that a count is frozen; a first
    // draft guessed 30 and the walk quietly found 29 because it could
    // not read plain `title="..."` attributes, of which there are 50.
    expect(all.length).toBeGreaterThan(60);
  });

  it('names a filter only where something gates it', () => {
    const offenders = all
      .filter((m) => CLAIMS_A_CONSTRAINT.test(m.copy) && !m.gated)
      .map((m) => `${m.file}:${m.line} — "${m.copy}"`);
    expect(
      offenders,
      'an empty state blames a narrowing unconditionally. Either gate the '
      + 'wording on the constraint being active, or say what actually emptied '
      + 'the view.',
    ).toEqual([]);
  });

  it('still finds the mounts that DO gate it — or the rule above is untested', () => {
    // If nothing in the tree named a constraint conditionally, the check
    // above would pass because the pattern had vanished, not because it
    // was respected.
    const gated = all.filter((m) => CLAIMS_A_CONSTRAINT.test(m.copy) && m.gated);
    expect(gated.length, 'no empty state names a constraint at all any more').toBeGreaterThan(0);
  });
});
