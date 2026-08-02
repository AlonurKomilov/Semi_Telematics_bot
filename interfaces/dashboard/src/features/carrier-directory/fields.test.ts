// mergeRows decides whether typed carrier data survives.  It runs on three
// surfaces (manager editor, read-only view, public form) and it is the only
// thing standing between a label rename and orphaned values, so the tricky
// inputs are pinned here rather than left to a manual click-through.
import { describe, it, expect } from 'vitest';
import { SECTIONS, PUBLIC_SECTIONS, mergeRows, safeHref } from './fields';
import type { FieldDef } from './fields';

const def = (label: string, renamedFrom?: string[]): FieldDef =>
  renamedFrom ? { label, renamedFrom } : { label };

describe('mergeRows', () => {
  it('fills template labels from stored values and keeps blanks', () => {
    const out = mergeRows([def('A'), def('B')], [{ label: 'A', value: '1' }]);
    expect(out).toEqual([
      { label: 'A', value: '1' },
      { label: 'B', value: '' },
    ]);
  });

  it('adopts a value stored under an OLD label after a rename', () => {
    const out = mergeRows(
      [def('NYC Runs Required?', ['New York City'])],
      [{ label: 'New York City', value: 'Yes' }],
    );
    // Re-filed under the new label — and NOT also left behind as a custom row.
    expect(out).toEqual([{ label: 'NYC Runs Required?', value: 'Yes' }]);
  });

  it('prefers the CURRENT label when both old and new are stored', () => {
    const out = mergeRows(
      [def('New', ['Old'])],
      [{ label: 'Old', value: 'stale' }, { label: 'New', value: 'fresh' }],
    );
    expect(out[0]).toEqual({ label: 'New', value: 'fresh' });
    // The stale one is not silently destroyed — it survives as a custom row
    // so a human can see it and delete it deliberately.
    expect(out).toContainEqual({ label: 'Old', value: 'stale' });
  });

  it('prefers a NON-EMPTY old value over an empty current one', () => {
    const out = mergeRows(
      [def('New', ['Old'])],
      [{ label: 'New', value: '' }, { label: 'Old', value: 'kept' }],
    );
    expect(out[0]).toEqual({ label: 'New', value: 'kept' });
    // …and the now-redundant blank row must NOT leak into the custom tail
    // as a second row wearing the same label. Asserting out[0] alone let
    // this through once already.
    expect(out).toHaveLength(1);
    expect(out.filter((r) => r.label === 'New')).toHaveLength(1);
  });

  it('never emits two template rows with the same label', () => {
    // Property check across every real template, for each of the four
    // stored-state permutations a rename can produce.
    for (const s of SECTIONS) {
      for (const f of s.fields ?? []) {
        const old = f.renamedFrom?.[0];
        if (!old) continue;
        for (const stored of [
          [{ label: f.label, value: '' }, { label: old, value: 'v' }],
          [{ label: f.label, value: 'v' }, { label: old, value: '' }],
          [{ label: old, value: '' }],
          [{ label: f.label, value: '' }],
        ]) {
          const out = mergeRows(s.fields, stored);
          const dupes = out.filter((r) => r.label === f.label);
          expect(dupes.length, `${f.label} duplicated for ${JSON.stringify(stored)}`).toBe(1);
        }
      }
    }
  });

  it('does not drop duplicate-label rows (the old Map collapsed them)', () => {
    const out = mergeRows(
      [def('A')],
      [
        { label: 'A', value: 'first' },
        { label: 'A', value: 'second' },
        { label: 'A', value: 'third' },
      ],
    );
    expect(out[0]).toEqual({ label: 'A', value: 'first' });
    // The other two are still reachable rather than silently gone.
    expect(out).toHaveLength(3);
    expect(out.map((r) => r.value)).toEqual(['first', 'second', 'third']);
  });

  it('keeps genuine custom rows after the template rows', () => {
    const out = mergeRows([def('A')], [{ label: 'Custom', value: 'x' }]);
    expect(out).toEqual([
      { label: 'A', value: '' },
      { label: 'Custom', value: 'x' },
    ]);
  });

  it('is a no-op-safe on empty inputs', () => {
    expect(mergeRows([], [])).toEqual([]);
    expect(mergeRows(undefined, undefined)).toEqual([]);
  });

  it('leaves a renamed field blank when neither label is stored', () => {
    expect(mergeRows([def('New', ['Old'])], [])).toEqual([
      { label: 'New', value: '' },
    ]);
  });
});

describe('cached draft written under an OLD template', () => {
  // The scenario that makes label-matching non-negotiable: a carrier
  // autosaves a draft, we ship a reorder + renames, they reopen the link.
  // Splicing the cached array in positionally would show their answers
  // under whatever field now happens to sit at that index.
  it('re-files old-order, old-label values under the right fields', () => {
    const oldOrderCache = [
      { label: 'Sign-On Bonus', value: '$1,000' },
      { label: 'New York City', value: 'Yes' },          // renamed since
      { label: 'Cameras', value: 'Road-facing only' },   // renamed since
      { label: 'Truck Speed', value: '68' },             // renamed since
    ];
    const presentation = SECTIONS.find((s) => s.key === 'presentation')!;
    const out = mergeRows(presentation.fields, oldOrderCache);
    const val = (label: string) => out.find((r) => r.label === label)?.value;

    expect(val('Sign-On Bonus')).toBe('$1,000');
    expect(val('NYC Runs Required?')).toBe('Yes');
    expect(val('In-Cab Cameras')).toBe('Road-facing only');
    expect(val('Governed Truck Speed (mph)')).toBe('68');
    // Nothing stranded at the tail under a dead label.
    for (const dead of ['New York City', 'Cameras', 'Truck Speed']) {
      expect(out.find((r) => r.label === dead)).toBeUndefined();
    }
    // And every other template field is present-but-blank, not missing.
    expect(out.length).toBe(presentation.fields!.length);
  });
});

describe('SECTIONS contract', () => {
  // Changing a section KEY orphans the whole stored content blob for that
  // section on every existing carrier — the keys are the wire contract with
  // the backend's _INTAKE_*_SECTIONS allow-lists.
  it('keeps the four stored section keys stable', () => {
    expect(SECTIONS.map((s) => s.key).sort()).toEqual([
      'application_process', 'prequal', 'presentation', 'recruiter_only',
    ]);
  });

  it('excludes the internal section from the public set', () => {
    expect(PUBLIC_SECTIONS.map((s) => s.key)).not.toContain('recruiter_only');
    expect(SECTIONS.find((s) => s.key === 'recruiter_only')?.internal).toBe(true);
  });

  it('has no duplicate labels within a section', () => {
    for (const s of SECTIONS) {
      const labels = (s.fields ?? []).map((f) => f.label);
      expect(new Set(labels).size, `duplicate label in ${s.key}`).toBe(labels.length);
    }
  });

  it('never reuses a live label as another field\'s renamedFrom', () => {
    // Would make two template fields fight over one stored value.
    const live = new Set(SECTIONS.flatMap((s) => (s.fields ?? []).map((f) => f.label)));
    for (const s of SECTIONS) {
      for (const f of s.fields ?? []) {
        for (const old of f.renamedFrom ?? []) {
          expect(live.has(old), `${old} is both a live label and an alias`).toBe(false);
        }
      }
    }
  });
});

describe('safeHref', () => {
  it('refuses every non-http(s) scheme', () => {
    for (const evil of [
      'javascript:alert(1)', 'JavaScript:alert(1)', '  javascript:alert(1) ',
      'data:text/html,<script>', 'vbscript:msgbox(1)', 'file:///etc/passwd',
    ]) {
      expect(safeHref(evil), evil).toBe('');
    }
  });

  it('passes http(s) through and upgrades a bare host', () => {
    expect(safeHref('https://a.example/x')).toBe('https://a.example/x');
    expect(safeHref('http://a.example')).toBe('http://a.example');
    expect(safeHref('a.example')).toBe('https://a.example');
  });

  it('treats blank as no link', () => {
    expect(safeHref('')).toBe('');
    expect(safeHref('   ')).toBe('');
    expect(safeHref(null)).toBe('');
    expect(safeHref(undefined)).toBe('');
  });
});
