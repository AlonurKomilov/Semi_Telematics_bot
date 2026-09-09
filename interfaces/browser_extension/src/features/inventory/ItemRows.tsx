/**
 * One grammar for an inventory item, wherever it is read — and, for
 * whoever may write, the two things a person does standing at a truck.
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
import { useRef, useState } from 'react';
import { PANEL_STATUSES, humanize, sortForPanel, statusTone, type InventoryItem } from './data';
import { ageMs, formatAge } from '../live-map/freshness';

/** A status's colour, in the panel's own tokens. */
const TONE_VAR: Record<string, string> = {
  danger: 'var(--danger)', warn: 'var(--warn)', ok: 'var(--ok)', muted: 'var(--muted)',
};

/** Seven rows, and the rest scrolls in place. */
export const ROWS_CEILING_PX = 168;

export interface ItemRowsProps {
  items: InventoryItem[];
  /** ``null`` lets the list run its full length — for a surface whose
   *  own region already scrolls, where a second scroller inside a
   *  scroller is a trap rather than a ceiling. */
  maxHeight?: number | null;
  id?: string;
  /** Given only when the person may write AND the server said so.  A
   *  control the server would refuse is not shown: offering it and
   *  answering 403 on the press is the worse of the two. */
  onVerify?: (itemId: number) => Promise<void>;
  onStatus?: (itemId: number, status: string) => Promise<void>;
}

export default function ItemRows({ items, maxHeight = ROWS_CEILING_PX, id, onVerify, onStatus }: ItemRowsProps) {
  const canWrite = Boolean(onVerify || onStatus);
  /** Which row has its actions showing.  One at a time: four controls
   *  under every row would bury the list they belong to. */
  const [openId, setOpenId] = useState<number | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  /** Which row's action failed, and why.  Held per ROW rather than per
   *  list: the map card scrolls this list inside 168px, so a message
   *  parked at the bottom is a message somebody never sees — and they
   *  drive away believing they logged a missing dashcam. */
  const [failed, setFailed] = useState<{ id: number; why: string } | null>(null);
  const now = Date.now();
  /** Each row's element, so one that MOVES can be brought back.  A
   *  status change re-ranks the list (attention first) inside a 168px
   *  scroller: press Missing on the twelfth item and it jumps to the
   *  top, out of sight, and the press looks like it did nothing. */
  const rowEls = useRef<Map<number, HTMLDivElement | null>>(new Map());

  const act = async (itemId: number, run: () => Promise<void>, close = true) => {
    setBusy(itemId);
    setFailed(null);
    try {
      await run();
      // A status change is a decision, and the strip closing says it was
      // taken.  A check is repeatable — closing on it would hide the very
      // thing it just changed, which is the age standing beside it.
      if (close) {
        setOpenId(null);
        // …and the row it belongs to may have just been re-ranked past
        // the fold.  Next frame, once the new order has rendered.
        requestAnimationFrame(() => {
          rowEls.current.get(itemId)?.scrollIntoView({ block: 'nearest' });
        });
      }
    } catch (e) {
      setFailed({ id: itemId, why: e instanceof Error ? e.message : 'That did not save' });
    } finally {
      setBusy(null);
    }
  };

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
        const open = openId === it.id;
        const line = (
          <>
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
          </>
        );
        return (
          // The FILL lives here, not on the row button: an open row lit
          // while the strip it opened stayed dark was two regions, with
          // nothing saying the second belonged to the first.  One block
          // now, and `marginBottom` puts more air below the group than
          // inside it — 3 to its own strip, 9 to the next item.
          <div key={it.id}
               ref={(el) => { rowEls.current.set(it.id, el); }}
               style={{ display: 'grid', gap: 3, borderRadius: 4,
                        ...(open ? {
                          background: 'rgba(255,255,255,.06)',
                          margin: '0 -4px 6px', padding: '0 4px',
                        } : {}) }}>
            {canWrite ? (
              // Only a row that DOES something becomes a button.  A
              // read-only list of buttons would promise an action that
              // is not there.
              <button type="button" className="row rowbtn"
                      aria-expanded={open}
                      title={open ? 'Hide the actions' : 'Verify or flag this item'}
                      onClick={() => setOpenId(open ? null : it.id)}
                      style={{ gap: 6, fontSize: 12, minWidth: 0, width: '100%', minHeight: 24,
                               background: 'transparent',
                               border: 0, padding: '2px 4px', margin: open ? 0 : '0 -4px', borderRadius: 4,
                               color: 'var(--fg)', cursor: 'pointer', font: 'inherit', textAlign: 'left' }}>
                {line}
              </button>
            ) : (
              <div className="row" style={{ gap: 6, fontSize: 12, minWidth: 0 }}>{line}</div>
            )}
            {failed?.id === it.id && (
              <p style={{ color: 'var(--danger)', margin: 0, fontSize: 12, paddingLeft: 14 }}>
                {failed.why}
              </p>
            )}
            {open && (
              // Two meaning classes, two shapes — the panel's own rule.
              // Verify is an ACTION (a bordered button, like Close);
              // the statuses are a SELECTION, so they are chips and the
              // current one is pressed.
              <div className="row" style={{ flexWrap: 'wrap', gap: 6, padding: '0 0 2px 14px' }}>
                {/* WHAT VERIFY CHANGES, said where Verify is pressed.
                    ``verify_inventory_item`` stamps the check and leaves
                    the status alone, so without this the button closed a
                    strip and altered nothing on screen — and on an item
                    already flagged missing it recorded a check the row
                    went on contradicting.  It is here rather than on the
                    row because a fifth piece ellipsised the item's own
                    name away at the panel's 320px floor. */}
                <span className="muted" style={{ fontSize: 11 }}
                      title={it.last_verified_at ? `Last checked ${it.last_verified_at}` : 'Nobody has checked this yet'}>
                  {(() => {
                    const age = ageMs(it.last_verified_at, now);
                    return age === null ? 'never checked' : `checked ${formatAge(age)} ago`;
                  })()}
                </span>
                {onVerify && (
                  <button className="btn" disabled={busy === it.id}
                          title="Record that you checked it and it is aboard"
                          onClick={() => void act(it.id, () => onVerify(it.id), false)}>
                    {busy === it.id ? 'Saving…' : 'Verify'}
                  </button>
                )}
                {onStatus && PANEL_STATUSES.map((st) => (
                  <button key={st} className={`chip ${st === it.status ? 'on' : ''}`}
                          disabled={busy === it.id || st === it.status}
                          aria-pressed={st === it.status}
                          onClick={() => void act(it.id, () => onStatus(it.id, st))}>
                    {humanize(st)}
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
