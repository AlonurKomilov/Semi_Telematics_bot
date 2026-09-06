/**
 * The provider row: what it sends, and who may press it.
 *
 * The bug this pins: the PUT went out as a pre-stringified body, which
 * apiJSON passes through untouched — no JSON header, a string where the
 * server expects an object, 422 "Input should be a valid dictionary".
 * apiJSON stringifies an object body itself; callers hand it objects.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import MapTypeControl from './MapTypeControl';

const apiJSON = vi.hoisted(() => vi.fn());
vi.mock('../../api/client', () => ({ apiJSON }));
const perms = vi.hoisted(() => ({ has: vi.fn() }));
vi.mock('../../hooks/usePermissions', () => ({ usePermissions: () => perms }));
vi.mock('../../lib/toast', () => ({ toast: { error: vi.fn() } }));
vi.mock('../../components/tooltip', () => ({ Tip: ({ children }: { children: React.ReactNode }) => <>{children}</> }));

const engine = { googleAvailable: true, fellBackFrom: null, reason: '', refresh: vi.fn() };
const noop = () => {};

function open() {
  render(<MapTypeControl mapType="standard" showLabels={false} setMapType={noop} setShowLabels={noop}
                         isReady provider="osm" engine={engine} />);
  fireEvent.click(screen.getByText('Map Type'));
}

beforeEach(() => { apiJSON.mockReset().mockResolvedValue({}); perms.has.mockReset(); engine.refresh.mockReset(); });

describe('choosing the basemap', () => {
  it('sends the engine as an OBJECT body, the shape apiJSON turns into JSON', async () => {
    perms.has.mockReturnValue(true);
    open();
    fireEvent.click(screen.getByRole('radio', { name: 'Google' }));
    await waitFor(() => expect(apiJSON).toHaveBeenCalledWith('/map/config', { method: 'PUT', body: { engine: 'google' } }));
    expect(typeof apiJSON.mock.calls[0][1].body).toBe('object');
    expect(engine.refresh).toHaveBeenCalled();
  });

  it('pressing the one already on sends nothing', () => {
    perms.has.mockReturnValue(true);
    open();
    fireEvent.click(screen.getByRole('radio', { name: 'OpenStreetMap' }));
    expect(apiJSON).not.toHaveBeenCalled();
  });

  it('without the config flag the row shows the choice but cannot make it', () => {
    perms.has.mockReturnValue(false);
    open();
    const google = screen.getByRole('radio', { name: 'Google' }) as HTMLButtonElement;
    expect(google.disabled).toBe(true);
    expect(screen.getByRole('radio', { name: 'OpenStreetMap' }).getAttribute('aria-checked')).toBe('true');
  });

  it('is not drawn at all when the server cannot offer Google', () => {
    perms.has.mockReturnValue(true);
    render(<MapTypeControl mapType="standard" showLabels={false} setMapType={noop} setShowLabels={noop}
                           isReady provider="osm" engine={{ ...engine, googleAvailable: false }} />);
    fireEvent.click(screen.getByText('Map Type'));
    expect(screen.queryByRole('radiogroup', { name: 'Map provider' })).toBeNull();
  });
});
