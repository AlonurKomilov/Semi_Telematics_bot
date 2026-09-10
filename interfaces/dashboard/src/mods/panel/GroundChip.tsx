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
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from '../../lib/icons';
import { cn } from '../../lib/utils';
import { undoableAction } from '../../components/banners/stagedAction';
import { groundTokens, type Ground } from '../theme/grounds';
import type { Mode } from '../context';

const TONE_NAMES: Record<string, string> = {
  ok: 'the success colour',
  warn: 'the warning colour',
  danger: 'the danger colour',
  info: 'the info colour',
};

export function GroundChip({ ground, hex, mode, fallback, onPick, onClear }: {
  ground: Ground;
  hex?: string;
  mode: Mode;
  /** What paints while this ground has no seed — the value the page's
   *  own palette derived. The picker opens on it, so the first drag
   *  starts from what is on screen rather than from black. */
  fallback: string;
  onPick: (hex: string) => void;
  onClear: () => void;
}) {
  const { t } = useTranslation();
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

  const note = refused
    ? t('theme.ground_refused', '{{tone}} would not be readable on that.')
        .replace('{{tone}}', (TONE_NAMES[refused.tone] ?? refused.tone).replace(/^the /, 'The '))
    : hex && !worn
      ? t('theme.ground_unworn', 'Not worn in {{mode}} mode — the built-in one is painting.')
          .replace('{{mode}}', mode)
      : null;

  return (
    <>
      <span className="relative inline-flex">
        <button
          type="button"
          className={cn(
            'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition-colors min-h-tap',
            hex && worn
              ? 'bg-primary/15 text-foreground ring-1 ring-primary/40'
              : 'text-muted-foreground hover:text-foreground hover:bg-muted/60',
          )}
        >
          <span
            aria-hidden
            className="w-2.5 h-2.5 rounded-full shrink-0 border border-border"
            style={{ background: (worn && hex) || fallback }}
          />
          {t(`theme.ground_${ground.id}`, ground.label)}
        </button>
        <input
          type="color"
          value={hex ?? fallback}
          onChange={(e) => pick(e.target.value)}
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
        <p className="basis-full text-2xs leading-snug text-muted-foreground mt-0.5">
          {note}
        </p>
      )}
    </>
  );
}
