/**
 * AlertDeepLink keeps ``?alertId`` and the open details drawer in sync,
 * in BOTH directions.  The failure modes it guards are opposites:
 *
 *   • URL → drawer broken  ⇒ a link someone sends you lands on a
 *     2,500-row board with the alert nowhere in sight.
 *   • drawer → URL broken  ⇒ you can't send that link, and a refresh
 *     loses your place.
 *
 * The risky part is the loop between them: writing the param re-runs the
 * fetch effect, so a naive implementation either refetches what it just
 * opened or ping-pongs forever.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, act, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useSearchParams } from 'react-router-dom';
import type { ReactNode } from 'react';

const apiJSON = vi.fn();
vi.mock('../../../api/client', () => ({ apiJSON: (...a: unknown[]) => apiJSON(...a) }));
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

import AlertDeepLink from './AlertDeepLink';
import { AlertsSelectionProvider, useAlertsSelection } from './AlertsSelectionContext';

/** Surfaces both sides of the sync so assertions read as user-visible facts. */
function Probe() {
  const { drillInAlert, openDrillIn, closeDrillIn } = useAlertsSelection();
  const [params, setParams] = useSearchParams();
  return (
    <div>
      <span data-testid="open">{drillInAlert ? String(drillInAlert.id) : 'none'}</span>
      <span data-testid="param">{params.get('alertId') ?? 'none'}</span>
      <button onClick={() => openDrillIn({ id: 77 } as never)}>open 77</button>
      <button onClick={() => closeDrillIn()}>close</button>
      {/* Stands in for any OTHER writer of the query string — in
          production that's useAlertsFilters' first-load defaults. */}
      <button onClick={() => setParams(new URLSearchParams({ days: '30' }), { replace: true })}>
        strip params
      </button>
    </div>
  );
}

function mount(url: string) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/alerts" element={<AlertsSelectionProvider>{children}</AlertsSelectionProvider>} />
      </Routes>
    </MemoryRouter>
  );
  return render(wrapper({ children: <><AlertDeepLink /><Probe /></> }));
}

const open = () => screen.getByTestId('open').textContent;
const param = () => screen.getByTestId('param').textContent;
const click = async (label: string) => {
  await act(async () => { screen.getByText(label).click(); });
};

// NOTE: no beforeEach reset of ``apiJSON``.  Clearing a vi.fn between
// tests confuses vitest's promise-result bookkeeping, and the one test
// here that makes the request FAIL then gets its (properly caught)
// rejection reported as unhandled.  Each test sets the implementation it
// needs instead, and the one call-count assertion clears locally.
afterEach(cleanup);

describe('AlertDeepLink', () => {
  it('opens the drawer from ?alertId and KEEPS the param', async () => {
    apiJSON.mockResolvedValue({ alert: { id: 42 } });
    mount('/alerts?alertId=42');

    await waitFor(() => expect(open()).toBe('42'));
    // The param used to be deleted on success, which made the drawer
    // unshareable and lost on refresh.
    expect(param()).toBe('42');
    expect(apiJSON).toHaveBeenCalledWith('/alerts/by-id/42');
  });

  it('writes ?alertId when the drawer is opened from a row', async () => {
    apiJSON.mockClear();
    mount('/alerts');
    expect(param()).toBe('none');

    await click('open 77');
    expect(open()).toBe('77');
    expect(param()).toBe('77');
    // Opening locally must NOT trigger a by-id fetch — the row already
    // carries the record, and re-fetching would be a wasted round trip.
    expect(apiJSON).not.toHaveBeenCalled();
  });

  it('clears the param when the drawer closes', async () => {
    apiJSON.mockResolvedValue({ alert: { id: 42 } });
    mount('/alerts?alertId=42');
    await waitFor(() => expect(open()).toBe('42'));

    await click('close');
    expect(open()).toBe('none');
    expect(param()).toBe('none');
  });

  it('drops the param when the alert is gone, so the URL never points at nothing', async () => {
    apiJSON.mockImplementation(() => Promise.reject(new Error('404')));
    mount('/alerts?alertId=999');

    await waitFor(() => expect(param()).toBe('none'));
    expect(open()).toBe('none');
  });

  it('survives another writer replacing the query string mid-fetch', async () => {
    // The real path this broke on: the bell links to a bare
    // /alerts?alertId=N, and useAlertsFilters' first-load effect then
    // writes persona defaults.  It used to build a fresh URLSearchParams,
    // dropping alertId — which changed our dep, re-ran the effect, and
    // cancelled the in-flight fetch, so the alert never opened.  Here the
    // param is stripped by a third party WHILE the fetch is pending.
    let release!: (v: unknown) => void;
    apiJSON.mockImplementation(() => new Promise((r) => { release = r; }));
    mount('/alerts?alertId=42');
    await waitFor(() => expect(apiJSON).toHaveBeenCalledWith('/alerts/by-id/42'));

    await click('strip params');          // simulates the defaults writer
    expect(param()).toBe('none');

    await act(async () => { release({ alert: { id: 42 } }); });
    // The drawer still opens, and the URL is restored to name it.
    expect(open()).toBe('42');
    expect(param()).toBe('42');
  });

  it('re-opens the same alert after closing it (the ref resets)', async () => {
    apiJSON.mockResolvedValue({ alert: { id: 42 } });
    mount('/alerts?alertId=42');
    await waitFor(() => expect(open()).toBe('42'));

    await click('close');
    expect(open()).toBe('none');

    // Same id again — a common move: close the drawer, reopen the bell,
    // click the same still-open alert.
    await click('open 77');
    expect(param()).toBe('77');
  });
});
