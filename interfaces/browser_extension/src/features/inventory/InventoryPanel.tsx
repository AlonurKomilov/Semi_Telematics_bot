/**
 * Inventory, in the side panel.
 *
 * The Live Map's card answers "what is on THIS truck" while you are
 * looking at the map.  This is the feature's own home: which trucks
 * want somebody, and what is aboard the one you pick.
 *
 * It draws no map.  The map is already on the screen — it is Google's,
 * and this panel sits beside it.  A truck clicked over there arrives
 * here the same way it arrives at Live Map, and this surface answers
 * with contents instead of a position.
 *
 * It is read-only, and deliberately: ``can_manage_inventory`` is outside
 * the extension token's scope, so a key that lives in a browser cannot
 * mark a dashcam missing.  The way out is a link, not a form.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { apiJSON } from '../../api/client';
import Field from './Field';
import Splitter from '../../shell/Splitter';
import { MIN_PCT } from '../../shell/splitterRange';
import { DASHBOARD_BASE } from '../../connect';
import { PENDING_SELECT_KEY, readPendingSelect } from '../maps-overlay/bridge';
import { FIELD_LABEL, addItem, editItem, forgetVehicle, humanize, inventoryFor, retryInventory,
         setItemStatus, verifyItem,
         type Inventory } from './data';
import ItemRows from './ItemRows';
import type { PanelFeatureProps } from '../../shell/registry';
import { positionOf } from '../live-map/locate';
import { followInGoogleMaps, searchUrl } from '../live-map/googleMaps';
// READ, never written here: "Follow in Google Maps" is a preference of
// the PANEL, and Settings is the only place it is changed.
import { getFollowPref } from '../../prefs';

/** One truck's line in the fleet answer — counts and a name, never
 *  contents.  An item's own label arrives when a truck is chosen. */
interface FleetRow {
  vehicle_id: number;
  name: string;
  company: string;
  total: number;
  attention: number;
}

/** One labelled field.  The label stays when the placeholder goes, and
 *  a required one says so where the eye already is rather than in a
 *  message that appears after the press. */
/** Inventory is not live data: somebody adds a dashcam, not thirty
 *  trucks move.  Slow enough to be free, often enough that a panel left
 *  open all morning is not lying by lunchtime. */
const FLEET_REFRESH_MS = 60_000;

/** The floor of everything BELOW the line, so a drag cannot take the truck
 *  list away entirely — the card would still scroll, but the way to pick a
 *  different vehicle would be gone.  A cap on the SPLITTER, not a
 *  `minHeight` on the list: the list must stay able to shrink to nothing,
 *  which is what makes the card's ceiling work at all (see the card).
 *    8   the search block's top padding
 *  + 34  the search input
 *  + 23  gap and the summary line, when it is shown
 *  + 1   its top border
 *  + 60  one row of list and a little, so the region is visibly a list */
const BELOW_FLOOR_PX = 8 + 34 + 23 + 1 + 60;

export default function InventoryPanel({ abilities, features }: PanelFeatureProps) {
  const canWrite = abilities.includes('inventory.write');
  /** Only when this person may see positions AT ALL.  A position is a
   *  location read whoever asks for it, and Inventory was split out of
   *  Vehicles precisely so it could be granted to somebody who has no
   *  business seeing where the trucks are. */
  const canLocate = features.includes('live-map');
  const [follow, setFollow] = useState(false);
  const [fleet, setFleet] = useState<FleetRow[] | null>(null);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<FleetRow | null>(null);
  /** THREE states, not two.  ``null`` used to mean both "still asking"
   *  and "the answer never came" — and data.ts returns null for a 403
   *  as well, which latches process-wide, so after one refusal every
   *  vehicle a person clicked said "Reading…" for the life of the
   *  panel.  A failure has to be a value, or it wears the label of
   *  whatever state it was folded into. */
  const [items, setItems] = useState<Inventory | 'loading' | 'failed'>('loading');
  /** The chosen truck, for handlers that outlive the render that made
   *  them — the storage listener is attached once. */
  const selectedRef = useRef<FleetRow | null>(null);
  selectedRef.current = selected;
  /** The fleet read, reachable from outside its own effect: a write
   *  changes a count, and a count that waits up to a minute to catch up
   *  reads as a press that did nothing. */
  const reload = useRef<() => void>(() => {});
  /** The add form: closed until asked for.  A truck's contents are the
   *  answer people come for; a form standing open above them would make
   *  every visit start with an empty question. */
  const [adding, setAdding] = useState(false);
  /** Raised by ItemRows while one of its rows is being corrected. */
  const [editing, setEditing] = useState(false);
  /** How much of the column the vehicle card takes, in percent.  It was
   *  a hardcoded 70; seventy is now only where the drag starts. */
  const [cardPct, setCardPct] = useState(70);
  const columnRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  /** Whether dragging would DO anything.
   *
   *  The card is capped with `maxHeight`, not sized with `height`, so a
   *  card whose content is short renders at its content height and a
   *  drag moves nothing — the handle would sit there looking draggable
   *  and lying.  With one item aboard, that is the ordinary case.
   *
   *  Measured against the SMALLEST share the splitter can give, not the
   *  current one: content height does not change as the cap moves, so
   *  this answer holds still for the whole drag instead of vanishing
   *  under the pointer halfway through. */
  const [cardCanResize, setCardCanResize] = useState(false);
  /** The last non-empty category list this account answered with.  A ref,
   *  not state: nothing re-renders because the vocabulary arrived, and it
   *  must survive every vehicle switch in between. */
  const vocabRef = useRef<string[]>([]);
  const [draft, setDraft] = useState({ category: '', label: '', identifier: '' });
  const [saving, setSaving] = useState(false);
  const [addError, setAddError] = useState('');
  /** The item just recorded, so the list can show it landing. */
  const [justAdded, setJustAdded] = useState<number | null>(null);

// Does the card have more content than the SMALLEST share the
// splitter could give it?  If not, dragging changes nothing and the
// handle is not offered — a control that cannot act is worse than a
// control that is absent.  ResizeObserver rather than a render-time
// read: the card grows when an item opens or the Add form appears,
// and neither is a re-render this component would otherwise see.
useEffect(() => {
  const card = cardRef.current;
  const col = columnRef.current;
  if (!selected || !card || !col) { setCardCanResize(false); return; }
  const check = () => {
    const floor = (col.clientHeight * MIN_PCT) / 100;
    setCardCanResize(card.scrollHeight > floor + 1);
  };
  check();
  const ro = new ResizeObserver(check);
  ro.observe(card);
  ro.observe(col);
  return () => ro.disconnect();
}, [selected, items]);

  // Read on open, not subscribed: Settings lives in this same panel, so
  // a change there re-mounts this on the way back.
  useEffect(() => {
    if (!canLocate) return;
    void getFollowPref().then(setFollow);
  }, [canLocate]);

  // ── the fleet answer ────────────────────────────────────────────
  useEffect(() => {
    let stopped = false;
    const load = async () => {
      try {
        // ?all=1 — every vehicle this person may see, carrying items or
        // not.  Without it the answer is the MAP's question (only what
        // has something aboard), and a truck missing from the list read
        // as a truck that does not exist.
        const out = await apiJSON<{ vehicles?: FleetRow[] }>('/extension/inventory-fleet?all=1');
        if (stopped) return;
        const rows = out.vehicles ?? [];
        setFleet(rows);
        // The chosen truck is a ROW, and a minute-old row carries
        // minute-old counts: somebody marks a dashcam missing and the
        // card above the list would go on saying "all settled".  Re-read
        // it from the answer that just arrived, and let it go if the
        // truck has left the answer entirely.
        setSelected((cur) => (cur ? rows.find((r) => r.vehicle_id === cur.vehicle_id) ?? null : null));
        setError('');
      } catch (e) {
        if (stopped) return;
        // Keep whatever is on screen: a refresh that fails should cost
        // the freshness, never the answer already being read.
        setFleet((f) => f ?? []);
        setError(e instanceof Error ? e.message : 'Could not read inventory');
      }
    };
    reload.current = () => { void load(); };
    void load();
    const t = setInterval(() => { void load(); }, FLEET_REFRESH_MS);
    return () => { stopped = true; clearInterval(t); };
  }, []);

  // ── one truck's contents ────────────────────────────────────────
  const select = (row: FleetRow | null) => {
    setSelected(row);
    setItems('loading');
    // A form left open across a selection would offer to record onto the
    // vehicle you just left, with the words you typed for the other one.
    setAdding(false);
    setAddError('');
    setJustAdded(null);
    setDraft({ category: '', label: '', identifier: '' });
    if (!row) return;
    void inventoryFor(row.vehicle_id).then((ob) => {
      // Drop an answer that belongs to a truck the person has left.
      if (selectedRef.current?.vehicle_id === row.vehicle_id) setItems(ob ?? 'failed');
    });
    // …and point Google's map at it, the way the Live Map does — so a
    // unit picked here does not have to be hunted for over there.
    if (canLocate && follow) {
      void positionOf(row.vehicle_id).then((at) => {
        if (at && selectedRef.current?.vehicle_id === row.vehicle_id) {
          void followInGoogleMaps(searchUrl(at[0], at[1]));
        }
      });
    }
  };

  /** The current `select`, for the storage listener that is attached
   *  once.  It closes over the follow switch now, so the effect cannot
   *  simply capture it and stay correct — and re-subscribing on every
   *  change would drop a click that arrived mid-swap. */
  const selectRef = useRef(select);
  selectRef.current = select;

  // ── a truck clicked on Google's map ─────────────────────────────
  useEffect(() => {
    if (!fleet) return;
    const take = (raw: unknown) => {
      const want = readPendingSelect(raw);
      if (!want) return;
      // Matched by unit number within its company: this surface has no
      // map ids, and the two together are what makes a unit unique when
      // an account runs several companies.
      // Consumed whatever happens.  Left in storage, a click that names
      // no row here is re-read on every fleet refresh and on every
      // remount, so a vehicle the person has moved on from keeps trying
      // to select itself.
      void chrome.storage.local.remove(PENDING_SELECT_KEY);
      const row = fleet.find((r) =>
        r.name === want.name && (!want.company || !r.company || r.company === want.company));
      if (!row) return;
      selectRef.current(row);
    };
    void chrome.storage.local.get(PENDING_SELECT_KEY).then((got) => take(got[PENDING_SELECT_KEY]));
    const onChange = (changes: Record<string, chrome.storage.StorageChange>, area: string) => {
      if (area === 'local' && PENDING_SELECT_KEY in changes) take(changes[PENDING_SELECT_KEY].newValue);
    };
    chrome.storage.onChanged.addListener(onChange);
    return () => chrome.storage.onChanged.removeListener(onChange);
  }, [fleet]);

  /** After a write: this truck's contents are stale, and so is its line
   *  in the fleet answer.  Both are re-read, in that order, so the list
   *  and the card cannot disagree about the truck in front of you. */
  const afterWrite = async (vehicleId: number) => {
    forgetVehicle(vehicleId);
    const ob = await inventoryFor(vehicleId);
    if (selectedRef.current?.vehicle_id === vehicleId) setItems(ob ?? 'failed');
    reload.current();
  };

  // The account's category vocabulary OUTLIVES one vehicle's read.  It
  // used to be [] whenever `items` was 'loading' or 'failed' — and the
  // Add button is offered in every state — so opening the form during a
  // slow or refused read emptied the datalist and told the person every
  // word they typed was a NEW category.  It was not; the server folds it
  // into the existing one.  The claim was simply false.
  if (items !== 'loading' && items !== 'failed' && items.categories.length
      && items.categories !== vocabRef.current) vocabRef.current = items.categories;
  const known = vocabRef.current;
  const canSubmit = Boolean(draft.label.trim() && draft.category.trim());
  /** Whether what is typed would MAKE a category rather than pick one.
   *  Compared the way the server normalises: case and spacing folded. */
  const asKey = (v: string) => v.trim().toLowerCase().split(/\s+/).join('_');
  // Silent until the vocabulary is KNOWN: "New category" is a claim, and
  // a claim made with nothing to check it against is a guess.  On a
  // first load, or a slow one, `known` is empty and every word typed
  // looked new — while the server was folding it into a category that
  // already existed.
  const isNewCategory = known.length > 0
    && Boolean(draft.category.trim())
    && !known.some((c) => asKey(c) === asKey(draft.category));
  const submitAdd = async () => {
    if (!selected || !draft.label.trim() || !draft.category.trim()) return;
    setSaving(true);
    setAddError('');
    try {
      const id = await addItem(selected.vehicle_id, {
        category: draft.category.trim(),
        label: draft.label.trim(),
        identifier: draft.identifier.trim(),
      });
      setJustAdded(id);
      setDraft({ category: '', label: '', identifier: '' });
      setAdding(false);
      await afterWrite(selected.vehicle_id);
    } catch (e) {
      // Said here, beside the form, with the draft still in it: a
      // failure that clears what somebody typed is a failure twice.
      setAddError(e instanceof Error ? e.message : 'That did not save');
    } finally {
      setSaving(false);
    }
  };

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return fleet ?? [];
    return (fleet ?? []).filter((r) =>
      r.name.toLowerCase().includes(q) || r.company.toLowerCase().includes(q));
  }, [fleet, search]);

  const attentionTrucks = (fleet ?? []).filter((r) => r.attention > 0).length;
  const withItems = (fleet ?? []).filter((r) => r.total > 0).length;
  const openDashboard = () => { void chrome.tabs.create({ url: `${DASHBOARD_BASE}/inventory` }); };

  return (
    // Same skeleton as Live Map: fixed rows top and bottom, ONE region
    // that gives — here the truck list, since there is no map to be it.
    <div ref={columnRef} style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: 8 }}>
      {/* THE CHANGING THING, first.  The search box and the vehicle
          list are the same on every visit; the chosen vehicle's contents
          are the only part that answers a question — so it takes the top
          of the panel, the way the Live Map gives the top to its map.
          It also stops the card pushing the list down as it grows. */}
      {selected && (
        // A CEILING, because the panel's contract is that one region
        // absorbs growth and this is not it.  Measured at 320px with the
        // form open and a full item list: ~615px of card, ~692 with the
        // search block — past a 600px panel the card would simply run
        // off the bottom, since neither it nor the root scrolls.
        //
        // `flexShrink: 0` is what makes the ceiling safe, and its absence
        // is what broke this once: the truck list's flex-basis is its
        // CONTENT, and with 190 vehicles that overflows the column by
        // thousands of pixels.  Flex then distributes the deficit across
        // every shrinkable item — so a `minHeight: 0` here (added to let
        // the ceiling work) removed this card's automatic minimum and it
        // was squeezed to a scrolling sliver showing one line.  It must
        // not shrink at all; the list below is the one that gives.
        <div ref={cardRef} className="sheet"
             style={{ flexShrink: 0, maxHeight: `${cardPct}%`, overflowY: 'auto' }}>
          <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap', rowGap: 6 }}>
            <strong style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>
              {selected.name}
              {selected.company && <span className="muted" style={{ fontWeight: 400 }}> · {selected.company}</span>}
            </strong>
            <button className="btn" onClick={() => select(null)}
                    title="Clear the selection">Close</button>
          </div>
          <p className="muted" style={{ margin: 0, fontSize: 12 }}>
            {selected.total === 0 ? 'Nothing recorded' : `${selected.total} item${selected.total === 1 ? '' : 's'}`}
            {selected.attention > 0 && (
              <span style={{ color: 'var(--warn)', fontWeight: 600 }}>
                {' · '}{selected.attention} flagged
              </span>
            )}
          </p>
          {items === 'loading'
            ? <p className="muted" style={{ margin: 0, fontSize: 12 }}>Reading…</p>
            : items === 'failed'
            // Named, not hidden.  It covers a refusal, a timeout and an
            // unreachable API alike — all three are "we do not know",
            // and none of them is "there is nothing aboard".
            ? <p style={{ margin: 0, fontSize: 12 }}>
                <span style={{ color: 'var(--danger)' }}>Could not read what is aboard.</span>{' '}
                <button type="button" className="link"
                        onClick={() => { retryInventory(); select(selected); }}>
                  Try again
                </button>
              </p>
            : items.items.length === 0
            // Only when the read SUCCEEDED and came back empty — the
            // failure has its own value above, so this can no longer
            // claim "nothing recorded" about a vehicle we could not
            // read at all.
            ? <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                Nothing recorded on this vehicle yet.
              </p>
            : <ItemRows items={items.items}
                         focusId={justAdded}
                         // Taller than the map card's seven rows: there
                         // the ceiling keeps a natural-height card from
                         // pushing the map to its floor, but HERE the
                         // card is the main event and the truck list
                         // below it is the region that gives.  It yields
                         // while the form is open: what is aboard matters
                         // less, for that moment, than what is being
                         // recorded.
                         // While a row is being CORRECTED the list drops
                         // its ceiling entirely: the form is ~200px and
                         // this ceiling is sized for rows, so inside it
                         // the form became a ~90px scroller showing one
                         // field at a time, with its own scrollbar,
                         // stacked under an open Add form.  The card
                         // above already scrolls (maxHeight 70%), so
                         // lifting it gives ONE scroller instead of two
                         // nested ones.
                         maxHeight={editing ? null : (adding ? 96 : 280)}
                         onEditingChange={(on) => {
                           setEditing(on);
                           // Two forms at once is a wall at 320px, and
                           // it is what made the squeeze visible.
                           if (on) setAdding(false);
                         }}
                         onVerify={canWrite ? async (id) => {
                           await verifyItem(id);
                           await afterWrite(selected.vehicle_id);
                         } : undefined}
                         onStatus={canWrite ? async (id, st) => {
                           await setItemStatus(id, st);
                           await afterWrite(selected.vehicle_id);
                         } : undefined}
                         onEdit={canWrite ? async (id, patch) => {
                           await editItem(id, patch);
                           await afterWrite(selected.vehicle_id);
                         } : undefined}
                         categories={known} />}
          {/* ADD, from the truck rather than from a desk.  The walk back
              to a laptop is where the record stops being made at all.
              REMOVE and TRANSFER are deliberately not here and not
              reachable from this key: they are how a loss gets tidied
              away, and they stay at a desk with the registry open. */}
          {canWrite && !adding && (
            <button className="btn" style={{ justifySelf: 'start' }}
                    title="Record something aboard this vehicle"
                    onClick={() => { setAddError(''); setAdding(true); }}>
              Add item
            </button>
          )}
          {canWrite && adding && (
            <div style={{ display: 'grid', gap: 8, borderTop: '1px solid var(--border)', paddingTop: 6 }}>
              {/* Each field keeps a LABEL.  Three identical boxes told
                  apart only by placeholder stop telling anything apart
                  the moment one is filled — the placeholder goes, and a
                  person who paused cannot re-read what they answered. */}
              {/* The vocabulary is OPEN, so the placeholder teaches the
                  BEHAVIOUR rather than listing three of six built-ins —
                  a list reads as the only choices, which this is not. */}
              <Field label={FIELD_LABEL.category} required>
                {/* An OPEN vocabulary, so a list of what this account
                    already uses AND a free field — a datalist is both,
                    and it is one control rather than a select plus an
                    "other…" escape nobody finds. */}
                {/* Two domain examples AND the openness, in one line.
                    "Choose one" alone pointed at a list that is invisible
                    until the field is focused; a bare list alone read as
                    the only choices, which it is not. */}
                <input className="input" list="fourtruck-inv-categories"
                       placeholder="Camera, fuel card… or type a new one"
                       value={draft.category} disabled={saving}
                       onChange={(e) => setDraft((d) => ({ ...d, category: e.target.value }))} />
                <datalist id="fourtruck-inv-categories">
                  {known.map((c) => <option key={c} value={humanize(c)} />)}
                </datalist>
                {/* Typing "Dash Cam" beside an existing "camera" makes a
                    SECOND category and splits every count that follows.
                    The list suggests; it does not constrain — so the one
                    thing owed is to say when a new one is being made. */}
                {isNewCategory && (
                  <p className="muted" style={{ margin: 0, fontSize: 11 }}>
                    New category — it will join your list.
                  </p>
                )}
              </Field>
              {/* "Name", a noun, like every other label here — "What it
                  is" was a question among nouns, and the dashboard
                  called the same field "Label".  One field, one name.

                  The example names no vendor.  This product talks to
                  Samsara, Motive and Datatruck; putting one of them in
                  the placeholder makes the form read as built for that
                  one, which is exactly how it read.  It shows what the
                  field is FOR instead: telling two of a kind apart. */}
              <Field label={FIELD_LABEL.label} required>
                <input className="input" placeholder="e.g. Front dashcam"
                       value={draft.label} disabled={saving}
                       onChange={(e) => setDraft((d) => ({ ...d, label: e.target.value }))} />
              </Field>
              {/* NOT "(optional)".  The API accepts a blank, but the
                  storage layer is blunt about what this field is for:
                  it "is what makes loss provable".  Calling it optional
                  tells somebody it does not matter, on the one field
                  that decides whether a missing dashcam can be shown to
                  have been theirs. */}
              <Field label={FIELD_LABEL.identifier}>
                {/* Spelled out, and no invented serial: a made-up
                    "GJ8-4471" teaches nothing, and bullet-masked digits
                    imply the field masks what is typed.  It does not. */}
                <input className="input" placeholder="Serial, card last 4, transponder no."
                       value={draft.identifier} disabled={saving}
                       onChange={(e) => setDraft((d) => ({ ...d, identifier: e.target.value }))} />
                <p className="muted" style={{ margin: 0, fontSize: 11 }}>
                  This is what proves a missing item was yours.
                </p>
              </Field>
              {addError && <p style={{ color: 'var(--danger)', margin: 0, fontSize: 12 }}>{addError}</p>}
              <div className="row" style={{ gap: 6 }}>
                {/* Disabled WITH A REASON, which is this panel's rule
                    everywhere else: a grey button that will not say what
                    it is waiting for is a dead end. */}
                <button className="btn primary" disabled={saving || !canSubmit}
                        title={canSubmit ? 'Record this item' : 'Category and name are required'}
                        onClick={() => void submitAdd()}>
                  {saving ? 'Saving…' : 'Add'}
                </button>
                <button className="btn" disabled={saving}
                        onClick={() => { setAdding(false); setAddError(''); }}>Cancel</button>
              </div>
            </div>
          )}
          {/* No hand-off link on the card.  It existed because
              correcting a record used to be a dashboard errand, and
              correcting is the one thing somebody standing at a truck
              actually needs — that is here now.  What the link still
              pointed at was retiring and moving alone: two desk actions
              nobody opens this panel to perform, taking a line in a
              320px column every time anyone looked at a vehicle. */}
        </div>
      )}

      {/* Between the card and everything under it, and only while a
          vehicle is chosen: with no card there is one region, and a line
          dividing one region divides nothing. */}
      {selected && cardCanResize && (
        <Splitter storageKey="inventoryCardPct" fallback={70}
                  columnRef={columnRef} onChange={setCardPct}
                  minBelowPx={BELOW_FLOOR_PX}
                  label="Resize the vehicle card" />
      )}

      {/* The divider sits ABOVE the search, not below it.  The search
          and the summary describe the list under them; a border between
          the two left the filter floating on bare ground between a
          filled card and a bordered region, belonging to neither. */}
      <div style={{ padding: '8px 10px 0', display: 'grid', gap: 6, flexShrink: 0,
                    borderTop: '1px solid var(--border)' }}>
        <input className="input" placeholder="Search vehicles…" value={search}
               onChange={(e) => setSearch(e.target.value)} />
        {/* The one number worth reading before anything is chosen. */}
        {/* Suppressed entirely when the first read failed: `fleet` is
            [] then, and "No vehicles to show" would be a confident
            falsehood printed directly above the red line saying we
            could not read the list at all. */}
        {fleet !== null && !(error && fleet.length === 0) && (
          <p className="muted" style={{ margin: 0, fontSize: 12 }}>
            {/* The list holds every vehicle this person may see now, so
                the first number is the fleet and the second is how much
                of it carries anything.  "Vehicles", not "trucks": the
                registry holds trailers and manual units too.

                Each count keeps its unit — a bare "2 of 23" here and a
                bare "2 of 7" in a row below would be the same shape
                counting two different things a few pixels apart. */}
            {/* Two rules this line kept breaking.
                ONE: a count carries its unit.  "N need attention" counts
                ITEMS in the card below and in the map card; here it
                counts VEHICLES, and the bare phrase made one screen say
                the same words about two different things.
                TWO: a claim may not outrun what was inspected.  "all
                settled" hung off the FLEET count, so twenty-one vehicles
                nobody has ever inventoried were declared settled — the
                exact assertion the row dot refuses to make one region
                below, where an empty vehicle is muted on purpose. */}
            {/* While a search runs the headline describes the LIST, not
                the fleet: "23 vehicles" sitting over a single row is a
                number about something the reader cannot see. */}
            {search.trim()
              ? `${shown.length} of ${fleet.length} vehicle${fleet.length === 1 ? '' : 's'} match`
              : fleet.length === 0
              ? 'No vehicles to show'
              : `${fleet.length} vehicle${fleet.length === 1 ? '' : 's'}`
                + (withItems === 0
                  ? ''
                  : ` · ${withItems} with items`
                    + (attentionTrucks === 0
                      ? ', none flagged'
                      : `, ${attentionTrucks} flagged`))}

          </p>
        )}
        {/* The follow SWITCH is not here.  It is a preference of the
            panel, not of Inventory, and it used to be rendered in three
            places for one stored value.  Settings owns it; this feature
            reads it and behaves accordingly. */}
        {error && <p style={{ color: 'var(--danger)', margin: 0, fontSize: 12 }}>{error}</p>}
      </div>

      {/* THE elastic region. */}
      {/* basis 0, not auto: with `auto` the basis is the content — 190
          rows — so the column overflows before anything is laid out and
          the whole layout becomes a shrink negotiation.  From 0 it is
          simply "take what is left", which is what this region is. */}
      <div style={{ flex: '1 1 0', minHeight: 0, overflowY: 'auto' }}>
        {fleet === null && <p className="muted" style={{ padding: 10, margin: 0 }}>Loading…</p>}
        {/* It is a HEADER now, not an empty state: the list below it is
            full of vehicles, they simply have nothing recorded.  And it
            stands down while a search is running, so the reason the list
            looks empty is never stated twice in two voices. */}
        {fleet !== null && fleet.length > 0 && withItems === 0 && !search.trim() && (
          // An empty state that names the way forward, not just the void
          // — and the way forward changed.  It used to send everybody to
          // the dashboard because adding was only possible there; a
          // person who may write now does it here, and telling them
          // otherwise sends them away from the panel they opened.
          <div style={{ padding: 10, display: 'grid', gap: 6 }}>
            <p className="muted" style={{ margin: 0 }}>
              {canWrite
                ? 'Nothing has been recorded on any of these vehicles yet. Pick one below and press Add item.'
                : 'Nothing has been recorded on any of these vehicles yet — dashcams, fuel cards, toll transponders and ELDs are added on the dashboard.'}
            </p>
            {!canWrite && (
              <button type="button" className="link" onClick={openDashboard}
                      style={{ justifySelf: 'start' }}>
                Open Inventory on 4truck →
              </button>
            )}
          </div>
        )}
        {/* "Nothing here" would be false: the search is what emptied it. */}
        {fleet !== null && fleet.length > 0 && shown.length === 0 && (
          <p className="muted" style={{ padding: 10, margin: 0 }}>
            No vehicle matches “{search.trim()}”.
          </p>
        )}
        {shown.map((r) => {
          const chosen = selected?.vehicle_id === r.vehicle_id;
          return (
            <button key={r.vehicle_id} type="button" onClick={() => select(r)}
                    className="row rowbtn" aria-pressed={chosen}
                    // Live Map's row, to the pixel: 10px dot, bold unit,
                    // 8px 10px.  The same object in the same role must
                    // not wear two faces across one panel's features.
                    style={{ width: '100%', gap: 8, padding: '8px 10px', background: chosen ? 'rgba(255,255,255,.06)' : 'transparent', borderBottom: '1px solid var(--border)' }}>
              {/* Three states, not two.  Green says "aboard and settled";
                  on a vehicle nobody has ever inventoried it would be
                  asserting a check that never happened, so an empty one
                  is muted — no claim either way. */}
              <span aria-hidden
                    title={r.total === 0 ? 'Nothing recorded'
                      : r.attention > 0 ? `${r.attention} of ${r.total} items flagged` : 'Nothing flagged'}
                    style={{ width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
                             background: r.total === 0 ? 'var(--muted)'
                               : r.attention > 0 ? 'var(--warn)' : 'var(--ok)' }} />
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>
                <span style={{ fontWeight: 600 }}>{r.name}</span>
                {r.company && <span className="muted" style={{ fontWeight: 400 }}> · {r.company}</span>}
              </span>
              <span style={{ marginLeft: 'auto', flexShrink: 0, fontSize: 12,
                             color: r.attention > 0 ? 'var(--warn)' : 'var(--muted)',
                             fontStyle: r.total === 0 ? 'italic' : undefined,
                             fontWeight: r.attention > 0 ? 600 : 400 }}>
                {/* "2 of 7" states a fraction with no verb — 2 of 7
                    what?  The word lived only in a colour and in a
                    tooltip on an aria-hidden span. */}
                {r.total === 0 ? 'nothing recorded'
                  : r.attention > 0 ? `${r.attention} flagged of ${r.total}` : `${r.total} items`}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
