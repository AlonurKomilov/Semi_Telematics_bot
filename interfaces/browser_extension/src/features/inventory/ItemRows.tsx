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
import { useEffect, useRef, useState } from 'react';
import { FIELD_LABEL, PANEL_STATUSES, humanize, sortForPanel, statusTone,
         type InventoryItem, type ItemPatch } from './data';
import Field from './Field';
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
  /** Correcting what an item SAYS.  Retire and transfer are not here
   *  and are not reachable from this key — they end an item's story
   *  rather than correcting it, and that stays at a desk. */
  onEdit?: (itemId: number, patch: ItemPatch) => Promise<void>;
  /** The categories this account already uses, for the edit form's
   *  datalist.  The vocabulary is OPEN, so it is a suggestion list and
   *  never a closed set. */
  categories?: string[];
  /** A row to bring into view once — a just-added item, which sorts to
   *  the bottom because nothing is wrong with it. */
  focusId?: number | null;
  /** Raised while a row is being corrected.  The edit form is ~200px and
   *  the list's ceiling is sized for ROWS — inside it the form became a
   *  90px scroller showing one field at a time, with its own scrollbar,
   *  under whatever else the panel had open.  The parent owns the
   *  region, so the parent is told and lifts the ceiling. */
  onEditingChange?: (editing: boolean) => void;
}

export default function ItemRows({ items, maxHeight = ROWS_CEILING_PX, id, onVerify, onStatus, onEdit, categories, focusId, onEditingChange }: ItemRowsProps) {
  const canWrite = Boolean(onVerify || onStatus || onEdit);
  /** Which row has its actions showing.  One at a time: four controls
   *  under every row would bury the list they belong to. */
  const [openId, setOpenId] = useState<number | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  /** Which row is being corrected.  Editing REPLACES the strip rather
   *  than adding a third line: at the panel's 320px floor, a form and
   *  five controls at once is a wall, and the two are different jobs —
   *  reporting what you found, and fixing what the record says. */
  const [editId, setEditId] = useState<number | null>(null);
  const startEdit = (id: number | null) => {
    setEditId(id);
    onEditingChange?.(id !== null);
  };
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

  useEffect(() => {
    if (focusId == null) return;
    rowEls.current.get(focusId)?.scrollIntoView({ block: 'nearest' });
  }, [focusId, items]);

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
    // The 4px BLEED lives here and nowhere else.  It used to sit on each
    // open row's wrapper, where a negative margin on a stretched grid
    // item makes the item WIDER than its track (used width = track + 8)
    // — 4px of real horizontal overflow, and the scrollbar the owner
    // photographed.  On the root the same trick costs nothing: the root
    // is itself the scroll container, so its own margin box is not its
    // content, and the bleed lands inside the .sheet's 10px padding.
    //
    // overflowX is declared INSIDE the ceiling branch on purpose.  Given
    // alone beside an implied `overflow-y: visible`, `overflow-x: hidden`
    // promotes the other axis to `auto` — which would turn the
    // maxHeight === null mode, documented above as the one that must NOT
    // scroll, into a scroller.
    <div id={id} style={{
      display: 'grid', gap: 3, margin: '0 -4px', padding: '0 4px',
      ...(maxHeight === null ? {} : {
        maxHeight, overflowY: 'auto' as const, overflowX: 'hidden' as const,
      }),
    }}>
      {sortForPanel(items).map((it) => {
        const tone = statusTone(it.status);
        // A settled item states its status quietly; one that wants
        // attention says so in colour.
        const settled = tone === 'ok' || tone === 'muted';
        const open = openId === it.id;
        const line = (
          <>
            {/* The caret LEADS what it opens — the panel's own rule, kept
                by the Live Map's card and list headers.  The row carried
                `aria-expanded` and nothing a person could see, which was
                survivable while the trailing slot held a status word and
                the row read as "information you can press".  It holds a
                BUTTON now, and a row whose only visible affordance is
                Edit reads as a row where Edit is all there is — while
                Verify, and every way to flag a problem, are behind the
                press.  Read-only rows get no caret: nothing opens. */}
            {canWrite && (
              <span aria-hidden className="muted" style={{ flexShrink: 0, fontSize: 10, width: 8 }}>
                {open ? '▾' : '▴'}
              </span>
            )}
            <span aria-hidden style={{ width: 8, height: 8, borderRadius: '50%',
                                       background: TONE_VAR[tone], flexShrink: 0 }} />
            {/* minWidth 0 so a long label ellipsises instead of widening the
                whole panel — and `title`, so an ellipsised name is recoverable
                at all.  Opening a row costs this line ~60px (Verify joins Edit)
                and every other element on it refuses to shrink, so the NAME was
                the only thing that gave: the flagged item somebody had just
                pressed could ellipsise to nothing, unreadable. */}
            <span title={it.label}
                  style={{ overflow: 'hidden', textOverflow: 'ellipsis',
                           whiteSpace: 'nowrap', minWidth: 0 }}>{it.label}</span>
            {/* The CATEGORY stands down while the row is open, and gives before
                the name does when it is not.  It is a repeated, low-information
                token — "Tablet" on every tablet — and the name is the identity.
                It carries `title` for the same reason the name does: the first
                draft made this shrinkable and did NOT, which moved the exact
                defect it was written to end one element to the right. */}
            {!open && (
              <span className="muted" title={humanize(it.category)}
                    style={{ minWidth: 0, overflow: 'hidden',
                             textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {humanize(it.category)}
              </span>
            )}
            {/* The word appears only when the DOT cannot say it.
                `installed` is what the green dot already means, so
                "Installed" on every settled row was the same fact
                twice and the widest thing competing with the item's
                own name.  `missing` and `damaged` both draw a RED dot
                — there the word is the only thing separating "it is
                broken" from "it is gone", so it stays. */}
            <span style={{ marginLeft: 'auto', flexShrink: 0,
                           color: settled ? 'var(--muted)' : TONE_VAR[tone],
                           fontWeight: settled ? 400 : 600 }}>
              {it.status === 'installed' ? '' : humanize(it.status)}
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
                          // Reaches the root's padding edge exactly — its
                          // margin box is 288 in a 288 padding box, so it
                          // fills without overflowing, and the content
                          // inside it does not shift on open.
                          margin: '0 -4px 6px', padding: '0 4px',
                        } : {}) }}>
            {canWrite ? (
              // A ROW, not a button: Edit sits on it, and a button
              // inside a button is invalid HTML that keyboards and
              // screen readers cannot untangle.  The identity half is
              // the press target; Edit is its sibling.
              <div className="row" style={{ gap: 6 }}>
              <button type="button" className="row rowbtn"
                      aria-expanded={open}
                      title={open ? 'Hide the actions' : 'Verify or flag this item'}
                      onClick={() => {
                        const next = open ? null : it.id;
                        setOpenId(next);
                        // A row closed mid-edit must not reopen still in
                        // the form: the person left it, and coming back
                        // to an abandoned draft reads as unsaved work.
                        startEdit(null);
                        // A row grows from 24px to ~88 inside a 168px
                        // scroller: opened near the fold, everything it
                        // just revealed is below it.  Next frame, once
                        // the strip has rendered.
                        if (next !== null) {
                          requestAnimationFrame(() => {
                            rowEls.current.get(it.id)?.scrollIntoView({ block: 'nearest' });
                          });
                        }
                      }}
                      // No width and no margin: as a stretched grid item
                      // it fills its track exactly, in both states, so
                      // the dot does not jump 4px when the row opens.
                      // The one row a step SMALLER than the rest: it sits
                      // beside a read-only branch at 12px, and inheriting
                      // the panel's 13px left the two a step apart.
                      style={{ flex: '1 1 0', gap: 6, padding: '2px 4px',
                               borderRadius: 4, fontSize: 12 }}>
                {line}
              </button>
              {/* Correcting a record is one press from the list, not two.
                  It used to live inside the strip the row opens, which
                  meant opening a row to reach it — and it took the slot
                  where "Installed" was saying what the dot already said.
                  `compact` so a 32px button does not sit on a 24px row.
                  It ends at a FIXED x on every row: the status word to
                  its left comes and goes, the button never moves. */}
              {/* Verify joins Edit on the ROW, and only while the row is
                  OPEN.  Closed, the list stays one control per row —
                  190 rows each offering two actions is a wall, and a
                  check is not something you press without first seeing
                  how long it has been.  Open, both sit together at the
                  same x rather than one on the row and one on a line
                  below it, which read as two different kinds of thing
                  when they are the same kind. */}
              {open && onVerify && (
                <button className="btn compact" disabled={busy === it.id}
                        style={{ flexShrink: 0 }}
                        title="Record that you checked it and it is aboard"
                        onClick={() => void act(it.id, () => onVerify(it.id), false)}>
                  {busy === it.id ? 'Saving…' : 'Verify'}
                </button>
              )}
              {onEdit && (
                <button className="btn compact" disabled={busy === it.id}
                        style={{ flexShrink: 0 }}
                        title="Correct this item's name, serial or category"
                        onClick={() => {
                          setOpenId(it.id);
                          startEdit(it.id);
                          requestAnimationFrame(() => {
                            rowEls.current.get(it.id)?.scrollIntoView({ block: 'nearest' });
                          });
                        }}>
                  Edit
                </button>
              )}
              </div>
            ) : (
              <div className="row" style={{ gap: 6, fontSize: 12, minWidth: 0 }}>{line}</div>
            )}
            {failed?.id === it.id && (
              <p style={{ color: 'var(--danger)', margin: 0, fontSize: 12, paddingLeft: 14 }}>
                {failed.why}
              </p>
            )}
            {open && editId === it.id && onEdit && (
              <EditForm item={it} categories={categories ?? []} busy={busy === it.id}
                        onCancel={() => startEdit(null)}
                        onSave={(patch) => void act(it.id, async () => {
                          await onEdit(it.id, patch);
                          startEdit(null);
                        }, false)} />
            )}
            {open && editId !== it.id && (
              // TWO explicit lines, not one wrapping row.  Five controls
              // need ~500px at the panel's 320px floor, so a single row
              // survived only by accidental wrap — and a 32px .btn sat
              // on the same line as 24px chips, aligning nothing.  Line
              // A says what is known and offers the check; line B is the
              // ladder of what it could be instead.
              //
              // Each line is guarded by the prop that fills it: the two
              // are independent, and a verify-only caller would
              // otherwise render an empty flex row.
              <>
              {onVerify && (
              // 28px, not 14: this indent is measured to land under the
              // item's NAME, not under its dot.  The name starts at
              // 4 (button padding) + 8 (caret) + 6 + 8 (dot) + 6 = 32,
              // and the block's own 4px padding makes 28 the number
              // that reaches it.  It was 14 when there was no caret.
              <div className="row" style={{ gap: 6, padding: '0 0 0 28px' }}>
                {/* WHAT VERIFY CHANGES, said where Verify is pressed.
                    ``verify_inventory_item`` stamps the check and leaves
                    the status alone, so without this the button closed a
                    strip and altered nothing on screen — and on an item
                    already flagged missing it recorded a check the row
                    went on contradicting.  It is here rather than on the
                    row because a fifth piece ellipsised the item's own
                    name away at the panel's 320px floor. */}
                {/* The serial the record was made against.  It lives
                    here rather than on the row for the same reason the
                    check age does — a fifth piece ellipsised the item's
                    own name away at 320px — and it is the thing you
                    hold the device up against when you press Verify. */}
                {/* The line's TEXT shrinks; its BUTTONS never do.  At the
                    panel's 320px floor this line has ~278px, and serial
                    + age + Verify + Edit measures ~275 with a short
                    serial — a 17-character one would have pushed Edit
                    past an `overflow-x: hidden` edge and out of reach.
                    `.row` is a flex with no wrap, so nothing would have
                    given: the button would simply have been gone. */}
                {/* The category, back on the line the open row does have
                    room for.  Hiding it above buys the NAME its width; not
                    showing it anywhere would mean collapsing a row to learn
                    what an item is filed under. */}
                <span className="muted" style={{ fontSize: 11, flexShrink: 0 }}>
                  {humanize(it.category)}
                </span>
                {it.identifier && (
                  <span className="muted" style={{ fontSize: 11, fontFamily: 'ui-monospace, monospace',
                                                   minWidth: 0, overflow: 'hidden',
                                                   textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                        title={`The serial this record was made against: ${it.identifier}`}>
                    {it.identifier}
                  </span>
                )}
                <span className="muted" style={{ fontSize: 11, minWidth: 0, overflow: 'hidden',
                                                 textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                      title={it.last_verified_at ? `Last checked ${it.last_verified_at}` : 'Nobody has checked this yet'}>
                  {(() => {
                    const age = ageMs(it.last_verified_at, now);
                    return age === null ? 'never checked' : `checked ${formatAge(age)} ago`;
                  })()}
                </span>
                {/* This line is FACTS now — the serial the record was
                    made against and how long since anybody looked.  Its
                    button moved up beside Edit, where the two actions
                    on an item belong together. */}
                {/* Edit is NOT here.  It sits on the ROW itself, one
                    press from the list.  A second copy in the strip
                    would be the same control twice — the rule this
                    panel just finished applying to Follow. */}
              </div>
              )}
              {onStatus && (
              // A grid, not a wrapping flex: with `flex:1 1 auto` a
              // wrapped last chip stretches into a full-width danger
              // bar, which is the loudest thing on the card for the
              // quietest reason.  Equal columns, and they reflow.
              <div style={{ display: 'grid', gap: 6, padding: '0 0 2px 28px',
                            gridTemplateColumns: 'repeat(auto-fit, minmax(72px, 1fr))' }}>
                {/* Only what it is NOT.  A permanently disabled chip
                    restating the status written two lines up is a
                    control that can never be pressed, taking a column
                    from three that can. */}
                {PANEL_STATUSES.filter((st) => st !== it.status).map((st) => (
                  <button key={st} className="chip" disabled={busy === it.id}
                          onClick={() => void act(it.id, () => onStatus(it.id, st))}>
                    {humanize(st)}
                  </button>
                ))}
              </div>
              )}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

/**
 * Correcting what an item says, in place.
 *
 * Three fields and no more: the name, the serial, and the category.
 * Notes are a desk field — long, and nobody types a paragraph standing
 * at a truck — and retiring or transferring are not corrections at all.
 *
 * Only CHANGED fields are sent.  An edit that stamped all three would
 * fill the accountability trail with rows recording that nothing
 * happened, which is how a reader learns to skim past the rows where
 * something did.
 */
function EditForm({ item, categories, busy, onSave, onCancel }: {
  item: InventoryItem;
  categories: string[];
  busy: boolean;
  onSave: (patch: ItemPatch) => void;
  onCancel: () => void;
}) {
  const [label, setLabel] = useState(item.label ?? '');
  const [identifier, setIdentifier] = useState(item.identifier ?? '');
  const [category, setCategory] = useState(humanize(item.category ?? ''));

  const patch: ItemPatch = {};
  if (label.trim() && label.trim() !== (item.label ?? '')) patch.label = label.trim();
  if (identifier.trim() !== (item.identifier ?? '')) patch.identifier = identifier.trim();
  if (category.trim() && category.trim() !== humanize(item.category ?? '')) {
    patch.category = category.trim();
  }
  const changed = Object.keys(patch).length > 0;
  // A name is what the row IS; emptying it would leave a row nobody can
  // identify, which is the opposite of what this feature is for.
  const nameGone = !label.trim();
  const listId = `cats-${item.id}`;

  return (
    <div style={{ display: 'grid', gap: 6, padding: '2px 0 4px 14px' }}>
      <Field label={FIELD_LABEL.label} required>
        <input className="input" value={label} disabled={busy} autoFocus
               onChange={(e) => setLabel(e.target.value)} />
      </Field>
      <Field label={FIELD_LABEL.identifier}>
        {/* Monospace, like the row above it: a serial is read a
            character at a time, and it is the field a correction is
            usually here for. */}
        <input className="input" value={identifier} disabled={busy}
               style={{ fontFamily: 'ui-monospace, monospace' }}
               onChange={(e) => setIdentifier(e.target.value)} />
      </Field>
      <Field label={FIELD_LABEL.category} required>
        <input className="input" value={category} disabled={busy} list={listId}
               onChange={(e) => setCategory(e.target.value)} />
        <datalist id={listId}>
          {categories.map((c) => <option key={c} value={humanize(c)} />)}
        </datalist>
      </Field>
      {/* The trail is the whole point of this feature, so the form says
          so rather than hiding it in a tooltip.  Neutral fact, not a
          warning: somebody fixing a typo is not doing anything wrong,
          and somebody rewriting a serial should know it is recorded. */}
      {/* Grouped WITH the buttons, not floating between them and the
          last field: it describes what Save does, and at an equal gap
          on both sides it belonged to neither. */}
      <div style={{ display: 'grid', gap: 4 }}>
      <p className="muted" style={{ margin: 0, fontSize: 11 }}>
        Saved with your name, next to what it replaced.
      </p>
      <div className="row" style={{ gap: 6 }}>
        {/* Disabled WITH A REASON.  A dead button that says nothing
            sends a person hunting for the field they missed. */}
        <button className="btn primary" disabled={busy || !changed || nameGone}
                title={nameGone ? 'An item needs a name'
                     : !changed ? 'Nothing has changed yet'
                     : 'Save the correction'}
                onClick={() => onSave(patch)}>
          {busy ? 'Saving…' : 'Save'}
        </button>
        <button className="btn" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
      </div>
    </div>
  );
}
