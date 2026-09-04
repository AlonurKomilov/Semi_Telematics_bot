/**
 * The global size slider — the popover's one shortcut into Size.
 *
 * The only control deliberately on both surfaces, and the page does NOT
 * repeat it: there `SizeCard` owns size whole (global, per region, the
 * cross-device switch). "A bit bigger" is the case almost everyone
 * wants, and making them open a page for it would be the wrong trade.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { RotateCcw } from 'lucide-react';
import { Slider } from '../../components/ui/slider';
import { Tip } from '../../components/tooltip';
import { SIZE_MIN, SIZE_MAX } from '../../preferences';
import { useMods, applySize } from '../context';
import type { LabelClass } from './Interface';

export function SizeSlider({ label: groupLabel }: { label: LabelClass }) {
  const { t } = useTranslation();
  const { size, setSize } = useMods();
  // What the slider shows while a drag is in flight. The stored value is
  // only written on release — see the note in ui/slider.tsx — so during a
  // drag the preference and the screen disagree by design, and this holds
  // the screen's value. `null` means "not dragging, read the preference".
  const [dragging, setDragging] = useState<number | null>(null);
  const shown = dragging ?? size.global;

  return (
    /* Size — replaces the Density chips, which changed nothing.
       The scale runs 100% → 150%, not around a midpoint: the lower
       half stays unavailable until the 24px hit-target floor is
       repaired (design.md §5.1). So the handle rests at its own
       minimum by default, and the live percentage beside the label
       is what tells the user the control is working — it is the
       only readout, which is why it sits in the label row rather
       than under the track. */
    <div>
      {/* Label, current value and reset share one row. The range ends
          were spelled out under the track at first, which put a
          second "100%" directly below the current value whenever the
          slider sat at its minimum — which is the default, so most
          users would have met the confusing state first. */}
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <p className={groupLabel}>
          {t('mods.group_size', 'Interface size')}
        </p>
        <div className="flex items-center gap-1.5">
          <span className="text-2xs tabular-nums text-muted-foreground">
            {Math.round(shown * 100)}%
          </span>
          <Tip label={t('mods.size_reset', 'Reset')}>
            <button
              type="button"
              onClick={() => { setDragging(null); setSize({ global: 1 }); }}
              disabled={size.global === 1 && dragging === null}
              aria-label={t('mods.size_reset', 'Reset')}
              className="inline-flex size-5 min-h-tap min-w-tap items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted-foreground"
            >
              <RotateCcw className="size-3" />
            </button>
          </Tip>
        </div>
      </div>
      <Slider
        value={shown}
        min={SIZE_MIN}
        max={SIZE_MAX}
        step={0.05}
        aria-label={t('mods.size_label', 'Interface size')}
        formatValue={(v) => `${Math.round(v * 100)}%`}
        // Live: paint straight to the DOM so the drag is smooth and
        // React is not re-rendered 60 times for one gesture.
        onValueChange={(v) => { setDragging(v); applySize({ ...size, global: v }); }}
        // Committed: now it becomes the stored preference.
        onValueCommitted={(v) => { setDragging(null); setSize({ global: v }); }}
      />
    </div>
  );
}
