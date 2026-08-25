/**
 * useCallout — WHICH labelled lines a callout answers.
 *
 * The vocabulary (`where · changed · why · affects · do`) is shared so
 * the form is learned once; the SELECTION is per callout, because one
 * fixed set cannot fit both kinds of statement this lane carries:
 *
 *   a FAULT has a cause, a cost and a remedy      → why/affects/do
 *   an EVENT has a subject and an old→new pair    → where/changed/…
 *
 * Forcing the fault's three onto an event is what put a changed VIN
 * mid-sentence under a label reading "Why", and printed "Answer below"
 * directly above the answer buttons.
 *
 * These run against the REAL en.json rather than fixtures: the thing
 * worth guarding is that the shipped copy declares the lines its
 * callout actually needs, which a fixture would happily fake.
 */
import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

import en from '../../locales/en.json';

// A stand-in for i18next's `t`: interpolate `{{var}}`, and echo the key
// back when the string is missing — the exact signal the resolver reads
// as "this callout does not answer that question".
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, vars: Record<string, string> = {}) => {
      const path = key.split('.');
      // Keys are `callout.<a.b>.<field>` — the middle segment carries a
      // dot of its own, so split off the ends rather than the whole.
      const bag = en.callout as unknown as
        Record<string, Record<string, string>>;
      const field = path[path.length - 1];
      const name = path.slice(1, -1).join('.');
      const raw = bag[name]?.[field];
      if (raw === undefined) return key;
      return raw.replace(/\{\{(\w+)\}\}/g, (_, v: string) => vars[v] ?? '');
    },
  }),
}));

import { useCallout, CALLOUT_LINES } from './useCallout';

const names = (c: Parameters<typeof useCallout>[0]) =>
  renderHook(() => useCallout(c)).result.current.lines.map((l) => l.name);

describe('which lines a callout answers', () => {
  it('gives an identity question its subject and its old→new pair', () => {
    const { result } = renderHook(() => useCallout({
      key: 'vehicle.vin_changed',
      params: { unit: '130', old: '4V4NC9EJ', new: '3AKJGLD5' },
    }));
    const lines = result.current.lines;
    expect(lines.map((l) => l.name))
      .toEqual(['where', 'changed', 'why', 'affects', 'do']);
    // The evidence the decision is made on stands alone, not buried in
    // a sentence — that was the whole complaint.
    expect(lines.find((l) => l.name === 'where')?.value).toBe('130');
    expect(lines.find((l) => l.name === 'changed')?.value)
      .toBe('4V4NC9EJ → 3AKJGLD5');
  });

  it('drops "where" when the surface already names the truck', () => {
    // A truck's own page passes an empty unit; the line resolves to
    // nothing and is omitted, so the copy needs no scoped variant.
    expect(names({
      key: 'vehicle.vin_changed',
      params: { unit: '', old: 'A', new: 'B' },
    })).toEqual(['changed', 'why', 'affects', 'do']);
  });

  it('leaves the fault vocabulary alone — no subject, no change', () => {
    expect(names({ key: 'vehicle.no_engine_data' }))
      .toEqual(['why', 'affects', 'do']);
  });

  it('lets a caveat answer one question and stop', () => {
    // A caveat qualifies a number: nothing changed, nothing to do.
    expect(names({ key: 'mileage.partial' })).toEqual(['why']);
  });

  it('renders every line in the vocabulary order, never call order', () => {
    const { result } = renderHook(() => useCallout({
      key: 'vehicle.odometer_rebased',
      params: { unit: '130', old: '769842', new: '770034' },
    }));
    const got = result.current.lines.map((l) => l.name);
    expect(got).toEqual(CALLOUT_LINES.filter((n) => got.includes(n)));
  });

  it('labels each line from the shared set, not per callout', () => {
    const { result } = renderHook(() => useCallout({
      key: 'vehicle.gateway_swapped',
      params: { unit: '130', old: 'G1', new: 'G2' },
    }));
    for (const l of result.current.lines) {
      expect(l.label).toBe(en.callout.labels[l.name]);
    }
  });
});
