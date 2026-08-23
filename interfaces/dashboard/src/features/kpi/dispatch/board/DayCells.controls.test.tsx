/**
 * The day cell holds TWO controls, never one inside the other.
 *
 * The defect this pins: load chips lived inside the cell's own button,
 * so clicking a load opened "mark this day inactive" — the wrong
 * action, and illegal HTML the moment the chip became a button too.
 * The layers split: a background button owns the DAY, the chips own
 * their LOADS.  Nesting them again would pass typecheck and break
 * both gestures.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup, screen, fireEvent } from '@testing-library/react';
import type { RunLoad, RunRow } from '../../api';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown, o?: Record<string, unknown>) => {
      const s = (typeof d === 'string' ? d : (d as { defaultValue?: string })?.defaultValue) ?? k;
      return typeof d === 'string' && o
        ? s.replace(/\{\{(\w+)\}\}/g, (_m, key) => String(o[key] ?? ''))
        : s;
    },
  }),
  initReactI18next: { type: '3rdParty', init: () => {} },
}));

const { DayCells } = await import('./DayCells');

const LOAD: RunLoad = {
  load_number: 'L-1', status: 'delivered',
  pickup_date: '2026-08-11', delivery_date: '2026-08-11',
  pickup_location: 'Reno, Nevada', delivery_location: 'Ogden, Utah',
  total_rate: 2400, miles: 900,
};

const ROW = {
  id: 7, vehicle_unit: '204', window_start: '2026-08-10',
  window_end: '2026-08-16', weekly_target: 8000, adjusted_target: 8000,
  inactive_dates: [], kpi_gross: 9000, pct: 2, confirmed_dollars: 180,
} as unknown as RunRow;

const DAYS = ['2026-08-10', '2026-08-11', '2026-08-12'];

function renderCells(over: Partial<Parameters<typeof DayCells>[0]> = {}) {
  const onOpenLoad = vi.fn();
  const onOpenMenu = vi.fn();
  render(
    <DayCells row={ROW} days={DAYS} loads={[LOAD]} suggestions={[]}
      clickable onOpenMenu={onOpenMenu} onOpenLoad={onOpenLoad} {...over} />,
  );
  return { onOpenLoad, onOpenMenu };
}

afterEach(cleanup);

describe('day cell controls', () => {
  it('never nests one button inside another', () => {
    renderCells();
    const nested = document.querySelectorAll('button button');
    expect(nested).toHaveLength(0);
  });

  it('opens the LOAD when its chip is clicked — not the day menu', () => {
    const { onOpenLoad, onOpenMenu } = renderCells();
    fireEvent.click(screen.getByRole('button', { name: /open load details/i }));
    expect(onOpenLoad).toHaveBeenCalledWith(ROW, LOAD);
    expect(onOpenMenu).not.toHaveBeenCalled();
  });

  it('opens the DAY menu from the cell background, anchored at itself', () => {
    const { onOpenLoad, onOpenMenu } = renderCells();
    const day = screen.getAllByRole('button', { name: /Mark .* inactive/i })[0];
    fireEvent.click(day);
    expect(onOpenMenu).toHaveBeenCalledWith(ROW, DAYS[0], day);
    expect(onOpenLoad).not.toHaveBeenCalled();
  });

  it('keeps loads openable on a finalized run, where days are read-only', () => {
    const { onOpenLoad, onOpenMenu } = renderCells({ clickable: false });
    expect(screen.queryByRole('button', { name: /Mark .* inactive/i })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /open load details/i }));
    expect(onOpenLoad).toHaveBeenCalledTimes(1);
    expect(onOpenMenu).not.toHaveBeenCalled();
  });
});
