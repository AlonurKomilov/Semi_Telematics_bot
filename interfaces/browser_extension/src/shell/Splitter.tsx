/**
 * A draggable line between two stacked regions.
 *
 * Both of this panel's features hold the same tension, and it changes
 * by TASK rather than by person: somebody auditing one truck wants the
 * card big, somebody hunting across 190 of them wants the list big, and
 * it is the same person an hour apart.  A setting cannot answer that —
 * it lives two screens away — so this is direct manipulation instead,
 * the way a window is resized.
 *
 * It REPLACES a magic constant rather than adding a control: the card
 * used to be a hardcoded `maxHeight: '70%'`, a number chosen by whoever
 * wrote it.  Seventy is now just where the drag starts.
 *
 * The shape is the dashboard's own separator (features/ai/AssistantPanel),
 * because that one is already correct where these are easy to get wrong:
 * a real ARIA separator with its value, arrow keys for anyone who cannot
 * drag, and double-click to undo a bad drag without hunting for the
 * exact pixel.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { getNumber, setNumber } from '../prefs';

/** Neither region may be dragged away entirely.  A card at 0 hides the
 *  truck somebody just selected; a list at 0 hides the way to select
 *  another, and both leave the panel looking broken rather than
 *  configured. */
export const MIN_PCT = 20;
export const MAX_PCT = 80;
/** One arrow press.  Big enough to be worth pressing, small enough to
 *  land where you meant on a 600px column. */
const STEP_PCT = 4;

function clamp(n: number): number {
  return Math.min(MAX_PCT, Math.max(MIN_PCT, Math.round(n)));
}

export interface SplitterProps {
  /** Storage key — one per SURFACE.  The map/list ratio and the
   *  card/list ratio are different judgements about different regions;
   *  one shared number would make each surface fight the other. */
  storageKey: string;
  /** Where the drag starts on a panel that has never been dragged. */
  fallback: number;
  /** The column the percentage is measured against. */
  columnRef: React.RefObject<HTMLElement | null>;
  /** Raised with the current percentage: on mount, on drag, on reset. */
  onChange: (pct: number) => void;
  label: string;
}

export default function Splitter({ storageKey, fallback, columnRef, onChange, label }: SplitterProps) {
  const [pct, setPct] = useState<number | null>(null);
  const dragging = useRef(false);

  // Read once, then the parent is told.  `null` until it arrives so the
  // parent can hold its own default rather than flashing 50% first.
  useEffect(() => {
    let cancelled = false;
    void getNumber(storageKey, fallback, MIN_PCT, MAX_PCT).then((n) => {
      if (cancelled) return;
      setPct(n);
      onChange(n);
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey]);

  const apply = useCallback((next: number, persist: boolean) => {
    const v = clamp(next);
    setPct(v);
    onChange(v);
    if (persist) void setNumber(storageKey, v);
  }, [onChange, storageKey]);

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const col = columnRef.current;
    if (!col || e.button !== 0) return;
    // Pointer CAPTURE, so a fast drag that leaves the 320px column does
    // not silently stop resizing halfway.
    e.currentTarget.setPointerCapture(e.pointerId);
    dragging.current = true;
    e.preventDefault();
  };

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    const col = columnRef.current;
    if (!col) return;
    const box = col.getBoundingClientRect();
    if (box.height <= 0) return;
    apply(((e.clientY - box.top) / box.height) * 100, false);
  };

  const stop = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    dragging.current = false;
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch {
      /* the pointer was already gone; the drag is over either way */
    }
    // Written on RELEASE, not per move: a drag is one decision, and
    // storage does not need sixty writes a second to hear it.
    if (pct != null) void setNumber(storageKey, pct);
  };

  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      aria-label={label}
      aria-valuenow={pct ?? fallback}
      aria-valuemin={MIN_PCT}
      aria-valuemax={MAX_PCT}
      tabIndex={0}
      title={`${label} — drag, or use the arrow keys; double-click to reset`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={stop}
      onPointerCancel={stop}
      onDoubleClick={() => apply(fallback, true)}
      onKeyDown={(e) => {
        // The whole point of the ARIA role: somebody who cannot drag can
        // still move it.
        if (e.key === 'ArrowUp') { apply((pct ?? fallback) - STEP_PCT, true); e.preventDefault(); }
        else if (e.key === 'ArrowDown') { apply((pct ?? fallback) + STEP_PCT, true); e.preventDefault(); }
        else if (e.key === 'Home') { apply(MIN_PCT, true); e.preventDefault(); }
        else if (e.key === 'End') { apply(MAX_PCT, true); e.preventDefault(); }
      }}
      // A 24px HIT BOX for a 2px line, bought with padding the layout
      // never pays for.  An edge handle is the case WCAG 2.5.8's spacing
      // exception rarely saves, and a hairline is not something anybody
      // can aim at.  The box is 10 + 7 + 7 = 24 tall; the -7 margins
      // give those 14px back, so the column still allocates 10 and no
      // region moves.  Both panel roots use `gap: 8`, so the grown box
      // reaches 7px into an 8px gap — it never overlaps a neighbour's
      // own target.  `touch-action: none` or the drag becomes a scroll.
      style={{
        flexShrink: 0, height: 10, boxSizing: 'content-box',
        padding: '7px 0', margin: '-7px 10px', cursor: 'row-resize',
        touchAction: 'none', display: 'flex', alignItems: 'center',
        borderRadius: 4,
      }}
    >
      <div aria-hidden style={{ height: 2, width: '100%', borderRadius: 2,
                                background: 'var(--border)' }} />
    </div>
  );
}
