/**
 * "Appearance size" — the full Size surface.
 *
 * The topbar picker carries the same global slider, because that is the
 * case almost everyone wants ("a bit bigger"). What lives HERE and not
 * there is the cross-device switch — a settings decision rather than a
 * quick toggle, and one a w-56 popover cannot hold without inventing a
 * width design.md §7 forbids.
 *
 * Per-region sliders are designed and the engine already publishes
 * `--size-region-*`, but they are not on this page yet: nothing reads
 * those variables until each region has an owning wrapper. See the note
 * where they will go.
 */
import { useState } from 'react';
import { RotateCcw } from 'lucide-react';

import { Slider } from '../components/ui/slider';
import { Switch } from '../components/ui/switch';
import { InfoTip } from '../components/tooltip';
import { useTheme, applySize } from '../context/ThemeContext';
import { usePreference } from './usePreference';
import { publishAppearanceDefault, resetAppearanceDefault } from './appearance';
import { SIZE_MIN, SIZE_MAX, SIZE_DEFAULT } from './registry';

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

export default function SizeCard() {
  const { size, setSize } = useTheme();
  const { value: followMe, setValue: setFollowMe } = usePreference('appearance.followMe');
  const { value: syncEnabled } = usePreference('prefs.syncEnabled');

  const resetAll = () => {
    setSize(SIZE_DEFAULT);
    // Reset has to reach the synced copy too, or the next browser the
    // user signs in on restores exactly what they just discarded.
    resetAppearanceDefault();
  };

  const atDefault = size.global === 1;

  return (
    <section id="appearance" className="bg-card border border-border rounded-xl p-5 scroll-mt-20">
      <div className="flex items-start justify-between gap-4 mb-1">
        <h2 className="text-lg font-semibold">Appearance size</h2>
        <button
          type="button"
          onClick={resetAll}
          disabled={atDefault}
          className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:hover:text-muted-foreground shrink-0 py-1 -my-1 min-h-tap"
        >
          <RotateCcw size={14} />
          Reset all
        </button>
      </div>
      <p className="text-xs text-muted-foreground mb-4">
        Make the interface bigger. Applies to this browser; the switch below shares it with your other ones.
      </p>

      <div className="space-y-3">
        <SizeRow
          label="Everything"
          ariaLabel="Interface size"
          value={size.global}
          onPreview={(v) => applySize({ ...size, global: v })}
          onCommit={(v) => setSize({ global: v })}
        />

        {/* The six per-region sliders are NOT here yet, deliberately.
            The engine publishes `--size-region-*` and the cascade is
            ready, but no component reads those variables until the
            regions get an owner — a single wrapper whose dimensions they
            can scope. Cards, chips, list rows and toolbars have no such
            wrapper at all today (239 / 144 / 54 / 53 hand-rolled sites,
            no primitive), and the shell page frame is six byte-identical
            copies. Shipping the sliders before their consumers would put
            six controls on this page that move nothing — which is
            exactly the fault this whole feature exists to fix. */}
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
    </section>
  );
}
