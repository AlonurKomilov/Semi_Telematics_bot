/**
 * Onboard Inventory, in the side panel.
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
import { inventoryFor, type Onboard } from './data';
import ItemRows from './ItemRows';

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

export default function InventoryPanel() {
  const [fleet, setFleet] = useState<FleetRow[] | null>(null);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<FleetRow | null>(null);
  const [items, setItems] = useState<Onboard | null>(null);
  /** The chosen truck, for handlers that outlive the render that made
   *  them — the storage listener is attached once. */
  const selectedRef = useRef<FleetRow | null>(null);
  selectedRef.current = selected;

  // ── the fleet answer ────────────────────────────────────────────
  useEffect(() => {
    let stopped = false;
    const load = async () => {
      try {
        const out = await apiJSON<{ vehicles?: FleetRow[] }>('/extension/inventory-fleet');
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
    void load();
    const t = setInterval(() => { void load(); }, FLEET_REFRESH_MS);
    return () => { stopped = true; clearInterval(t); };
  }, []);

  // ── one truck's contents ────────────────────────────────────────
  const select = (row: FleetRow | null) => {
    setSelected(row);
    setItems(null);
    if (!row) return;
    void inventoryFor(row.vehicle_id).then((ob) => {
      // Drop an answer that belongs to a truck the person has left.
      if (selectedRef.current?.vehicle_id === row.vehicle_id) setItems(ob);
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
      const row = fleet.find((r) =>
        r.name === want.name && (!want.company || !r.company || r.company === want.company));
      if (!row) return;
      void chrome.storage.local.remove(PENDING_SELECT_KEY);
      select(row);
    };
    void chrome.storage.local.get(PENDING_SELECT_KEY).then((got) => take(got[PENDING_SELECT_KEY]));
    const onChange = (changes: Record<string, chrome.storage.StorageChange>, area: string) => {
      if (area === 'local' && PENDING_SELECT_KEY in changes) take(changes[PENDING_SELECT_KEY].newValue);
    };
    chrome.storage.onChanged.addListener(onChange);
    return () => chrome.storage.onChanged.removeListener(onChange);
  }, [fleet]);

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return fleet ?? [];
    return (fleet ?? []).filter((r) =>
      r.name.toLowerCase().includes(q) || r.company.toLowerCase().includes(q));
  }, [fleet, search]);

  const attentionTrucks = (fleet ?? []).filter((r) => r.attention > 0).length;
  const openDashboard = () => { void chrome.tabs.create({ url: `${DASHBOARD_BASE}/inventory` }); };

  return (
    // Same skeleton as Live Map: fixed rows top and bottom, ONE region
    // that gives — here the truck list, since there is no map to be it.
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: 8 }}>
      <div style={{ padding: '8px 10px 0', display: 'grid', gap: 6 }}>
        <input className="input" placeholder="Search trucks…" value={search}
               onChange={(e) => setSearch(e.target.value)} />
        {/* The one number worth reading before anything is chosen. */}
        {fleet !== null && (
          <p className="muted" style={{ margin: 0, fontSize: 12 }}>
            {/* "carry items", not "tracked": this list holds the trucks
                that HAVE something recorded, and a truck missing from it
                has an empty inventory, not a missing existence.  Without
                that word somebody hunting truck 117 reads its absence as
                "no such truck".

                And the count keeps its unit.  A bare "2 of 23" here and
                a bare "2 of 7" in a row below would be the same shape
                counting two different things a few pixels apart. */}
            {fleet.length === 0
              ? 'No items recorded on any truck yet'
              : `${fleet.length} truck${fleet.length === 1 ? '' : 's'} carry items`
                + (attentionTrucks === 0
                  ? ' · all settled'
                  : ` · ${attentionTrucks} need attention`)}
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
            {selected.total} item{selected.total === 1 ? '' : 's'}
            {selected.attention > 0 && (
              <span style={{ color: 'var(--warn)', fontWeight: 600 }}> · {selected.attention} need attention</span>
            )}
          </p>
          {items === null
            ? <p className="muted" style={{ margin: 0, fontSize: 12 }}>Reading…</p>
            : <ItemRows items={items.items} />}
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
        {fleet !== null && fleet.length === 0 && (
          // An empty state that names the way forward, not just the void.
          <div style={{ padding: 10, display: 'grid', gap: 6 }}>
            <p className="muted" style={{ margin: 0 }}>
              Nothing is recorded on any truck yet — dashcams, fuel cards,
              toll transponders and ELDs are added on the dashboard.
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
            No truck matching “{search.trim()}” carries any items.
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
              <span aria-hidden style={{ width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
                                         background: r.attention > 0 ? 'var(--warn)' : 'var(--ok)' }} />
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>
                <span style={{ fontWeight: 600 }}>{r.name}</span>
                {r.company && <span className="muted" style={{ fontWeight: 400 }}> · {r.company}</span>}
              </span>
              <span style={{ marginLeft: 'auto', flexShrink: 0, fontSize: 12,
                             color: r.attention > 0 ? 'var(--warn)' : 'var(--muted)',
                             fontWeight: r.attention > 0 ? 600 : 400 }}>
                {r.attention > 0 ? `${r.attention} of ${r.total} items` : `${r.total} items`}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
