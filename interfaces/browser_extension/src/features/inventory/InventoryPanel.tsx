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
import { DASHBOARD_BASE } from '../../connect';
import { PENDING_SELECT_KEY, readPendingSelect } from '../maps-overlay/bridge';
import { forgetVehicle, inventoryFor, retryInventory, setItemStatus, verifyItem, type Inventory } from './data';
import ItemRows from './ItemRows';
import type { PanelFeatureProps } from '../../shell/registry';

/** One truck's line in the fleet answer — counts and a name, never
 *  contents.  An item's own label arrives when a truck is chosen. */
interface FleetRow {
  vehicle_id: number;
  name: string;
  company: string;
  total: number;
  attention: number;
}

/** Inventory is not live data: somebody adds a dashcam, not thirty
 *  trucks move.  Slow enough to be free, often enough that a panel left
 *  open all morning is not lying by lunchtime. */
const FLEET_REFRESH_MS = 60_000;

export default function InventoryPanel({ abilities }: PanelFeatureProps) {
  const canWrite = abilities.includes('inventory.write');
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
    if (!row) return;
    void inventoryFor(row.vehicle_id).then((ob) => {
      // Drop an answer that belongs to a truck the person has left.
      if (selectedRef.current?.vehicle_id === row.vehicle_id) setItems(ob ?? 'failed');
    });
  };

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
      select(row);
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
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: 8 }}>
      <div style={{ padding: '8px 10px 0', display: 'grid', gap: 6 }}>
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
        {error && <p style={{ color: 'var(--danger)', margin: 0, fontSize: 12 }}>{error}</p>}
      </div>

      {/* The chosen truck, above the list — the same place the Live Map
          puts the truck it is describing. */}
      {selected && (
        <div className="sheet">
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
                        onClick={() => { retryInventory(); select(selected); }}
                        style={{ background: 'none', border: 0, padding: 0, font: 'inherit',
                                 fontSize: 12, cursor: 'pointer', minHeight: 24 }}>
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
                         // Taller than the map card's seven rows: there
                         // the ceiling keeps a natural-height card from
                         // pushing the map to its floor, but HERE the
                         // card is the main event and the truck list
                         // below it is the region that gives.
                         maxHeight={280}
                         onVerify={canWrite ? async (id) => {
                           await verifyItem(id);
                           await afterWrite(selected.vehicle_id);
                         } : undefined}
                         onStatus={canWrite ? async (id, st) => {
                           await setItemStatus(id, st);
                           await afterWrite(selected.vehicle_id);
                         } : undefined} />}
          {/* Read here, changed there.  The link goes to /inventory
              rather than the truck's own page: that page is gated on
              can_view_vehicles, the one grant this reader may not have
              and the whole reason Inventory became its own feature. */}
          <button type="button" className="link" onClick={openDashboard}
                  style={{ justifySelf: 'start', background: 'none', border: 0, padding: '2px 0',
                           font: 'inherit', fontSize: 12, cursor: 'pointer', minHeight: 24 }}>
            Manage on 4truck →
          </button>
        </div>
      )}

      {/* THE elastic region. */}
      <div style={{ flex: '1 1 auto', minHeight: 0, overflowY: 'auto',
                    borderTop: '1px solid var(--border)' }}>
        {fleet === null && <p className="muted" style={{ padding: 10, margin: 0 }}>Loading…</p>}
        {/* It is a HEADER now, not an empty state: the list below it is
            full of vehicles, they simply have nothing recorded.  And it
            stands down while a search is running, so the reason the list
            looks empty is never stated twice in two voices. */}
        {fleet !== null && fleet.length > 0 && withItems === 0 && !search.trim() && (
          // An empty state that names the way forward, not just the void.
          <div style={{ padding: 10, display: 'grid', gap: 6 }}>
            <p className="muted" style={{ margin: 0 }}>
              Nothing has been recorded on any of these vehicles yet — dashcams,
              fuel cards, toll transponders and ELDs are added on the dashboard.
            </p>
            <button type="button" className="link" onClick={openDashboard}
                    style={{ justifySelf: 'start', background: 'none', border: 0, padding: '2px 0',
                             font: 'inherit', fontSize: 12, cursor: 'pointer', minHeight: 24 }}>
              Open Inventory on 4truck →
            </button>
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
                    style={{ width: '100%', gap: 8, padding: '8px 10px', minHeight: 24,
                             background: chosen ? 'rgba(255,255,255,.06)' : 'transparent',
                             border: 0, borderBottom: '1px solid var(--border)',
                             color: 'var(--fg)', cursor: 'pointer', font: 'inherit', textAlign: 'left' }}>
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
