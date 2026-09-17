/**
 * The strip itself, not just the arithmetic behind it.
 *
 * `update.test.ts` proves the decision is right; this proves the panel
 * ACTS on it — that the two channels really say different things, that
 * dismissing writes the version rather than a flag, and that a panel
 * which cannot reach the server says nothing at all.  Those are the
 * parts a reader meets, and none of them were covered while only the
 * pure functions had tests.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

import { setManifest, setUpdateCheck } from '../test/setup';
import UpdateNotice from './UpdateNotice';

// `vi.mock` is hoisted above the imports by vitest's transform, so the
// component below really does receive this stub and not the real client.
const apiJSON = vi.fn();
vi.mock('../api/client', () => ({ apiJSON: (...a: unknown[]) => apiJSON(...a) }));

/** The strip's text, whitespace-collapsed, or '' when it is not there. */
const strip = () => document.body.textContent?.replace(/\s+/g, ' ').trim() ?? '';

beforeEach(() => {
  apiJSON.mockReset();
  apiJSON.mockResolvedValue({ version: '0.5.20.0' });
});

describe('UpdateNotice', () => {
  it('says nothing when the panel is already current', async () => {
    setManifest({ version: '0.5.20.0' });
    render(<UpdateNotice />);
    await waitFor(() => expect(apiJSON).toHaveBeenCalled());
    expect(strip()).toBe('');
  });

  it('says nothing when the server cannot be reached', async () => {
    // A panel with no answer must not invent one — silence is correct.
    apiJSON.mockRejectedValue(new Error('offline'));
    setManifest({ version: '0.5.18.1' });
    render(<UpdateNotice />);
    await waitFor(() => expect(apiJSON).toHaveBeenCalled());
    expect(strip()).toBe('');
  });

  it('tells a SIDELOAD reader to do it by hand, because nothing else will', async () => {
    setManifest({ version: '0.5.18.1', key: 'MIIBIjANBg' });
    render(<UpdateNotice />);
    await screen.findByText(/0\.5\.20\.0/);
    expect(strip()).toMatch(/will not update itself/i);
    // The meaning leads.  A strip that opens with `0.5.20.0` tells a
    // driver the one thing on it they have no use for.
    expect(strip()).toMatch(/^A newer panel is ready/);
    expect(strip()).toMatch(/chrome:\/\/extensions/);
    // …and hands them somewhere to actually get it.
    expect(screen.getByRole('link', { name: /your Profile page/i }).getAttribute('href'))
      .toMatch(/\/profile$/);
    // No "Check now": Chrome cannot update an unpacked copy, so offering
    // the button would be offering something that cannot work.
    expect(screen.queryByRole('button', { name: /check now/i })).toBeNull();
  });

  it('tells a STORE reader there is nothing to do', async () => {
    setManifest({ version: '0.5.18.1' });       // no key → store package
    render(<UpdateNotice />);
    await screen.findByText(/0\.5\.20\.0/);
    expect(strip()).toMatch(/Chrome installs it on its own/i);
    expect(strip()).toMatch(/^A newer panel is ready/);
    expect(strip()).not.toMatch(/chrome:\/\/extensions/);
    expect(screen.getByRole('button', { name: /check now/i })).toBeTruthy();
  });

  it('reports what Chrome actually answered, rather than assuming', async () => {
    setManifest({ version: '0.5.18.1' });
    setUpdateCheck({ status: 'no_update' });
    render(<UpdateNotice />);
    await screen.findByText(/0\.5\.20\.0/);
    fireEvent.click(screen.getByRole('button', { name: /check now/i }));
    // The honest answer when the store has not published yet — the
    // opposite of the optimistic "updating…" the reader would remember
    // as a lie the next time they opened the panel.
    await waitFor(() => expect(strip()).toMatch(/Not published yet/i));
  });

  it('remembers the VERSION dismissed, so a later one still speaks', async () => {
    setManifest({ version: '0.5.18.1' });
    render(<UpdateNotice />);
    await screen.findByText(/0\.5\.20\.0/);
    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }));
    await waitFor(() => expect(strip()).toBe(''));

    const got = await chrome.storage.local.get('updateDismissed');
    expect(got.updateDismissed).toBe('0.5.20.0');   // the version, not `true`
  });

  it('stays quiet on re-open for the version that was dismissed', async () => {
    await chrome.storage.local.set({ updateDismissed: '0.5.20.0' });
    setManifest({ version: '0.5.18.1' });
    render(<UpdateNotice />);
    await waitFor(() => expect(apiJSON).toHaveBeenCalled());
    expect(strip()).toBe('');
  });

  it('speaks again when a version newer than the dismissed one lands', async () => {
    await chrome.storage.local.set({ updateDismissed: '0.5.20.0' });
    apiJSON.mockResolvedValue({ version: '0.5.21.0' });
    setManifest({ version: '0.5.18.1' });
    render(<UpdateNotice />);
    expect(await screen.findByText(/0\.5\.21\.0/)).toBeTruthy();
  });

  it('reuses the last hour\'s answer instead of asking on every open', async () => {
    // The panel is opened and closed all day; this is the difference
    // between one request an hour and dozens a day, per reader.
    await chrome.storage.local.set({
      updateLastSeen: { version: '0.5.20.0', at: Date.now() },
    });
    setManifest({ version: '0.5.18.1' });
    render(<UpdateNotice />);
    await screen.findByText(/0\.5\.20\.0/);
    expect(apiJSON).not.toHaveBeenCalled();
  });

  it('asks again once that answer is stale', async () => {
    await chrome.storage.local.set({
      updateLastSeen: { version: '0.5.19.0', at: Date.now() - 2 * 60 * 60 * 1000 },
    });
    setManifest({ version: '0.5.18.1' });
    render(<UpdateNotice />);
    await screen.findByText(/0\.5\.20\.0/);       // the fresh answer, not the stale one
    expect(apiJSON).toHaveBeenCalledWith('/extension/info');
  });
});
