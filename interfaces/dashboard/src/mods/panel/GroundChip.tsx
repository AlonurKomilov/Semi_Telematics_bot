/**
 * One ground, picked — the same shape as the background's chip, because
 * it is the same decision one plane over.
 *
 * A ground is a SEED: what a person picks here is turned into that
 * plane's whole family — its ink, its hover fill, its edge — by
 * `groundTokens`, which refuses a colour the semantic tones cannot be
 * read on exactly as the background's gate does. The refusal is shown
 * here as a courtesy; the engine refuses again on the path that paints,
 * so a colour that stops being wearable in the other mode simply is not
 * worn.
 */
import { useId, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from '../../lib/icons';
import { cn } from '../../lib/utils';
import { undoableAction } from '../../components/banners/stagedAction';
import { groundTokens, type Ground } from '../theme/grounds';
import type { Mode } from '../context';
import { useColorProbe } from './useColorProbe';

const TONE_NAMES: Record<string, string> = {
  ok: 'the success colour',
  warn: 'the warning colour',
  danger: 'the danger colour',
  info: 'the info colour',
};

export function GroundChip({ ground, hex, mode, fallback, compact = false, onPick, onClear }: {
  ground: Ground;
  hex?: string;
  mode: Mode;
  /** The Mods panel. What a plane IS belongs on the page; here the line
   *  is kept for the two things that are about THIS moment — a refusal,
   *  and a colour this mode cannot wear. */
  compact?: boolean;
  /** What paints while this ground has no seed — the value the page's
   *  own palette derived. The picker opens on it, so the first drag
   *  starts from what is on screen rather than from black. */
  fallback: string;
  onPick: (hex: string) => void;
  onClear: () => void;
}) {
  const { t } = useTranslation();
  // ONE announced control. The swatch button used to be a dead tab stop
  // — no handler — in front of an `opacity-0` input with the same name,
  // so a keyboard user tabbed twice, pressed Enter on nothing, and got
  // no focus ring at all on the control that works. The button opens the
  // picker now and keeps its pressed state; the input is reachable only
  // through it.
  const noteId = useId();
  const [refused, setRefused] = useState<{ tone: string; ratio: number } | null>(null);

  const worn = useMemo(
    () => (hex ? groundTokens(ground.id, hex, mode).tokens !== null : false),
    [ground.id, hex, mode],
  );

  const pick = (next: string) => {
    const fit = groundTokens(ground.id, next, mode);
    if (!fit.tokens) {
      setRefused({ tone: fit.breaks ?? '', ratio: fit.ratio ?? 0 });
      return;
    }
    setRefused(null);
    onPick(next);
  };

  // A colour input reports every frame of a drag, so a person can land
  // on a colour the gate refuses having passed through several it
  // accepted — and the accepted one is what stays painted. Saying only
  // "that would not be readable" beside a plane that visibly changed
  // reads as the app contradicting itself, so the note names BOTH: what
  // was refused, and what is on screen instead.
  /** What the chip is showing: the frame under the pointer while a drag
   *  is happening, otherwise what is stored, otherwise the derived
   *  colour this plane already paints. */
  const shown = hex ?? fallback;
  const { ref: probeRef, probe } = useColorProbe(shown, pick);

  // The gate, run on the frame under the pointer — so "this one would
  // break the warning colour" is learned while dragging rather than
  // after letting go. It writes nothing; `pick` still commits once.
  const probeBreak = probe ? (() => { const f = groundTokens(ground.id, probe, mode); return f.tokens ? null : f; })() : null;

  const live = probeBreak ? { tone: probeBreak.breaks ?? '', ratio: probeBreak.ratio ?? 0 } : refused;
  const tone = live
    ? (TONE_NAMES[live.tone] ?? live.tone).replace(/^the /, 'The ')
    : '';
  const note = live
    ? (hex && worn
      ? t('theme.ground_refused_kept', '{{tone}} would not be readable on the colour you stopped at — the last one that worked is still on.')
          .replace('{{tone}}', tone)
      : t('theme.ground_refused', '{{tone}} would not be readable on that.').replace('{{tone}}', tone))
    : hex && !worn
      ? t('theme.ground_unworn', 'Not worn in {{mode}} mode — the built-in one is painting.')
          .replace('{{mode}}', mode)
      : compact ? null : t(`theme.ground_${ground.id}_hint`, ground.description);

  return (
    <>
      <span className="relative inline-flex">
        <button
          type="button"
          onClick={() => probeRef.current?.click()}
          aria-pressed={Boolean(hex) && worn}
          className={cn(
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
            'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition-colors min-h-tap',
            hex && worn
              ? 'bg-primary/15 text-foreground ring-1 ring-primary/40'
              : 'text-muted-foreground hover:text-foreground hover:bg-muted/60',
          )}
        >
          <span
            aria-hidden
            className="w-2.5 h-2.5 rounded-full shrink-0 border border-border"
            style={{ background: probe ?? ((worn && hex) || fallback) }}
          />
          {t(`theme.ground_${ground.id}`, ground.label)}
        </button>
        <input
          ref={probeRef}
          tabIndex={-1}
          aria-hidden
          aria-describedby={note ? noteId : undefined}
          type="color"
          defaultValue={shown}
          aria-label={t(`theme.ground_${ground.id}`, ground.label)}
          className="absolute inset-0 w-full min-h-tap opacity-0 cursor-pointer"
        />
      </span>
      {hex && (
        <button
          type="button"
          onClick={() => {
            setRefused(null);
            const was = hex;
            onClear();
            undoableAction({
              label: `${ground.label} cleared`,
              undo: async () => { if (was) onPick(was); },
            });
          }}
          className="inline-flex items-center gap-1 px-1 text-xs text-muted-foreground hover:text-foreground min-h-tap"
        >
          <X className="size-3" aria-hidden />
          {t('theme.canvas_clear', 'Clear')}
        </button>
      )}
      {note && (
        <p
          id={noteId}
          role="status"
          aria-live="polite"
          className="basis-full text-2xs leading-snug text-muted-foreground mt-0.5"
        >
          {note}
        </p>
      )}
    </>
  );
}
