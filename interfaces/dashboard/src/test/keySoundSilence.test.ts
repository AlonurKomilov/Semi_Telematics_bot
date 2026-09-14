/**
 * Every field that should not speak, doesn't — and a new one cannot
 * appear quietly.
 *
 * `keys.test.ts` proves the POLICY silences what it is handed. It cannot
 * prove the tree hands it the right things: a PII form added next month
 * would type happily, and nothing would go red. So this walks the source
 * and fails when a field whose name says "sensitive" is not marked.
 *
 * The public driver application — where the SSN and the date of birth
 * actually live — needs no marking at all, and the last test here is why:
 * it mounts on a tree with no ModProvider, so no sound can reach it. That
 * is a structural guarantee rather than a policy, and it is asserted
 * rather than trusted, because folding the two mounts together is a
 * refactor somebody could reasonably attempt.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC = join(__dirname, '..');

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === 'dist') continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (name.endsWith('.tsx') && !name.includes('.test.')) out.push(full);
  }
  return out;
}

/** What a field name has to look like before it must stay silent. */
const SENSITIVE = /\b(ssn|social.?security|dob|date.?of.?birth|birth.?date|cvc|cvv|card.?number|routing.?number|tax.?id)\b/i;

/** Read one JSX element's whole opening tag, braces balanced. */
function element(src: string, at: number): string {
  let i = at, depth = 0;
  while (i < src.length) {
    const ch = src[i];
    if (ch === '{') depth++;
    else if (ch === '}') depth--;
    else if (ch === '>' && depth === 0) break;
    i++;
  }
  return src.slice(at, i + 1);
}

interface Hit { file: string; line: number; text: string }

const hits: Hit[] = [];
for (const full of walk(SRC)) {
  const rel = relative(SRC, full);
  // The public application is a separate mount with no ModProvider —
  // the last test in this file is the one that keeps that true.
  if (rel.includes('applications/public')) continue;
  const src = readFileSync(full, 'utf8');
  for (const m of src.matchAll(/<(input|textarea|TextInput|Input)\b/g)) {
    const text = element(src, m.index!);
    if (!SENSITIVE.test(text)) continue;
    hits.push({
      file: rel,
      line: src.slice(0, m.index!).split('\n').length,
      text,
    });
  }
}

describe('a field whose name says sensitive stays silent', () => {
  it('finds fields to check — otherwise every assertion below is vacuous', () => {
    // Drivers' date of birth is the one this ships with. If this ever
    // reads 0, the walker broke, not the codebase.
    expect(hits.length, 'the walker found no sensitive fields at all').toBeGreaterThan(0);
  });

  it('every one of them carries data-no-key-sound', () => {
    const bare = hits
      .filter((h) => !h.text.includes('data-no-key-sound'))
      .map((h) => `${h.file}:${h.line}`);
    expect(
      bare,
      'a field whose name says SSN, date of birth, card or routing number types out '
        + 'loud. Four key classes make length, word boundaries and corrections audible '
        + 'to the room. Add data-no-key-sound to the input, or to a fieldset around it.',
    ).toEqual([]);
  });
});

/**
 * …and the second path into it, which the guarantee above does not cover.
 *
 * The docstring at the top of this file says the public form "mounts on a
 * tree with no ModProvider, so no sound can reach it", and it names the
 * refactor it fears: folding the two mounts together. That is not what
 * happened. `features/applications/ApplyPreview.tsx` renders
 * `<PublicApply>` INLINE, inside the dashboard tree, so the recruiter's
 * own preview of the FMCSA form — SSN, date of birth — sat under
 * ModProvider with the keyboard axis live.
 *
 * Nothing could have caught it. The walk above looks for `<input>`
 * elements whose NAMES say sensitive; this file renders a component, so
 * there is no input to find. And `isSensitiveTarget` (keys.ts:95-103)
 * knows passwords and card fields, not an SSN.
 *
 * So the rule is about the COMPONENT, not about its fields: every render
 * of the public form outside its own mount is marked, or this fails.
 */
describe('the public form is silent wherever else it is rendered', () => {
  const RENDER = /<PublicApply\b/g;
  const MARK = /data-no-key-sound/g;

  /**
   * Files that render the form and are not its own mount.
   *
   * `main.tsx` is excluded because it IS the mount, and the block below
   * proves the branch it renders from carries no ModProvider — a
   * structural guarantee, which is stronger than a marker. Every other
   * file renders into a tree that does carry one.
   */
  const embedders = walk(SRC)
    .map((full) => ({ rel: relative(SRC, full), src: readFileSync(full, 'utf8') }))
    .filter((f) => f.rel !== 'main.tsx')
    .filter((f) => !f.rel.startsWith('features/applications/public'))
    .filter((f) => RENDER.test(f.src) && ((RENDER.lastIndex = 0), true));

  it('finds the embed this rule exists for', () => {
    // Zero would make the assertion below green forever, and the whole
    // point is that this embed was invisible to every other check here.
    expect(embedders.map((f) => f.rel))
      .toContain('features/applications/ApplyPreview.tsx');
  });

  it('and every render of it is marked silent', () => {
    const offenders = embedders
      .map((f) => ({
        rel: f.rel,
        renders: (f.src.match(RENDER) ?? []).length,
        marks: (f.src.match(MARK) ?? []).length,
      }))
      .filter((f) => f.marks < f.renders)
      .map((f) => `${f.rel}: ${f.renders} render(s), ${f.marks} mark(s)`);
    expect(
      offenders,
      'the FMCSA form renders here inside ModProvider, and its SSN and '
        + 'date-of-birth fields are unmarked — four key classes make length, word '
        + 'boundaries and corrections audible to the room. Wrap it in '
        + '`<div data-no-key-sound className="contents">`.',
    ).toEqual([]);
  });
});

describe('the public application cannot make a sound at all', () => {
  const main = readFileSync(join(SRC, 'main.tsx'), 'utf8');

  it('mounts the apply tree outside ModProvider', () => {
    const start = main.indexOf('if (_isApply) {');
    const end = main.indexOf('} else {', start);
    expect(start, 'the apply branch moved — this reader is stale').toBeGreaterThan(-1);
    expect(end, 'the apply branch has no else — this reader is stale').toBeGreaterThan(start);

    // COMMENTS FIRST. The branch's own comment says "Outside ModProvider
    // by design", and matching prose is how the tap-floor ratchet in
    // chrome.test.ts spent its life counting sentences. Strip, then read.
    const applyBranch = main.slice(start, end)
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
      .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, '');
    // Sentinel: the branch really is the one that renders the form.
    expect(applyBranch, 'not the apply branch').toContain('PublicApply');
    expect(
      applyBranch.includes('ModProvider'),
      'the public form now mounts inside ModProvider — the SSN and date-of-birth '
        + 'fields on it are unmarked, because until now nothing could reach them',
    ).toBe(false);
  });

  it('and nothing under it imports the mods service', () => {
    const offenders: string[] = [];
    for (const full of walk(join(SRC, 'features/applications/public'))) {
      const src = readFileSync(full, 'utf8');
      if (/from\s+['"][^'"]*\/mods(\/|['"])/.test(src)) offenders.push(relative(SRC, full));
    }
    expect(offenders, 'the public form reached into mods').toEqual([]);
  });
});
