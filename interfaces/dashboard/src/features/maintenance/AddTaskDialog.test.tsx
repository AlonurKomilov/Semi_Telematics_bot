/**
 * The add form's STATE — the part a refactor breaks silently.
 *
 * Stage 2 of splitting Tasks.tsx moved fifteen `useState` hooks out of
 * the page and into this component. Code that moves compiles; state that
 * moves does not announce when it lands in the wrong place. The suite
 * stayed green through the whole migration and would have stayed green
 * if a `setF…` had been wired to the neighbouring field.
 *
 * Two behaviours are worth a test, and the second is a failure mode the
 * refactor CREATED:
 *
 *  1. The three trigger modes hold independent values. Only the active
 *     one is sent on save — the others are deliberately left undefined
 *     so a number you typed and switched away from never reaches the
 *     API — which only works if switching preserves them rather than
 *     bleeding one into another.
 *
 *  2. Closing the dialog discards what was typed. It used to reset
 *     fourteen fields by hand at the end of `handleAdd`, because the
 *     form never unmounted. The page renders this only while open now,
 *     so the reset comes free — and those fourteen lines are gone. If
 *     the unmount ever stops happening, the old state survives AND
 *     nothing clears it: a half-typed task reappears on the next open.
 *
 * This was a manual check ("open it, type, close, reopen, look"). It is
 * cheap to automate and expensive to remember, so it lives here.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) =>
      (typeof d === 'string' ? d : (d as { defaultValue?: string })?.defaultValue) ?? k,
  }),
  // src/i18n.ts is pulled in transitively and calls .use(initReactI18next)
  // at module scope — the mock has to satisfy that too.
  initReactI18next: { type: '3rdParty', init: () => {} },
}));
vi.mock('sonner', () => ({ toast: { success: () => {}, error: () => {} } }));
vi.mock('../../api/client', () => ({ apiJSON: vi.fn().mockResolvedValue({}) }));
// The service-task picker runs its own query; the form only needs it to
// render something selectable, and none of these tests touch it.
vi.mock('../service-tasks/ServiceTaskPicker', () => ({
  default: () => <div data-testid="service-task-picker" />,
}));

import AddTaskDialog from './AddTaskDialog';

const PROPS = {
  onOpenChange: () => {},
  onCreated: () => {},
  onError: () => {},
  canCreateTasks: true,
  vehicleList: [],
  fleetLoading: false,
  templates: [],
  templateItems: [],
  tz: 'UTC',
  company: '',
  onCompanyChange: () => {},
};

/** The three trigger modes, by the placeholder each one's input carries. */
const FIELD = { Date: 'e.g. 30', Miles: 'e.g. 3000', Hours: 'e.g. 500' } as const;

const switchTo = (mode: keyof typeof FIELD) =>
  fireEvent.click(screen.getByRole('button', { name: mode }));

const type = (mode: keyof typeof FIELD, value: string) =>
  fireEvent.change(screen.getByPlaceholderText(FIELD[mode]), { target: { value } });

const read = (mode: keyof typeof FIELD) =>
  (screen.getByPlaceholderText(FIELD[mode]) as HTMLInputElement).value;

afterEach(cleanup);

describe('AddTaskDialog — trigger modes', () => {
  it('keeps each mode’s value when you switch between them', () => {
    render(<AddTaskDialog open {...PROPS} />);

    type('Date', '30');
    switchTo('Miles');
    type('Miles', '5000');
    switchTo('Hours');
    type('Hours', '250');

    // Back to the first: if a setter landed on the wrong field during
    // the move, this is where it shows.
    switchTo('Date');
    expect(read('Date')).toBe('30');
    switchTo('Miles');
    expect(read('Miles')).toBe('5000');
    switchTo('Hours');
    expect(read('Hours')).toBe('250');
  });

  it('shows only the active mode’s field', () => {
    render(<AddTaskDialog open {...PROPS} />);
    expect(screen.queryByPlaceholderText(FIELD.Date)).toBeTruthy();
    expect(screen.queryByPlaceholderText(FIELD.Miles)).toBeNull();

    switchTo('Miles');
    expect(screen.queryByPlaceholderText(FIELD.Miles)).toBeTruthy();
    expect(screen.queryByPlaceholderText(FIELD.Date)).toBeNull();
  });
});

/** What the PAGE does — renders the dialog only while open.  Modelling
 *  it as ``open={false}`` instead would test something the app never
 *  does, and would pass while the real bug was live: this file's first
 *  run caught exactly that, because ``if (!open) return null`` inside the
 *  component leaves it mounted with its state intact. */
function Host({ open }: { open: boolean }) {
  return open ? <AddTaskDialog open {...PROPS} /> : null;
}

describe('AddTaskDialog — closing discards the form', () => {
  // ⭐ The one that guards a failure mode stage 2 introduced.  The manual
  // fourteen-line reset is gone; unmounting is what replaces it.
  it('comes back empty after being closed and reopened', () => {
    const { rerender } = render(<Host open />);
    type('Date', '30');
    expect(read('Date')).toBe('30');

    rerender(<Host open={false} />);
    expect(screen.queryByPlaceholderText(FIELD.Date)).toBeNull();

    rerender(<Host open />);
    expect(read('Date')).toBe('');
  });

  it('does not leak a half-typed value into a different mode either', () => {
    const { rerender } = render(<Host open />);
    switchTo('Miles');
    type('Miles', '9999');

    rerender(<Host open={false} />);
    rerender(<Host open />);

    // Reopening restores the DEFAULT mode as well as the empty value.
    expect(read('Date')).toBe('');
    switchTo('Miles');
    expect(read('Miles')).toBe('');
  });
});
