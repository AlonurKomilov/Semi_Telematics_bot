/**
 * ``defaultPinned`` — identity columns ship frozen.
 *
 * The rule this pins: identity columns are pinned by DEFAULT, not
 * merely pinnable.  A wide settlement grid whose Unit column scrolls
 * away loses the one answer ("whose row is this?") exactly when the
 * reader is deep in the numbers that need it.  The column flag is only
 * the FALLBACK for the persisted pinning preference: an operator's own
 * stored map wins wholly, and Reset returns to the declared default.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import type { AnyColumn } from '../../types';

globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) =>
      (typeof d === 'string' ? d : (d as { defaultValue?: string })?.defaultValue) ?? k,
  }),
  initReactI18next: { type: '3rdParty', init: () => {} },
}));
vi.mock('../../hooks/useTimezone', () => ({ useTimezone: () => 'UTC' }));

// Simulates a user who already stored a pinning map for ONE table id:
// their choice must beat the columns' declared default.
const STORED: Record<string, unknown> = {};
vi.mock('../../preferences', async () => {
  const actual = await vi.importActual<
    typeof import('../../preferences/registry')
  >('../../preferences/registry');
  const { useState } = await import('react');
  return {
    useSyncLoaded: () => true,
    useTablePreference: (tableId: unknown, key: string, fallback?: unknown) => {
      const storedKey = `${String(tableId)}.${key}`;
      const initial = STORED[storedKey] !== undefined
        ? STORED[storedKey]
        : fallback !== undefined
          ? fallback
          : (actual.TABLE_PARTS as Record<string, { default: unknown }>)[key]?.default;
      const [value, setValue] = useState(initial);
      return { value, setValue };
    },
    usePreference: (_k: string, fallback: unknown) => {
      const [value, setValue] = useState(fallback);
      return { value, setValue };
    },
  };
});

import DataGrid from './index';

const COLUMNS: AnyColumn[] = [
  { key: 'unit', label: 'Unit', defaultPinned: 'left' },
  { key: 'gross', label: 'Gross' },
  { key: 'pct', label: 'KPI %' },
];
const ROWS = [
  { id: 1, unit: '226', gross: 9000, pct: 3 },
  { id: 2, unit: '237', gross: 7000, pct: 0 },
];

afterEach(() => {
  cleanup();
  for (const k of Object.keys(STORED)) delete STORED[k];
});

const leafPin = (container: HTMLElement, label: string) =>
  Array.from(container.querySelectorAll('thead tr[data-header-row="leaf"] th'))
    .find((th) => th.textContent?.includes(label))
    ?.getAttribute('data-pin') ?? null;

describe('defaultPinned', () => {
  it('starts a defaultPinned column frozen left', () => {
    const { container } = render(
      <DataGrid tableId="dp-test" columns={COLUMNS} data={ROWS}
        enableToolbar={false} enablePagination={false} />,
    );
    expect(leafPin(container, 'Unit')).toBe('left');
    expect(leafPin(container, 'Gross')).toBeNull();
  });

  it('a stored pinning map beats the declared default', () => {
    STORED['dp-test.pinning'] = { left: ['gross'], right: [] };
    const { container } = render(
      <DataGrid tableId="dp-test" columns={COLUMNS} data={ROWS}
        enableToolbar={false} enablePagination={false} />,
    );
    expect(leafPin(container, 'Gross')).toBe('left');
    expect(leafPin(container, 'Unit')).toBeNull();
  });
});
