/**
 * What "Interface sounds" could not answer about itself.
 *
 * Keyboard and Background each show their pack on their own row,
 * because each OWNS one. This item does not, and correctly so: the cue
 * set belongs to the category, since `playBannerCue` reads the same
 * preference as `playUiCue` — choosing Blip chooses what an alert
 * sounds like too.
 *
 * What that left was a page with one switch on it. No name for the cue
 * set, no way to hear it, and no sign the choice lived one level up. A
 * person turned it on, did nothing that answers, heard nothing, and
 * concluded the feature was broken — which is exactly what happened.
 *
 * So this file holds three things: the pack is NAMED, it is PLAYABLE,
 * and it is not a second picker.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

const { playCue, armAudio } = vi.hoisted(() => ({
  playCue: vi.fn(), armAudio: vi.fn(),
}));
vi.mock('../sound/engine', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  playCue, armAudio,
}));

vi.mock('react-i18next', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTranslation: () => ({
    t: (_k: string, d?: string, vars?: Record<string, string>) =>
      (d ?? _k).replace(/\{\{(\w+)\}\}/g, (_m, n) => vars?.[n] ?? ''),
  }),
}));

/** The three preferences this item reads, per test. */
let prefs: Record<string, unknown> = {};
vi.mock('../../preferences', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  usePreference: (k: string) => ({ value: prefs[k], setValue: vi.fn() }),
}));

import { InterfaceSoundItem } from './Sounds';
import { SOUND_PACKS } from '../store/items/sound';

const packOf = (id: string) => SOUND_PACKS.find((p) => p.id === id)!;

beforeEach(() => {
  playCue.mockClear(); armAudio.mockClear();
  prefs = { 'mods.sound.ui': true, 'mods.sound.volume': 1, 'mods.sound.pack': 'chime' };
});
afterEach(cleanup);

describe('the cue set says its own name', () => {
  it('names the pack in use', () => {
    render(<InterfaceSoundItem />);
    expect(screen.getByRole('button', { name: /Hear Chime/ })).toBeTruthy();
  });

  it('and plays THAT pack — not a hardcoded one', () => {
    // The regression this exists for: a preview wired to one pack's cue
    // sounds right on the day it is written and lies for everybody who
    // ever changes the setting.
    prefs['mods.sound.pack'] = 'blip';
    render(<InterfaceSoundItem />);
    fireEvent.click(screen.getByRole('button', { name: /Hear Blip/ }));
    expect(playCue).toHaveBeenCalledWith(packOf('blip').cues.success, 1);
    expect(playCue).not.toHaveBeenCalledWith(packOf('chime').cues.success, 1);
  });

  /** `success` and not `alert`: the category's own preview plays the
   *  arriving-alert cue, and this row is about the app ANSWERING —
   *  which is the first thing its hint names. */
  it('plays the cue this row is actually about', () => {
    render(<InterfaceSoundItem />);
    fireEvent.click(screen.getByRole('button', { name: /Hear Chime/ }));
    expect(playCue).toHaveBeenCalledWith(packOf('chime').cues.success, 1);
  });

  it('at the level the person set, so the preview is what they will hear', () => {
    prefs['mods.sound.volume'] = 0.4;
    render(<InterfaceSoundItem />);
    fireEvent.click(screen.getByRole('button', { name: /Hear Chime/ }));
    expect(playCue).toHaveBeenCalledWith(packOf('chime').cues.success, 0.4);
  });

  /** The whole point of the button is the FIRST press: a browser grants
   *  audio on a gesture, and this is the gesture. Arming after the fact
   *  would make the first try silent — the one try that decides whether
   *  somebody believes the feature works. */
  it('and the first press is the one that unlocks audio', () => {
    render(<InterfaceSoundItem />);
    fireEvent.click(screen.getByRole('button', { name: /Hear Chime/ }));
    expect(armAudio).toHaveBeenCalled();
  });
});

describe('what it refuses to become', () => {
  it('is not a second picker for a setting it does not own', () => {
    // Two controls for one shared preference is how two controls start
    // disagreeing. The pack is the category's; this row reports it.
    render(<InterfaceSoundItem />);
    expect(screen.queryByRole('button', { name: /Hear Blip/ }), 'a pack picker grew here')
      .toBeNull();
    expect(screen.getByText(/chosen under Sound/)).toBeTruthy();
  });

  it('says nothing at all while the switch is off', () => {
    prefs['mods.sound.ui'] = false;
    render(<InterfaceSoundItem />);
    expect(screen.queryByRole('button', { name: /Hear/ })).toBeNull();
  });
});

describe('silenced is a state, not a dead button', () => {
  it('refuses the press and says why IN THE PAGE', () => {
    // Not a tooltip: a disabled button swallows pointer events, so a tip
    // on it never opens — and this is the one state where the person
    // most needs telling why nothing happened.
    prefs['mods.sound.volume'] = 0;
    render(<InterfaceSoundItem />);
    expect((screen.getByRole('button', { name: /Hear Chime/ }) as HTMLButtonElement).disabled)
      .toBe(true);
    expect(screen.getByText(/silenced/i)).toBeTruthy();
    expect(screen.queryByText(/chosen under Sound/), 'both notes at once')
      .toBeNull();
  });
});
