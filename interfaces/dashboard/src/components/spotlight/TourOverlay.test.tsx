/**
 * The engine against a live DOM — the three bugs review found, pinned.
 *
 * Each of these shipped GREEN through the data-layer guards, because
 * anchors existing and locales being complete says nothing about what
 * the engine does when a label forwards a second click, or when the
 * user closes the form mid-tour.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import TourOverlay from './TourOverlay';
import type { TourSpec } from './types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

// jsdom has no scrollIntoView.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

const tour = (steps: TourSpec['steps']): TourSpec => ({
  key: 'maintenance.bulk_add',
  feature: 'maintenance',
  steps,
  relevant: () => true,
});

function mountAnchor(anchor: string, tag = 'button'): HTMLElement {
  const el = document.createElement(tag);
  el.setAttribute('data-spotlight', anchor);
  document.body.appendChild(el);
  return el;
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('TourOverlay', () => {
  it('a label click that dispatches twice advances ONE step', async () => {
    // A real <label> wrapping an <input> fires two native clicks per
    // press (its own + the forwarded one).  Both land on the capture
    // listener; only one may count, or step 2 silently becomes step 3.
    const a = mountAnchor('s1');
    mountAnchor('s2');
    mountAnchor('s3');
    render(
      <TourOverlay
        tour={tour([
          { anchor: 's1', advanceOn: 'click' },
          { anchor: 's2', advanceOn: 'click' },
          { anchor: 's3', advanceOn: 'click' },
        ])}
        onDone={() => {}}
        onExit={() => {}}
      />,
    );
    await screen.findByText('spotlight.labels.step_of');
    a.click();
    a.click();          // the browser-forwarded duplicate
    await waitFor(() =>
      expect(screen.getByText('spotlight.maintenance.bulk_add.step2')).toBeTruthy());
  });

  it('advanceWithin refuses clicks on the container itself', async () => {
    const well = mountAnchor('chips', 'div');
    const chip = document.createElement('button');
    well.appendChild(chip);
    const onDone = vi.fn();
    render(
      <TourOverlay
        tour={tour([{ anchor: 'chips', advanceOn: 'click', advanceWithin: 'button' }])}
        onDone={onDone}
        onExit={() => {}}
      />,
    );
    await screen.findByText('spotlight.labels.step_of');
    well.click();                       // the well's own padding — picks nothing
    expect(screen.queryByText('spotlight.labels.done_title')).toBeNull();
    chip.click();                       // a real chip
    await waitFor(() =>
      expect(screen.getByText('spotlight.labels.done_title')).toBeTruthy());
  });

  it('an anchor that leaves the DOM sends the tour back to waiting, and it recovers', async () => {
    // Step 1's button TOGGLES the form: pressing it again mid-step-2
    // unmounts the step-2 anchor.  The ring must not stay frozen over
    // empty space — chrome hides, and reappears when the anchor does.
    const a = mountAnchor('gone');
    render(
      <TourOverlay
        tour={tour([{ anchor: 'gone', advanceOn: 'click' }])}
        onDone={() => {}}
        onExit={() => {}}
      />,
    );
    await screen.findByText('spotlight.labels.step_of');
    a.remove();
    await waitFor(() =>
      expect(screen.queryByText('spotlight.labels.step_of')).toBeNull());
    mountAnchor('gone');                // the user reopened the form
    await waitFor(() =>
      expect(screen.getByText('spotlight.labels.step_of')).toBeTruthy());
  });

  it('the last step celebrates once, and rapid clicks stay idempotent', async () => {
    const a = mountAnchor('only');
    const onDone = vi.fn();
    render(
      <TourOverlay
        tour={tour([{ anchor: 'only', advanceOn: 'click' }])}
        onDone={onDone}
        onExit={() => {}}
      />,
    );
    await screen.findByText('spotlight.labels.step_of');
    a.click(); a.click(); a.click();
    await waitFor(() =>
      expect(screen.getByText('spotlight.labels.done_title')).toBeTruthy());
    expect(onDone).not.toHaveBeenCalled();   // the card waits for its close
  });

  it('Escape exits mid-tour', async () => {
    mountAnchor('esc');
    const onExit = vi.fn();
    render(
      <TourOverlay
        tour={tour([{ anchor: 'esc', advanceOn: 'click' }])}
        onDone={() => {}}
        onExit={onExit}
      />,
    );
    await screen.findByText('spotlight.labels.step_of');
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(onExit).toHaveBeenCalledTimes(1);
  });
});

describe("advanceOn: 'click-gone' — celebrate the outcome, not the click", () => {
  it('a refused submit holds the step; success (anchor gone after click) celebrates', async () => {
    const btn = mountAnchor('create');
    render(
      <TourOverlay
        tour={tour([{ anchor: 'create', advanceOn: 'click-gone' }])}
        onDone={() => {}}
        onExit={() => {}}
      />,
    );
    await screen.findByText('spotlight.labels.step_of');
    btn.click();                    // validation refuses — button stays
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText('spotlight.labels.done_title')).toBeNull();
    expect(screen.getByText('spotlight.labels.step_of')).toBeTruthy();
    btn.remove();                   // the form closed itself: real success
    await waitFor(() =>
      expect(screen.getByText('spotlight.labels.done_title')).toBeTruthy());
  });

  it('an UNARMED disappearance is the user closing the form — no celebration', async () => {
    const btn = mountAnchor('create');
    render(
      <TourOverlay
        tour={tour([{ anchor: 'create', advanceOn: 'click-gone' }])}
        onDone={() => {}}
        onExit={() => {}}
      />,
    );
    await screen.findByText('spotlight.labels.step_of');
    btn.remove();                   // never clicked Create — cancelled
    await waitFor(() =>
      expect(screen.queryByText('spotlight.labels.step_of')).toBeNull());
    expect(screen.queryByText('spotlight.labels.done_title')).toBeNull();
    mountAnchor('create');          // reopened — the step comes back
    await waitFor(() =>
      expect(screen.getByText('spotlight.labels.step_of')).toBeTruthy());
  });
});
