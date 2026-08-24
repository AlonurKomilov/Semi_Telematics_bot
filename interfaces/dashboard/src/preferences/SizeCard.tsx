/**
 * "Appearance size" — the full Size surface.
 *
 * The topbar picker carries the same global slider, because that is the
 * case almost everyone wants ("a bit bigger"). What lives HERE and not
 * there is the cross-device switch — a settings decision rather than a
 * quick toggle, and one a w-56 popover cannot hold without inventing a
 * width design.md §7 forbids.
 *
 * The six per-region sliders live here too, behind a disclosure. They
 * MULTIPLY the global rather than replacing it, so 110% on Tables over a
 * 120% interface renders at 132% — the composition is done by the
 * cascade, in `tailwind.config.js`, not by this page. Each region is
 * claimed by the surface that already owns it (`lib/sizeRegion.ts`).
 */
import { useState } from 'react';
import { RotateCcw, ChevronRight } from 'lucide-react';

import { Slider } from '../components/ui/slider';
import { Switch } from '../components/ui/switch';
import { InfoTip } from '../components/tooltip';
import { useTheme, applySize } from '../context/ThemeContext';
import { usePreference } from './usePreference';
import { publishAppearanceDefault, resetAppearanceDefault } from './appearance';
import { SIZE_MIN, SIZE_MAX, SIZE_DEFAULT } from './registry';
import type { SizeRegion as SizeRegionKey } from './registry';
import { Card } from '@/components/ui/card';

const pct = (v: number) => `${Math.round(v * 100)}%`;

/** One labelled row. Live value paints straight to the DOM; only the
 *  committed value becomes a preference — a drag is ~60 frames and each
 *  one would otherwise be a synchronous localStorage write. */
function SizeRow({
  label, value, onPreview, onCommit, ariaLabel,
}: {
  label: string;
  value: number;
  onPreview: (v: number) => void;
  onCommit: (v: number) => void;
  ariaLabel: string;
}) {
  const [drag, setDrag] = useState<number | null>(null);
  const shown = drag ?? value;
  return (
    // Stacks below `sm`: the label rides --size-text, so at a large Size
    // on a phone a fixed-width label and value would leave the track no
    // travel at all.
    <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
      <span className="text-sm text-foreground sm:w-40 sm:shrink-0">{label}</span>
      <Slider
        value={shown}
        min={SIZE_MIN}
        max={SIZE_MAX}
        step={0.05}
        aria-label={ariaLabel}
        formatValue={pct}
        onValueChange={(v) => { setDrag(v); onPreview(v); }}
        onValueCommitted={(v) => { setDrag(null); onCommit(v); }}
        className="max-w-xs"
      />
      <span className="text-xs tabular-nums text-muted-foreground sm:w-12 sm:text-right sm:shrink-0">
        {pct(shown)}
      </span>
    </div>
  );
}

/** Region key -> what the user calls that part of the screen. Ordered
 *  the way the eye moves through the app, not alphabetically. The keys
 *  are frozen (registry.ts); only these labels are editable. */
const REGION_ROWS: { key: SizeRegionKey; label: string }[] = [
  { key: 'text',       label: 'Main content' },
  { key: 'tables',     label: 'Tables' },
  { key: 'controls',   label: 'Top bar' },
  { key: 'navigation', label: 'Sidebar' },
  { key: 'overlays',   label: 'Dialogs & panels' },
  { key: 'assistant',  label: 'Assistant' },
];

export default function SizeCard() {
  const { size, setSize } = useTheme();
  const [open, setOpen] = useState(false);
  const { value: followMe, setValue: setFollowMe } = usePreference('appearance.followMe');
  const { value: syncEnabled } = usePreference('prefs.syncEnabled');

  const resetAll = () => {
    setSize(SIZE_DEFAULT);
    // Reset has to reach the synced copy too, or the next browser the
    // user signs in on restores exactly what they just discarded.
    resetAppearanceDefault();
  };

  const tuned = REGION_ROWS.filter(
    (r) => (size.regions[r.key] ?? 1) !== 1,
  ).length;
  const atDefault = size.global === 1 && tuned === 0;

  return (
    <Card className="scroll-mt-20" render={<section />} id="appearance">
      <div className="flex items-start justify-between gap-4 mb-1">
        <h2 className="text-lg font-semibold">Appearance size</h2>
        <button
          type="button"
          onClick={resetAll}
          disabled={atDefault}
          className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:hover:text-muted-foreground shrink-0 py-1 -my-1 min-h-tap"
        >
          <RotateCcw className="size-3.5" />
          Reset all
        </button>
      </div>
      <p className="text-xs text-muted-foreground mb-4">
        Resize the interface. Applies to this browser; the switch below shares it with your other ones.
      </p>

      <div className="space-y-3">
        <SizeRow
          label="Everything"
          ariaLabel="Interface size"
          value={size.global}
          onPreview={(v) => applySize({ ...size, global: v })}
          onCommit={(v) => setSize({ global: v })}
        />

        {/* Per-area sliders are BEHIND a disclosure, not stacked under
            the first one. "Everything" is the control almost everyone
            wants; six more of the same shape beside it would make the
            page read as six equal decisions and bury the one that
            matters. Open, they show what they are worth — each one
            multiplies the global rather than replacing it. */}
        <div className="pt-1">
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground py-1 -my-1 min-h-tap"
          >
            <ChevronRight
              className={`size-3.5 transition-transform ${open ? 'rotate-90' : ''}`}
              aria-hidden
            />
            Fine-tune by area
            {tuned > 0 && (
              <span className="text-2xs tabular-nums text-primary">
                {tuned} adjusted
              </span>
            )}
          </button>

          {open && (
            <div className="mt-3 space-y-3 border-l border-border pl-3">
              <p className="text-xs text-muted-foreground">
                Each area multiplies the size above — 110% here on a 120%
                interface renders at 132%.
              </p>
              {REGION_ROWS.map(({ key, label }) => (
                <SizeRow
                  key={key}
                  label={label}
                  ariaLabel={`${label} size`}
                  value={size.regions[key] ?? 1}
                  onPreview={(v) => applySize({
                    ...size, regions: { ...size.regions, [key]: v },
                  })}
                  onCommit={(v) => setSize({
                    regions: { ...size.regions, [key]: v },
                  })}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Cross-device. Disabled WITH THE REASON SHOWN rather than hidden
          when the account-wide sync switch is off — a control that
          silently vanishes reads as a bug. */}
      <div className="border-t border-border mt-4 pt-4 flex items-start gap-3">
        <Switch
          checked={followMe && syncEnabled}
          disabled={!syncEnabled}
          onCheckedChange={(next) => {
            setFollowMe(next);
            if (next) queueMicrotask(publishAppearanceDefault);
          }}
          aria-label="Use these settings on my other devices"
        />
        <div className="min-w-0">
          <p className="text-sm text-foreground inline-flex items-center gap-1.5">
            Use these settings on my other devices
            <InfoTip
              size={14}
              label="Your colour, corners and size are saved to your account and applied the first time you sign in on a new browser. Browsers you already use keep their own."
            />
          </p>
          {!syncEnabled && (
            <p className="text-xs text-muted-foreground mt-0.5">
              Turn on &ldquo;Keep preferences on the account&rdquo; above to use this.
            </p>
          )}
        </div>
      </div>
    </Card>
  );
}
