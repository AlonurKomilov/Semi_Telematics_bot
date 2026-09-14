/**
 * The vehicle list: a column, not a scrolling box.
 *
 * The whole card used to scroll, so the search field and the status
 * filters left the screen as soon as you started reading the list they
 * filter.  Finding a truck meant scrolling to the top to type and back
 * down to see the result — the two controls you reach for WHILE reading
 * were the two that went away when you read.
 *
 * The geometry that fixes it is three classes deep and every one of
 * them is load-bearing, which is why they are held here rather than
 * left to the next person's eye.
 */
import { describe, it, expect } from 'vitest';
import src from './LiveMap.tsx?raw';

const code = (src as unknown as string)
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, '');

/** The `<Card …>` opening tag for the vehicle list. */
const card = /<Card padding="none" className="([^"]*)"/.exec(code)?.[1] ?? '';

describe('the vehicle list card', () => {
  it('is a flex column that does not scroll as one piece', () => {
    expect(card, 'the card markup moved — re-point this guard').not.toEqual('');
    expect(card).toMatch(/\bflex\b/);
    expect(card).toMatch(/\bflex-col\b/);
    expect(card, 'the card scrolls as one piece again, so the search '
      + 'field leaves the screen the moment you read the list')
      .not.toMatch(/\boverflow-y-auto\b/);
  });

  it('can shrink below its content, or nothing scrolls at all', () => {
    // A flex child's automatic minimum size is its CONTENT height.
    // Without min-h-0 the body refuses to shrink, the card grows past
    // the row, and the scroll never engages — the same rule that let
    // the entrance wrapper stretch this whole page.
    expect(card).toMatch(/\bmin-h-0\b/);
  });

  it('pins the header and scrolls exactly one region below it', () => {
    const header = /<div className="p-4 border-b border-border space-y-3([^"]*)"/.exec(code)?.[1];
    expect(header, 'the list header moved — re-point this guard').toBeDefined();
    expect(header!, 'the header is free to shrink, so the search field '
      + 'still travels with the list').toMatch(/\bshrink-0\b/);

    const bodies = code.match(/className="flex-1 min-h-0 overflow-y-auto"/g) ?? [];
    expect(bodies.length,
      'exactly one elastic region below the header — two of them means '
      + 'two scrollbars in one column').toBe(1);
  });
});
