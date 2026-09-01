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
import { useRef, useState, type CSSProperties } from 'react';
import { RotateCcw, ChevronRight } from 'lucide-react';

import { Slider } from '../components/ui/slider';
import { Switch } from '../components/ui/switch';
import { InfoTip, Tip } from '../components/tooltip';
import { useMods, applySize } from './context';
import { usePreference } from '../preferences/usePreference';
import { publishAppearanceDefault, resetAppearanceDefault } from '../preferences/appearance';
import { undoableAction } from '../components/banners/stagedAction';
import { SIZE_MIN, SIZE_MAX, SIZE_DEFAULT } from '../preferences/registry';
import type { SizeRegion as SizeRegionKey } from '../preferences/registry';
import { Card } from '@/components/ui/card';
import { SectionHeader } from '@/components/shell';

const pct = (v: number) => `${Math.round(v * 100)}%`;

/** One labelled row. Live value paints straight to the DOM; only the
 *  committed value becomes a preference — a drag is ~60 frames and each
 *  one would otherwise be a synchronous localStorage write. */
function SizeRow({
  label, value, onPreview, onCommit, onReset, ariaLabel,
}: {
  label: string;
  value: number;
  /** Shown only when this row is off 100%. The global row has one too:
   *  without it there was no way back to a plain 100% without also
   *  discarding every per-area override. */
  onReset?: () => void;
  onPreview: (v: number) => void;
  onCommit: (v: number) => void;
  ariaLabel: string;
}) {
  const [drag, setDrag] = useState<number | null>(null);
  const shown = drag ?? value;
  const row = useRef<HTMLDivElement>(null);
  const anchor = useRef<number | null>(null);

  /**
   * Keep this row where the pointer left it.
   *
   * Pinning the panel's own scale stops the TRACK from changing length,
   * but not the row from moving: everything above it on the page is
   * previewing, so the panel slides down as the page grows. Measured in
   * a browser: an 80px drag put the thumb 74px BELOW the cursor, and a
   * long one 172px below — far enough that the pointer leaves the row
   * entirely and the drag reads as broken.
   *
   * So on each preview frame, measure how far the row moved and give
   * that back to the scroll container. The page still previews; the row
   * the finger is on does not move.
   */
  const holdStill = () => {
    const el = row.current;
    if (!el) return;
    const top = el.getBoundingClientRect().top;
    if (anchor.current === null) { anchor.current = top; return; }
    const delta = top - anchor.current;
    if (Math.abs(delta) < 0.5) return;
    let sc: HTMLElement | null = el.parentElement;
    while (sc) {
      const oy = getComputedStyle(sc).overflowY;
      if ((oy === 'auto' || oy === 'scroll') && sc.scrollHeight > sc.clientHeight) break;
      sc = sc.parentElement;
    }
    if (sc) sc.scrollTop += delta;
    else window.scrollBy(0, delta);
  };

  return (
    // Stacks below `sm`: the label rides --size-text, so at a large Size
    // on a phone a fixed-width label and value would leave the track no
    // travel at all.
    <div ref={row} className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
      <span className="text-sm text-foreground sm:w-40 sm:shrink-0">{label}</span>
      <Slider
        value={shown}
        min={SIZE_MIN}
        max={SIZE_MAX}
        step={0.05}
        aria-label={ariaLabel}
        formatValue={pct}
        onValueChange={(v) => {
          setDrag(v);
          onPreview(v);
          holdStill();
        }}
        onValueCommitted={(v) => {
          setDrag(null);
          anchor.current = null;
          onCommit(v);
        }}
        className="max-w-xs"
      />
      <span className="text-xs tabular-nums text-muted-foreground sm:w-12 sm:text-right sm:shrink-0">
        {pct(shown)}
      </span>
      {/* Only when this row is off 100%: otherwise every row carries a
          dead control. Without it, undoing ONE area meant dragging back
          to exactly 100 or throwing away all six. */}
      {/* The slot is always here, even when the button is not. It used
          to mount and unmount with `value !== 1`, so the row grew a few
          pixels taller the moment you moved a slider off 100% — every
          row below it stepped down while you were still dragging. */}
      <span className="inline-flex items-center justify-center min-h-tap min-w-tap shrink-0">
        {onReset && value !== 1 && (
          <Tip label={`Reset ${label.toLowerCase()} to 100%`}>
            <button
              type="button"
              onClick={onReset}
              aria-label={`Reset ${label.toLowerCase()} to 100%`}
              className="inline-flex items-center justify-center min-h-tap min-w-tap shrink-0 text-muted-foreground hover:text-foreground"
            >
              <RotateCcw className="size-3.5" />
            </button>
          </Tip>
        )}
      </span>
    </div>
  );
}

/** Region key -> what the user calls that part of the screen. Ordered
 *  the way the eye moves through the app, not alphabetically. The keys
 *  are frozen (registry.ts); only these labels are editable. */
const REGION_ROWS: { key: SizeRegionKey; label: string }[] = [
  // 'Page content', not 'Main content' and not 'Pages': every other
  // label points at something on screen, and the original named the
  // leftover — everything that is none of the others. A browser audit
  // then found 'Pages' promising more than it delivers, since a table
  // on a page takes the Tables setting, not this one. Keys are frozen
  // (registry.ts); labels are free.
  { key: 'text',       label: 'Page content' },
  { key: 'tables',     label: 'Tables' },
  { key: 'controls',   label: 'Top bar' },
  { key: 'navigation', label: 'Sidebar' },
  { key: 'overlays',   label: 'Dialogs & panels' },
  { key: 'assistant',  label: 'Assistant' },
];

export default function SizeCard() {
  const { size, setSize } = useMods();
  const [open, setOpen] = useState(false);
  const { value: followMe, setValue: setFollowMe } = usePreference('appearance.followMe');
  const { value: syncEnabled } = usePreference('prefs.syncEnabled');

  const resetAll = () => {
    // Snapshot BEFORE the write: this is the user's own configuration,
    // which is the whole point of the feature, and one click threw away
    // six tuned areas plus the account copy with nothing to catch it.
    const previous = size;
    setSize(SIZE_DEFAULT);
    // Reset has to reach the synced copy too, or the next browser the
    // user signs in on restores exactly what they just discarded.
    resetAppearanceDefault();
    undoableAction({
      label: 'Size reset to 100%',
      undo: async () => {
        setSize(previous);
        publishAppearanceDefault();
      },
    });
  };

  const tuned = REGION_ROWS.filter(
    (r) => (size.regions[r.key] ?? 1) !== 1,
  ).length;
  const atDefault = size.global === 1 && tuned === 0;

  // A control must not sit in the layout it is changing — and the first
  // attempt at this only pinned the panel WHILE a thumb was held, which a
  // browser audit found still running away: 80px of drag put the thumb
  // 25px right and 74px BELOW the pointer, a long drag 114/172px, and the
  // reverse direction overshot 89px left.
  //
  // So the instrument does not resize AT ALL. Not "not while dragging" —
  // never. A ruler you are adjusting has to hold still to be read, and
  // the preview belongs on the page around it, which is where the user
  // is looking to judge the setting anyway.
  //
  // `--size-region: 1` escapes the `text` region this panel renders
  // inside; the four axes hold at the COMMITTED size, which is what
  // `size` carries because it only updates on commit.
  const pinned = {
    '--size-region': 1,
    '--size-text': size.text * size.global,
    '--size-control': size.control * size.global,
    '--size-layout': size.layout * size.global,
    '--size-panel': size.panel * size.global,
  } as CSSProperties;

  return (
    <Card className="scroll-mt-20" render={<section />} id="appearance" style={pinned}>
      {/* Title, muted line and reset come from the ONE primitive rather
          than a hand-rolled flex row plus a loose <p>. The Appearance
          card beside this one wears exactly the same chrome, which is
          where the page says the two are siblings — their bodies differ
          because their content does, not because they were built at
          different times. */}
      <SectionHeader
        description="Resize the interface. This browser only, unless you turn on the switch below."
        action={(
          <button
            type="button"
            onClick={resetAll}
            disabled={atDefault}
            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:hover:text-muted-foreground shrink-0 py-1 -my-1 min-h-tap"
          >
            <RotateCcw className="size-3.5" />
            Reset sizes
          </button>
        )}
      >
        Interface size
      </SectionHeader>

      <div className="space-y-3 mt-4">
        <SizeRow
          label="Everything"
          ariaLabel="Interface size"
          value={size.global}
          onPreview={(v) => applySize({ ...size, global: v })}
          onCommit={(v) => setSize({ global: v })}
          onReset={() => { applySize({ ...size, global: 1 }); setSize({ global: 1 }); }}
        />

        {/* Per-area sliders are BEHIND a disclosure, not stacked under
            the first one. "Everything" is the control almost everyone
            wants; six more of the same shape beside it would make the
            page read as six equal decisions and bury the one that
            matters. Open, they show what they are worth — each one
            multiplies the global rather than replacing it. */}
        {/* `pt-4`, not `pt-1`: this button OWNS the six rows below it, and
            at pt-1 the gap above it measured 14px against a 12px rhythm
            between the rows — 1.17x, which says nothing. The indent rail
            was carrying the whole grouping alone. And the weight is
            `text-foreground font-medium`, not `text-xs
            text-muted-foreground`: a bar that governs a group should not
            be quieter than the group's members. */}
        <div className="pt-4">
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            className="inline-flex items-center gap-1.5 text-sm font-medium text-foreground hover:text-foreground/80 py-1 -my-1 min-h-tap"
          >
            <ChevronRight
              className={`size-3.5 transition-transform ${open ? 'rotate-90' : ''}`}
              aria-hidden
            />
            Fine-tune by area
            {/* A muted PHRASE, not a coloured numeric badge. This repo has
                already ruled on the shape once — datagrid/CLAUDE.md
                records that hidden columns were given neither a chip nor
                a count badge because either "reads as an unresolved
                notification to clear". A tuned area is a state to report,
                not a task to finish. */}
            {tuned > 0 && (
              <span className="text-2xs font-normal text-muted-foreground">
                · {tuned} {tuned === 1 ? 'area' : 'areas'} changed
              </span>
            )}
          </button>

          {open && (
            <div className="mt-3 space-y-3 border-l border-border pl-3">
              <p className="text-xs text-muted-foreground">
                These stack on top of the setting above, they don't replace
                it — so an area at 110% inside a 120% interface ends up at
                132%.
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
                  onReset={() => {
                    const next = { ...size.regions };
                    delete next[key];
                    applySize({ ...size, regions: next });
                    setSize({ regions: next });
                  }}
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
