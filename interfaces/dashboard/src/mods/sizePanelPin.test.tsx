/**
 * The Size panel must not resize itself.
 *
 * This is the third attempt at the same bug. Twice it was called fixed:
 * once with no pin at all, once with a pin that only applied while a
 * thumb was held. A browser audit measured the second one still running
 * away — an 80px drag left the thumb 25px right and 74px BELOW the
 * pointer, a long drag 114/172px, and the reverse direction overshot
 * 89px left. Measurement in a probe said it was fixed; the real page
 * disagreed, because the probe had almost nothing above the panel and
 * the real page has a header and other cards that all grow.
 *
 * So the assertion is not "does it look right" — it is the invariant:
 * every axis, plus the region escape, is pinned on the panel's own
 * element, unconditionally. If someone makes it conditional again, this
 * goes red without needing a browser.
 */
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';

vi.mock('../preferences/usePreference', () => ({
  usePreference: () => ({ value: false, setValue: () => {} }),
}));
vi.mock('./context', () => ({
  useMods: () => ({
    size: { global: 1.2, text: 1, control: 1, layout: 1, panel: 1, regions: {} },
    setSize: () => {},
  }),
  applySize: () => {},
}));
vi.mock('../preferences/appearance', () => ({
  publishAppearanceDefault: () => {}, resetAppearanceDefault: () => {},
}));

import SizeCard from './SizeCard';

const AXES = ['--size-text', '--size-control', '--size-layout', '--size-panel'];

describe('the Size panel pins its own scale', () => {
  it('escapes its region and holds every axis, with no drag in progress', () => {
    const { container } = render(<SizeCard />);
    const panel = container.querySelector('#appearance') as HTMLElement;
    expect(panel).not.toBeNull();
    expect(panel.style.getPropertyValue('--size-region')).toBe('1');
    for (const axis of AXES) {
      // committed global is 1.2 and each axis is 1 → 1.2
      expect(panel.style.getPropertyValue(axis)).toBe('1.2');
    }
  });
});
