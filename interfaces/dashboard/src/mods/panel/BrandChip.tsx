/**
 * The custom-colour chip, and the words it says when a colour is refused.
 *
 * Its own file rather than a corner of `Interface.tsx`: it is 130 lines
 * against the ~30 each of the other Interface groups, it owns the only
 * local state in the whole panel that is not a slider drag, and it is
 * the one control that can REFUSE what a person asked for — which is
 * enough behaviour to be worth finding on its own.
 */
import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';
import { cn } from '../../lib/utils';
import { undoableAction } from '../../components/banners/stagedAction';
import { accentTokens } from '../theme/accent';
import type { Mode } from '../context';

/**
 * What a tone is called to somebody who is not reading our CSS.
 *
 * The token names are `ok` / `warn` / `danger` / `info` because that is
 * what they do in a stylesheet. "Your colour is too close to --ok" is
 * not a sentence anybody can act on.
 */
const TONE_NAMES: Record<string, string> = {
  ok: 'the success colour',
  warn: 'the warning colour',
  danger: 'the danger colour',
  info: 'the info colour',
};

/**
 * The colour nobody curated.
 *
 * The four packs beside it were each tuned by hand; this one is checked
 * by `accentTokens` at the moment it is picked, and it can come back
 * refused. That refusal is the feature — a primary button the colour of
 * `--danger` is a lie about what the button does — so it is SAID, not
 * swallowed, and the tone it collided with is named in words a person
 * can act on.
 *
 * A nudge is said too. The engine moves a colour's lightness a little to
 * get it clear of a tone, and a colour that came back slightly different
 * with no explanation is the kind of thing that reads as a bug.
 *
 * A native colour input rather than a hand-built wheel: it is the
 * platform's own picker, it is keyboard-accessible and localised for
 * free, and it costs nothing to ship. It sits invisibly ON the chip, so
 * the chip is the hit target and the input never has to be styled.
 */
export function BrandChip({ brand, mode, wearing, onPick, onClear }: {
  brand?: string;
  mode: Mode;
  /** The pack seed currently painting, for the mode being worn. The
   *  picker opens HERE when nothing custom is set: a colour picker that
   *  opens on grey is a blank decision wearing the shape of a value, and
   *  the honest starting point is the colour already on the screen. */
  wearing: string;
  onPick: (hex: string) => void;
  onClear: () => void;
}) {
  const { t } = useTranslation();
  const [refused, setRefused] = useState<string | null>(null);

  // What the stored colour actually does in the mode being worn. A hex
  // can clear the tones on near-black and collide on white, so "is a
  // custom colour painting right now" is a question about this mode, not
  // about whether the field is set.
  const worn = useMemo(
    () => (brand ? accentTokens(brand, mode) : null),
    [brand, mode],
  );
  const active = !!worn?.tokens;

  const pick = (hex: string) => {
    const r = accentTokens(hex, mode);
    if (!r.tokens) {
      setRefused(r.collidesWith ?? null);
      return;
    }
    setRefused(null);
    onPick(hex);
  };

  const note = refused
    ? t('theme.brand_refused', 'That colour reads as {{tone}}. Pick another.')
        .replace('{{tone}}', TONE_NAMES[refused] ?? refused)
    : worn?.movedFrom
      ? t('theme.brand_moved', 'Lightened away from {{tone}} so the two do not read alike.')
          .replace('{{tone}}', TONE_NAMES[worn.movedFrom] ?? worn.movedFrom)
      : brand && !active
        ? t('theme.brand_unworn', 'This colour cannot be worn in {{mode}} mode — the pack below is painting.')
            .replace('{{mode}}', mode)
        : null;

  return (
    <>
      <span className="relative inline-flex">
        <button
          type="button"
          aria-pressed={active}
          className={cn(
            'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition-colors min-h-tap',
            active
              ? 'bg-primary/15 text-foreground ring-1 ring-primary/40'
              : 'text-muted-foreground hover:text-foreground hover:bg-muted/60',
          )}
        >
          <span
            aria-hidden
            className="w-2.5 h-2.5 rounded-full shrink-0 border border-border"
            style={{ background: brand ?? 'var(--muted-foreground)' }}
          />
          {t('theme.accent_custom', 'Custom')}
        </button>
        {/* Over the whole chip, invisible, so the chip IS the control.
            `sr-only` would take it out of the pointer's way entirely. */}
        <input
          type="color"
          value={brand ?? wearing}
          onChange={(e) => pick(e.target.value)}
          aria-label={t('theme.accent_custom', 'Custom')}
          className="absolute inset-0 w-full min-h-tap opacity-0 cursor-pointer"
        />
      </span>
      {/* Offered whenever a colour is STORED, not only while it paints.
          Gating this on `active` stranded the exact person who most
          needs it: 80 hexes clear the tones on near-black and collide on
          white, so a colour picked in dark mode and then carried into
          light shows the advisory below with no way to act on it. */}
      {brand && (
        // An ACTION sitting in a row of SELECTIONS, so it does not wear
        // a selection's box. Same rule the rest of the app follows: one
        // shape per meaning class, or the eye has to read every element
        // to find out which are choices and which do something.
        <button
          type="button"
          onClick={() => {
            setRefused(null);
            const was = brand;
            onClear();
            // The colour is a hex somebody chose by eye. Losing it costs
            // them the search, not a click — and every other destructive
            // control on this surface (all five resets, and installing a
            // mod over hand-set axes) already offers the way back.
            undoableAction({
              label: 'Custom colour cleared',
              undo: async () => { if (was) onPick(was); },
            });
          }}
          className="inline-flex items-center gap-1 px-1 text-xs text-muted-foreground hover:text-foreground min-h-tap"
        >
          <X className="size-3" aria-hidden />
          {t('theme.brand_clear', 'Clear')}
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
