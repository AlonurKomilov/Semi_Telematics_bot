/**
 * The invitation's contract: present only where its anchor is, a real
 * accessible button, and one press opens the conversation.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import TourBeacon from './TourBeacon';
import type { TourSpec } from './types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => { document.body.innerHTML = ''; });

const tour: TourSpec = {
  key: 'maintenance.bulk_add', feature: 'maintenance',
  steps: [{ anchor: 'b1', advanceOn: 'click' }],
  relevant: () => true,
};

describe('TourBeacon', () => {
  it('renders beside its anchor and opens on press', async () => {
    const el = document.createElement('button');
    el.setAttribute('data-tour', 'b1');
    document.body.appendChild(el);
    const onOpen = vi.fn();
    render(<TourBeacon tour={tour} onOpen={onOpen} />);
    const beacon = await screen.findByLabelText('tour.labels.beacon');
    beacon.click();
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it('no anchor on screen means no invitation to make', async () => {
    render(<TourBeacon tour={tour} onOpen={() => {}} />);
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByLabelText('tour.labels.beacon')).toBeNull();
  });

  it('appears once the anchor mounts late, and leaves when it unmounts', async () => {
    const { } = render(<TourBeacon tour={tour} onOpen={() => {}} />);
    expect(screen.queryByLabelText('tour.labels.beacon')).toBeNull();
    const el = document.createElement('button');
    el.setAttribute('data-tour', 'b1');
    document.body.appendChild(el);
    await waitFor(() =>
      expect(screen.getByLabelText('tour.labels.beacon')).toBeTruthy(),
      { timeout: 2000 });
    el.remove();
    await waitFor(() =>
      expect(screen.queryByLabelText('tour.labels.beacon')).toBeNull(),
      { timeout: 2000 });
  });
});

describe('resolveManualTour', () => {
  it('finds a tour only under its own feature', async () => {
    const { resolveManualTour } = await import('./TourHost');
    expect(resolveManualTour('maintenance', 'maintenance.bulk_add')?.key)
      .toBe('maintenance.bulk_add');
    expect(resolveManualTour('loads', 'maintenance.bulk_add')).toBeNull();
    expect(resolveManualTour('maintenance', null)).toBeNull();
    expect(resolveManualTour('maintenance', 'maintenance.nonsense')).toBeNull();
  });
});
