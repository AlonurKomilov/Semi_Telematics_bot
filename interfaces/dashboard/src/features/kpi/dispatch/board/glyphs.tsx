/**
 * The board's repeated glyph, defined ONCE — the perf audit measured
 * the "no loads" calendar icon at ~296 copies × 7 nodes (svg + six
 * paths) = ~2,000 nodes, a third of the page, all drawing the same
 * picture.  A single <symbol> now holds the drawing and every cell
 * renders a 2-node <use> reference to it.
 *
 * The drawing IS lucide's CalendarOff (v1.8.0 paths, verbatim) — same
 * icon set, same look, same currentColor behaviour; only the delivery
 * mechanism changes, because the lucide component is priced for
 * variety, not for 300 copies of one icon in a grid.
 */

/** Mount once per board, before any <CalendarOffGlyph/>. */
export function BoardGlyphDefs() {
  return (
    <svg width={0} height={0} className="absolute" aria-hidden focusable="false">
      <symbol id="kpi-calendar-off" viewBox="0 0 24 24" fill="none"
        stroke="currentColor" strokeWidth={2}
        strokeLinecap="round" strokeLinejoin="round">
        <path d="M4.2 4.2A2 2 0 0 0 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 1.82-1.18" />
        <path d="M21 15.5V6a2 2 0 0 0-2-2H9.5" />
        <path d="M16 2v4" />
        <path d="M3 10h7" />
        <path d="M21 10h-5.5" />
        <path d="m2 2 20 20" />
      </symbol>
    </svg>
  );
}

/** A 2-node reference to the shared drawing — colour rides
 *  currentColor exactly like the lucide component it replaces. */
export function CalendarOffGlyph({ className }: { className?: string }) {
  return (
    <svg width={12} height={12} viewBox="0 0 24 24"
      className={className} aria-hidden focusable="false">
      <use href="#kpi-calendar-off" />
    </svg>
  );
}
