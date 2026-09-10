/**
 * The grounds row: two seeds, one gate, and a clear that can be undone.
 *
 * What it holds is the part a person can get wrong. A colour the
 * semantic tones cannot be read on is never written — the panel says
 * which tone, and the store keeps what it had. A seed that stops being
 * wearable when the mode changes is not shown as worn. And clearing one
 * ground leaves the other alone, because they are two decisions.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';

const { setTheme, undoableAction, view } = vi.hoisted(() => ({
  setTheme: vi.fn(), undoableAction: vi.fn(),
  /** What this view may open — the applies-to row is built from it. */
  view: { allow: new Set<string>() },
}));

let theme: Record<string, unknown> = {};

vi.mock('react-i18next', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));
vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}));
vi.mock('./context', () => ({
  useMods: () => ({
    theme,
    setTheme,
    size: { global: 1, text: 1, control: 1, layout: 1, panel: 1, regions: {} },
    setSize: () => {},
  }),
  applySize: () => {},
}));
vi.mock('../preferences', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  usePreference: () => ({ value: 'chime', setValue: () => {} }),
}));
vi.mock('../components/banners/stagedAction', () => ({ undoableAction }));
vi.mock('../hooks/useViewPermissions', () => ({
  useViewPermissions: () => ({
    has: (f: string) => view.allow.has(f),
    hasAny: (...f: string[]) => f.some((x) => view.allow.has(x)),
    hasWide: () => true, vehicleScope: 'all', role: 'fleet', ready: true,
  }),
}));

import { ModControls } from './panel/ModControls';
import { GROUNDS } from './theme/grounds';
import { SURFACES, permissionFor } from './surfaces';
import { groundTokens } from './theme/grounds';

const BASE = {
  mode: 'dark' as const, accent: 'blue', radius: 'md', material: 'solid',
  motion: 'default', mod: '',
};
const mount = (over: Record<string, unknown> = {}, compact = false) => {
  theme = { ...BASE, ...over };
  return render(<ModControls compact={compact} />);
};
/** The place the shell says the person is on — the same stamp the
 *  injector reads, which is where the panel takes it from. */
const standingOn = (id?: string) => {
  if (id) document.documentElement.dataset.surface = id;
  else delete document.documentElement.dataset.surface;
};
const input = (label: string) =>
  screen.getAllByLabelText(label).find(
    (el): el is HTMLInputElement => el instanceof HTMLInputElement)!;

const GREYS = Array.from({ length: 256 }, (_, i) => `#${i.toString(16).padStart(2, '0').repeat(3)}`);
const WORN = GREYS.find((h) => groundTokens('card', h, 'dark').tokens !== null)!;
const REFUSED = GREYS.find((h) => groundTokens('card', h, 'dark').tokens === null)!;
/** A second wearable colour, so a drag can end somewhere it did not start. */
const WORN2 = GREYS.filter((h) => groundTokens('card', h, 'dark').tokens !== null).pop()!;

const EVERY_PERMISSION = new Set(SURFACES.flatMap((s) => permissionFor(s) ?? []));

beforeEach(() => {
  setTheme.mockClear(); undoableAction.mockClear(); cleanup();
  standingOn(undefined); view.allow = new Set(EVERY_PERMISSION);
});

describe('the row offers every ground', () => {
  it('one picker each, by its own name', () => {
    mount();
    expect(GROUNDS.length).toBeGreaterThan(1);
    for (const g of GROUNDS) expect(input(g.label)).toBeTruthy();
  });
});

describe('what a pick writes', () => {
  it('seeds that ground and leaves the others alone', () => {
    mount({ grounds: { sidebar: WORN } });
    fireEvent.change(input('Cards'), { target: { value: WORN } });
    expect(setTheme).toHaveBeenCalledWith({ grounds: { sidebar: WORN, card: WORN } });
  });

  it('and a colour the tones cannot be read on is refused, by name, unwritten', () => {
    mount();
    fireEvent.change(input('Cards'), { target: { value: REFUSED } });
    expect(setTheme, `${REFUSED} was written though the gate refuses it`).not.toHaveBeenCalled();
    expect(screen.getByText(/would not be readable/i)).toBeTruthy();
  });
});

describe('what the row says', () => {
  it('gives every ground its own line, and it is that ground\'s own words', () => {
    mount();
    for (const g of GROUNDS) expect(screen.getByText(g.description)).toBeTruthy();
  });

  it('and names what is still painting when the colour you stopped at is refused', () => {
    // A colour input reports every frame of a drag, so the refused
    // colour is often NOT the one on screen: several accepted ones went
    // by on the way, and the last of them is what stayed. Saying only
    // "that would not be readable" beside a plane that visibly changed
    // is the app contradicting itself.
    mount({ grounds: { card: WORN } });
    fireEvent.change(input('Cards'), { target: { value: REFUSED } });
    expect(setTheme, 'the refused colour was written').not.toHaveBeenCalled();
    expect(screen.getByText(/still on/i)).toBeTruthy();
  });
});

describe('clearing one', () => {
  it('drops that key, keeps the other, and offers an undo', () => {
    mount({ grounds: { card: WORN, sidebar: WORN } });
    const clears = screen.getAllByRole('button', { name: /^clear$/i });
    fireEvent.click(clears[clears.length - 1]);
    expect(setTheme).toHaveBeenCalledWith({ grounds: { card: WORN } });
    expect(undoableAction).toHaveBeenCalled();
  });

  it('and clearing the last one removes the object rather than leaving it empty', () => {
    mount({ grounds: { card: WORN } });
    fireEvent.click(screen.getAllByRole('button', { name: /^clear$/i })[0]);
    expect(setTheme).toHaveBeenCalledWith({ grounds: undefined });
  });
});

describe('a seed the mode cannot wear', () => {
  it('says so instead of pretending it is on', () => {
    // The engine drops it on the paint path; the panel's job is to stop
    // the person wondering why nothing changed.
    const light = GREYS.find((h) =>
      groundTokens('card', h, 'dark').tokens !== null
      && groundTokens('card', h, 'light').tokens === null);
    if (!light) return;                       // no such colour today: nothing to claim
    mount({ mode: 'light', grounds: { card: light } });
    expect(screen.getByText(/not worn in light mode/i)).toBeTruthy();
  });
});

/**
 * The Mods panel is 224px, and it is opened to change something now.
 *
 * So it offers the two answers that are about THIS moment — everywhere,
 * or the page in front of you — and leaves the list of every themable
 * place, and every line explaining what a plane is, to the Mods page.
 * A place the person is not looking at is a list to read, not a choice
 * to make.
 */
describe('what the panel narrows', () => {
  it('offers Everywhere and the page the person is on, and no other place', () => {
    standingOn('loads');
    mount({}, true);
    expect(screen.getByRole('button', { name: 'Everywhere' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Loads' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Live Map' }),
      'the panel lists a place the person is not on').toBeNull();
  });

  it('and drops the row entirely where the page has no look of its own', () => {
    standingOn(undefined);                 // e.g. Overview, Settings, /mods itself
    mount({}, true);
    expect(screen.queryByRole('button', { name: 'Everywhere' }),
      'a question with one answer is a decoration').toBeNull();
  });

  it('while the Mods page still offers every place', () => {
    standingOn('loads');
    mount({}, false);
    // Scoped to Color's own row: Wallpaper has an "applies to" of its
    // own on this page, with an Everywhere of its own.
    const row = screen.getByText('Background applies to').parentElement as HTMLElement;
    for (const name of ['Everywhere', 'Live Map', 'Loads'])
      expect(within(row).getByRole('button', { name }), `${name} is missing from the page`).toBeTruthy();
  });

  it('and the grounds keep their chips but not their descriptions', () => {
    standingOn('loads');
    mount({}, true);
    for (const g of GROUNDS) {
      expect(screen.getByRole('button', { name: g.label }), `${g.label} left the panel`).toBeTruthy();
      expect(screen.queryByText(g.description), `${g.label}: the panel is explaining rather than offering`).toBeNull();
    }
  });

  it('but still says when a colour cannot be worn here', () => {
    // The two things that ARE about this moment stay: a refusal, and a
    // stored colour this mode is refusing to wear.
    mount({}, true);
    fireEvent.change(input('Cards'), { target: { value: REFUSED } });
    expect(screen.getByText(/would not be readable/i)).toBeTruthy();
  });
});

/**
 * A drag is one decision, so it is one write.
 *
 * `onChange` on a colour input is the `input` event: dragging across
 * the picker used to write per frame — the theme stored, the account
 * blob published, the whole palette re-derived, dozens of times a
 * second — and worse, frames the gate refused were dropped while frames
 * it accepted were kept, so releasing on a refused colour left a
 * different one painted.
 */
describe('a drag writes once', () => {
  const fireDrag = (el: HTMLInputElement, frames: string[], final: string) => {
    for (const f of frames) {
      el.value = f;
      fireEvent.input(el);
    }
    el.value = final;
    fireEvent.change(el);
  };

  it('at the colour the person stopped on, whatever it passed through', () => {
    mount({ grounds: { card: WORN } });
    const el = input('Cards');
    fireDrag(el, [REFUSED, WORN, REFUSED], WORN2);
    expect(setTheme.mock.calls.length, 'a write per frame').toBe(1);
    expect(setTheme).toHaveBeenCalledWith({ grounds: { card: WORN2 } });
  });

  it('and writes nothing at all when the colour it stopped on is refused', () => {
    mount({ grounds: { card: WORN } });
    fireDrag(input('Cards'), [WORN2], REFUSED);
    expect(setTheme, 'the refused colour was written').not.toHaveBeenCalled();
    expect(screen.getByText(/would not be readable/i)).toBeTruthy();
  });

  it('while the note follows the pointer, so a refusal is learned mid-drag', () => {
    mount();
    const el = input('Cards');
    el.value = REFUSED;
    fireEvent.input(el);                       // still dragging: nothing written
    expect(setTheme).not.toHaveBeenCalled();
    expect(screen.getByText(/would not be readable/i),
      'the person learns it only after letting go').toBeTruthy();
  });
});

/**
 * The tail of the audit: the words on this row, and the order it asks
 * its two questions in.
 */
describe('the row asks the place first, then the colour', () => {
  it('puts "applies to" above the picker it scopes', () => {
    standingOn('loads');
    mount({}, false);
    const html = document.body.innerHTML;
    expect(html.indexOf('Background applies to'), 'the picker comes first, so a first-time pick lands on Everywhere')
      .toBeLessThan(html.indexOf('Background ·') >= 0 ? html.indexOf('Background ·') : html.indexOf('>Background<'));
  });

  it('and the picker names the place it is aimed at', () => {
    standingOn('loads');
    mount({}, false);
    // Scoped: Wallpaper has a "Pattern applies to" row with a Loads of
    // its own on this page.
    const row = screen.getByText('Background applies to').parentElement as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Loads' }));
    expect(screen.getByRole('button', { name: 'Background · Loads' }),
      'aimed at a place, the chip still reads like the global one').toBeTruthy();
  });

  it('and a refusal names the mode, since the same colour may be fine in the other', () => {
    mount({}, false);
    const el = input('Cards');
    el.value = REFUSED;
    fireEvent.input(el);
    expect(screen.getByText(/in dark mode/i), 'the refusal never says which mode refused it').toBeTruthy();
  });
});
