import { describe, it, expect } from 'vitest';
import {
  priceKey, verdictFor, summaryFor, detailFor, overpricedLines,
  type PriceContext,
} from './priceContext';

const ctx = (over: Partial<PriceContext> = {}): PriceContext => ({
  buys: 6, low: 92, high: 105, last_price: 98,
  cheapest_vendor: 'Road Repair LLC', cheapest_price: 88, months: 12,
  ...over,
});

describe('priceKey', () => {
  it('matches the server normalization', () => {
    expect(priceKey('  Brake   Pad  SET ')).toBe('brake pad set');
    expect(priceKey('BRAKE PAD SET')).toBe(priceKey('brake pad set'));
  });
  it('empty stays empty', () => {
    expect(priceKey('   ')).toBe('');
  });
});

describe('verdictFor', () => {
  it('flags above the band', () => {
    expect(verdictFor(118, ctx())).toBe('above');
  });
  it('band edges are inclusive — exactly p75 is not scolded', () => {
    expect(verdictFor(105, ctx())).toBe('typical');
    expect(verdictFor(92, ctx())).toBe('typical');
  });
  it('inside the band is typical', () => {
    expect(verdictFor(99, ctx())).toBe('typical');
  });
  it('below the band is called out too (a good buy)', () => {
    expect(verdictFor(70, ctx())).toBe('below');
  });
  it('a blank or zero price is never judged', () => {
    expect(verdictFor(0, ctx())).toBe('typical');
    expect(verdictFor(NaN, ctx())).toBe('typical');
  });
});

describe('copy', () => {
  it('summary reads as plain English', () => {
    expect(summaryFor(ctx())).toBe('You usually pay $92.00–$105.00');
  });
  it('a single-value band does not render a fake range', () => {
    expect(summaryFor(ctx({ low: 90, high: 90 }))).toBe('You usually pay $90.00');
  });
  it('detail carries confidence + where it was cheapest', () => {
    const d = detailFor(ctx());
    expect(d).toContain('6 purchases in the last 12 months');
    expect(d).toContain('last paid $98.00');
    expect(d).toContain('cheapest $88.00 at Road Repair LLC');
  });
  it('omits the vendor clause when unknown', () => {
    expect(detailFor(ctx({ cheapest_vendor: '' }))).not.toContain('cheapest');
  });
  it('singular purchase reads correctly', () => {
    expect(detailFor(ctx({ buys: 1 }))).toContain('1 purchase in');
  });
});

describe('overpricedLines', () => {
  const contexts = { 'brake pad set': ctx() };

  it('picks only lines above their own band', () => {
    const out = overpricedLines([
      { part_name: 'Brake Pad Set', unit_cost: 118 },
      { part_name: 'Brake Pad Set', unit_cost: 99 },
    ], contexts);
    expect(out).toHaveLength(1);
    expect(out[0].unit_cost).toBe(118);
  });

  it('a part with no history is never flagged', () => {
    expect(overpricedLines(
      [{ part_name: 'Never Bought Before', unit_cost: 9999 }], contexts,
    )).toEqual([]);
  });

  it('matching is case- and spacing-insensitive', () => {
    const out = overpricedLines(
      [{ part_name: '  brake   PAD set ', unit_cost: 200 }], contexts,
    );
    expect(out).toHaveLength(1);
  });
});
