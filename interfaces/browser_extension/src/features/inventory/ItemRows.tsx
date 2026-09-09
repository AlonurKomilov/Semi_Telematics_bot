/**
 * One grammar for an inventory item, wherever it is read.
 *
 * The Live Map's vehicle card shows a truck's items as a fold; the
 * Inventory feature shows the same items as its answer.  Two surfaces,
 * one row: a dot for how much it wants somebody, the item's own name,
 * the category it belongs to, and its status.
 *
 * It carries its own ceiling.  The panel's contract is that ONE region
 * absorbs growth, and inside the Live Map's card that region is the
 * map — a natural-height card holding twelve items pushes the map to
 * its floor and then overflows a column with no scroll of its own.
 */
import { humanize, sortForPanel, statusTone, type InventoryItem } from './data';

/** A status's colour, in the panel's own tokens. */
const TONE_VAR: Record<string, string> = {
  danger: 'var(--danger)', warn: 'var(--warn)', ok: 'var(--ok)', muted: 'var(--muted)',
};

/** Seven rows, and the rest scrolls in place. */
export const ROWS_CEILING_PX = 168;

export default function ItemRows({ items, maxHeight = ROWS_CEILING_PX, id }: {
  items: InventoryItem[];
  /** ``null`` lets the list run its full length — for a surface whose
   *  own region already scrolls, where a second scroller inside a
   *  scroller is a trap rather than a ceiling. */
  maxHeight?: number | null;
  id?: string;
}) {
  return (
    <div id={id} style={{
      display: 'grid', gap: 3,
      ...(maxHeight === null ? {} : { maxHeight, overflowY: 'auto' as const }),
    }}>
      {sortForPanel(items).map((it) => {
        const tone = statusTone(it.status);
        // A settled item states its status quietly; one that wants
        // attention says so in colour.
        const settled = tone === 'ok' || tone === 'muted';
        return (
          <div key={it.id} className="row" style={{ gap: 6, fontSize: 12, minWidth: 0 }}>
            <span aria-hidden style={{ width: 8, height: 8, borderRadius: '50%',
                                       background: TONE_VAR[tone], flexShrink: 0 }} />
            {/* minWidth 0 so a long label ellipsises instead of widening
                the whole panel. */}
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis',
                           whiteSpace: 'nowrap', minWidth: 0 }}>{it.label}</span>
            <span className="muted" style={{ flexShrink: 0 }}>{humanize(it.category)}</span>
            <span style={{ marginLeft: 'auto', flexShrink: 0,
                           color: settled ? 'var(--muted)' : TONE_VAR[tone],
                           fontWeight: settled ? 400 : 600 }}>
              {humanize(it.status)}
            </span>
          </div>
        );
      })}
    </div>
  );
}
