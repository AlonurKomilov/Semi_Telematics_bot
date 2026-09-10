/**
 * The background, and the palette that follows from it.
 *
 * A sibling of `BrandChip` and deliberately not part of it: picking an
 * accent tints the interactive things, picking a canvas repaints every
 * surface in the app. Two sizes of decision should not sit in one row
 * looking alike.
 *
 * Like the accent, it can REFUSE. The reason is different and worth
 * saying plainly: `derivePalette` guarantees everything it derives, but
 * the semantic tones — success, warning, danger, info — are deliberately
 * beyond a seed's reach, and they follow the mode rather than the
 * canvas. So a canvas chosen against its mode leaves them unreadable,
 * measured as low as 1.56:1. The refusal names which one.
 */
import { useId, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from '../../lib/icons';
import { cn } from '../../lib/utils';
import { undoableAction } from '../../components/banners/stagedAction';
import { fitCanvas, CANVAS_SEED } from '../theme/canvas';
import type { Mode } from '../context';
import { useColorProbe } from './useColorProbe';

/** What a tone is called to somebody who is not reading our CSS. */
const TONE_NAMES: Record<string, string> = {
  ok: 'the success colour',
  warn: 'the warning colour',
  danger: 'the danger colour',
  info: 'the info colour',
};

export function CanvasChip({ canvas, mode, onPick, onClear }: {
  canvas?: string;
  mode: Mode;
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

  /** Whether the stored canvas is wearable in the mode being worn. The
   *  same hex can be fine on one side and unreadable on the other. */
  const worn = useMemo(
    () => (canvas ? fitCanvas(canvas, mode).rgb !== null : false),
    [canvas, mode],
  );

  const pick = (hex: string) => {
    const fit = fitCanvas(hex, mode);
    if (!fit.rgb) {
      setRefused({ tone: fit.breaks ?? '', ratio: fit.ratio ?? 0 });
      return;
    }
    setRefused(null);
    onPick(hex);
  };

  // A colour input reports every frame of a drag: a person can stop on
  // a colour the gate refuses having passed through several it took,
  // and one of those is still painting. The note names both rather than
  // describing a background nobody is looking at.
  /** What the chip is showing: the frame under the pointer while a drag
   *  is happening, otherwise what is stored, otherwise the derived
   *  colour this plane already paints. */
  const shown = canvas ?? CANVAS_SEED[mode];
  const { ref: probeRef, probe } = useColorProbe(shown, pick);

  // The gate, run on the frame under the pointer — so "this one would
  // break the warning colour" is learned while dragging rather than
  // after letting go. It writes nothing; `pick` still commits once.
  const probeBreak = probe ? (() => { const f = fitCanvas(probe, mode); return f.rgb ? null : f; })() : null;

  const live = probeBreak ? { tone: probeBreak.breaks ?? '', ratio: probeBreak.ratio ?? 0 } : refused;
  const note = live
    ? (canvas && worn
      ? t('theme.canvas_refused_kept', '{{tone}} would not be readable on the colour you stopped at — the last background that worked is still on.')
          .replace('{{tone}}', (TONE_NAMES[live.tone] ?? live.tone).replace(/^the /, 'The '))
      : t('theme.canvas_refused', '{{tone}} would not be readable on that background.')
          .replace('{{tone}}', (TONE_NAMES[live.tone] ?? live.tone).replace(/^the /, 'The ')))
    : canvas && !worn
      ? t('theme.canvas_unworn', 'This background cannot be worn in {{mode}} mode — the built-in one is painting.')
          .replace('{{mode}}', mode)
      : canvas
        ? t('theme.canvas_on', 'Every surface is derived from this — and checked for readability.')
        : null;

  return (
    <>
      <span className="relative inline-flex">
        <button
          type="button"
          aria-pressed={worn}
          onClick={() => probeRef.current?.click()}
          className={cn(
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
            'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition-colors min-h-tap',
            worn
              ? 'bg-primary/15 text-foreground ring-1 ring-primary/40'
              : 'text-muted-foreground hover:text-foreground hover:bg-muted/60',
          )}
        >
          <span
            aria-hidden
            className="w-2.5 h-2.5 rounded-full shrink-0 border border-border"
            style={{ background: probe ?? canvas ?? 'var(--background)' }}
          />
          {t('theme.canvas_label', 'Background')}
        </button>
        <input
          ref={probeRef}
          tabIndex={-1}
          aria-hidden
          aria-describedby={note ? noteId : undefined}
          type="color"
          defaultValue={shown}
          aria-label={t('theme.canvas_label', 'Background')}
          className="absolute inset-0 w-full min-h-tap opacity-0 cursor-pointer"
        />
      </span>
      {canvas && (
        <button
          type="button"
          onClick={() => {
            setRefused(null);
            const was = canvas;
            onClear();
            undoableAction({
              label: 'Background cleared',
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
