import { Fragment, useEffect, useState, useCallback } from 'react';
import { MapPin } from 'lucide-react';
import { apiJSON, ApiError } from '../api/client';

// Global vendor directory curation — the platform-owned identity list
// every account's private vendors can link to.  Operators approve or
// reject account suggestions (pending queue floats to the top), edit
// contact/services, and add entries directly (born active).
// Identity data only: this screen never sees any account's invoices,
// spend, or which accounts use a shop (suggested_by_account is shown
// as a bare account id for audit, nothing more).

interface PendingReview {
  id: number;
  entry_id: number;
  entry_name: string;
  account_id: number;
  rating: number;
  comment: string;
  updated_at: string;
}

interface DirEntry {
  id: number;
  name: string;
  address: string;
  phone: string;
  email: string;
  website: string;
  services: string;
  notes: string;
  status: 'pending' | 'active' | 'rejected';
  source: string;
  suggested_by_account: number | null;
  lat: number | null;
  lng: number | null;
  /** Multi-location brand family ("TA / Petro"); '' = independent. */
  chain: string;
  created_at: string;
}

interface GeoSuggestion {
  lat: number;
  lng: number;
  label: string;
}

const STATUS_BADGE: Record<DirEntry['status'], string> = {
  pending: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  active: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  rejected: 'bg-slate-700/40 text-slate-500 border-slate-600/40',
};

const inputCls =
  'bg-slate-950 border border-slate-800 rounded px-2 py-1 text-sm text-slate-200 ' +
  'placeholder:text-slate-600 focus:outline-none focus:border-slate-600 w-full';

export default function VendorDirectoryPage() {
  const [entries, setEntries] = useState<DirEntry[]>([]);
  const [filter, setFilter] = useState<'all' | DirEntry['status']>('all');
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<number | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<Partial<DirEntry>>({});
  const [newName, setNewName] = useState('');
  const [newAddress, setNewAddress] = useState('');
  const [newPhone, setNewPhone] = useState('');
  const [newChain, setNewChain] = useState('');
  const [reviews, setReviews] = useState<PendingReview[]>([]);
  // Scale helpers: the directory grows to hundreds of chain locations.
  const [search, setSearch] = useState('');
  const [shownCount, setShownCount] = useState(100);
  // Bulk import (spreadsheet paste, TAB-separated).
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [importBusy, setImportBusy] = useState(false);
  const [importResult, setImportResult] = useState('');

  // Location editor (one entry at a time): geocode SUGGESTS, the
  // operator confirms — nothing is stored until Save.
  const [geoOpen, setGeoOpen] = useState<number | null>(null);
  const [geoLat, setGeoLat] = useState('');
  const [geoLng, setGeoLng] = useState('');
  const [geoQuery, setGeoQuery] = useState('');
  const [geoSug, setGeoSug] = useState<GeoSuggestion[]>([]);
  const [geoBusy, setGeoBusy] = useState(false);
  const [geoErr, setGeoErr] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    setErr('');
    apiJSON<{ entries: DirEntry[] }>('/system/vendor-directory')
      .then((r) => setEntries(r.entries))
      .then(() => apiJSON<{ reviews: PendingReview[] }>('/system/vendor-directory/reviews?status=pending'))
      .then((r) => setReviews(r.reviews))
      .catch((e: unknown) => {
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          setErr('Session expired or no operator access.');
        } else {
          setErr(e instanceof Error ? e.message : 'Failed to load');
        }
      })
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  const act = async (id: number, fn: () => Promise<unknown>) => {
    setBusy(id);
    try { await fn(); load(); }
    catch (e) { setErr(e instanceof Error ? e.message : 'Action failed'); }
    finally { setBusy(null); }
  };

  const approve = (id: number) =>
    act(id, () => apiJSON(`/system/vendor-directory/${id}/approve`, { method: 'POST' }));
  const reject = (id: number) =>
    act(id, () => apiJSON(`/system/vendor-directory/${id}/reject`, { method: 'POST' }));
  const saveEdit = (id: number) =>
    act(id, async () => {
      await apiJSON(`/system/vendor-directory/${id}`, { method: 'PUT', body: draft });
      setEditing(null);
    });
  const create = () =>
    act(-1, async () => {
      if (!newName.trim()) return;
      await apiJSON('/system/vendor-directory', {
        method: 'POST',
        body: { name: newName, address: newAddress, phone: newPhone, chain: newChain },
      });
      setNewName(''); setNewAddress(''); setNewPhone(''); setNewChain('');
    });

  // Distinct existing chain labels — offered as datalist suggestions so
  // one brand doesn't fork into "TA/Petro" vs "TA / Petro" spellings.
  const chainOptions = [...new Set(entries.map(e => e.chain).filter(Boolean))].sort();

  const reviewAct = (id: number, verb: 'approve' | 'reject') =>
    act(id, () => apiJSON(`/system/vendor-directory/reviews/${id}/${verb}`, { method: 'POST' }));

  const openGeo = (e: DirEntry) => {
    setGeoOpen(e.id);
    setGeoLat(e.lat != null ? String(e.lat) : '');
    setGeoLng(e.lng != null ? String(e.lng) : '');
    setGeoQuery([e.name, e.address].filter(Boolean).join(', '));
    setGeoSug([]);
    setGeoErr('');
  };

  const closeGeo = () => {
    setGeoOpen(null);
    setGeoSug([]);
    setGeoErr('');
  };

  const geocode = async () => {
    if (geoQuery.trim().length < 3) return;
    setGeoBusy(true);
    setGeoErr('');
    setGeoSug([]);
    try {
      const r = await apiJSON<{ results: GeoSuggestion[] }>(
        `/system/vendor-directory/geocode?q=${encodeURIComponent(geoQuery.trim())}`,
      );
      setGeoSug(r.results);
      if (r.results.length === 0) setGeoErr('No matches — refine the address or enter coordinates manually.');
    } catch (e) {
      setGeoErr(e instanceof Error ? e.message : 'Geocoding failed');
    } finally {
      setGeoBusy(false);
    }
  };

  // Parsed coordinates for validation + the pin preview.
  const latNum = geoLat.trim() === '' ? null : Number(geoLat);
  const lngNum = geoLng.trim() === '' ? null : Number(geoLng);
  const geoValid =
    latNum != null && lngNum != null &&
    Number.isFinite(latNum) && Number.isFinite(lngNum) &&
    Math.abs(latNum) <= 90 && Math.abs(lngNum) <= 180;
  const geoEmpty = geoLat.trim() === '' && geoLng.trim() === '';

  const saveGeo = (id: number) =>
    act(id, async () => {
      if (!geoValid && !geoEmpty) {
        setGeoErr('Enter both coordinates (lat −90…90, lng −180…180) or clear both.');
        return;
      }
      await apiJSON(`/system/vendor-directory/${id}/geo`, {
        method: 'PUT',
        body: geoEmpty ? { lat: null, lng: null } : { lat: latNum, lng: lngNum },
      });
      closeGeo();
    });

  const runImport = async () => {
    // One row per line, TAB-separated (paste straight from a
    // spreadsheet): name  chain  address  lat  lng  phone  services
    const rows = importText.split('\n').map(l => l.trim()).filter(Boolean).map(l => {
      const c = l.split('\t').map(x => x.trim());
      const num = (v: string) => (v && !isNaN(Number(v)) ? Number(v) : null);
      return {
        name: c[0] ?? '', chain: c[1] ?? '', address: c[2] ?? '',
        lat: num(c[3] ?? ''), lng: num(c[4] ?? ''),
        phone: c[5] ?? '', services: c[6] ?? '',
      };
    }).filter(r => r.name);
    if (!rows.length) { setImportResult('No valid rows — paste TAB-separated lines.'); return; }
    setImportBusy(true);
    setImportResult('');
    try {
      const r = await apiJSON<{ created: number; skipped: number; vendors_adopted: number }>(
        '/system/vendor-directory/import', { method: 'POST', body: { entries: rows } });
      setImportResult(`Imported ${r.created} · skipped ${r.skipped} (existing) · ${r.vendors_adopted} account vendors auto-linked`);
      setImportText('');
      load();
    } catch (e) {
      setImportResult(e instanceof Error ? e.message : 'Import failed');
    } finally {
      setImportBusy(false);
    }
  };

  const q = search.trim().toLowerCase();
  const searched = q
    ? entries.filter(e =>
        e.name.toLowerCase().includes(q) ||
        (e.chain || '').toLowerCase().includes(q) ||
        (e.address || '').toLowerCase().includes(q))
    : entries;
  const filtered = filter === 'all' ? searched : searched.filter(e => e.status === filter);
  const shown = filtered.slice(0, shownCount);
  const pendingCount = entries.filter(e => e.status === 'pending').length;

  return (
    <div className="max-w-5xl">
      <header className="mb-4">
        <h1 className="text-xl font-semibold text-slate-100">Vendor Directory</h1>
        <p className="text-sm text-slate-500 mt-1 max-w-2xl">
          Global repair-shop identities every account can link to. Identity data only —
          no account's invoices or spend ever appear here. Approve or reject the
          suggestion queue; add shops directly (born active).
        </p>
      </header>

      {err && (
        <div className="mb-3 bg-danger/10 border border-danger/40 text-danger text-sm rounded px-3 py-2">
          {err}
        </div>
      )}

      {/* Add entry */}
      <div className="mb-4 bg-slate-900 border border-slate-800 rounded-lg px-4 py-3 flex flex-wrap items-center gap-2">
        <input className={`${inputCls} max-w-56`} placeholder="Shop name" value={newName}
          onChange={e => setNewName(e.target.value)} />
        <input className={`${inputCls} max-w-64`} placeholder="Address" value={newAddress}
          onChange={e => setNewAddress(e.target.value)} />
        <input className={`${inputCls} max-w-40`} placeholder="Phone" value={newPhone}
          onChange={e => setNewPhone(e.target.value)} />
        <input className={`${inputCls} max-w-44`} placeholder="Chain (e.g. TA / Petro)" value={newChain}
          onChange={e => setNewChain(e.target.value)} list="chain-options" />
        <datalist id="chain-options">
          {chainOptions.map(c => <option key={c} value={c} />)}
        </datalist>
        <button
          onClick={create}
          disabled={!newName.trim() || busy === -1}
          className="px-3 py-1 rounded bg-slate-200 text-slate-900 text-sm font-medium hover:bg-white disabled:opacity-50"
        >
          Add active entry
        </button>
      </div>

      {/* Search + bulk import (scales to hundreds of chain locations) */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input
          className={`${inputCls} max-w-72`}
          placeholder="Search name / chain / address…"
          value={search}
          onChange={e => { setSearch(e.target.value); setShownCount(100); }}
        />
        <button
          onClick={() => setImportOpen(o => !o)}
          className={`px-2.5 py-1 rounded border text-xs ${importOpen
            ? 'bg-slate-200 text-slate-900 border-slate-200 font-medium'
            : 'border-slate-700 text-slate-400 hover:text-slate-200'}`}
        >
          Import…
        </button>
      </div>

      {importOpen && (
        <div className="mb-4 bg-slate-900 border border-slate-800 rounded-lg px-4 py-3">
          <p className="text-xs text-slate-500 mb-2">
            Paste TAB-separated rows (straight from a spreadsheet), one location per line:
            <span className="text-slate-400"> name ⇥ chain ⇥ address ⇥ lat ⇥ lng ⇥ phone ⇥ services</span>.
            Entries are created active + geocoded; matching account vendors link automatically;
            existing names are skipped.
          </p>
          <textarea
            rows={6}
            className={`${inputCls} font-mono text-xs`}
            placeholder={"TA Truck Service #016 – Tuscaloosa, AL\tTA / Petro\t1014 US-82, Tuscaloosa, AL\t33.209\t-87.601\t205-556-5951\tTruck repair, Tires"}
            value={importText}
            onChange={e => setImportText(e.target.value)}
          />
          <div className="mt-2 flex items-center gap-3">
            <button
              onClick={runImport}
              disabled={importBusy || !importText.trim()}
              className="px-3 py-1 rounded bg-slate-200 text-slate-900 text-sm font-medium hover:bg-white disabled:opacity-50"
            >
              {importBusy ? 'Importing…' : 'Import rows'}
            </button>
            {importResult && <span className="text-xs text-slate-400">{importResult}</span>}
          </div>
        </div>
      )}

      {/* Status filter */}
      <div className="mb-3 flex items-center gap-1.5 text-xs">
        {(['all', 'pending', 'active', 'rejected'] as const).map(f => (
          <button key={f} onClick={() => setFilter(f)}
            className={`px-2.5 py-1 rounded-full border capitalize ${
              filter === f
                ? 'bg-slate-200 text-slate-900 border-slate-200 font-medium'
                : 'bg-transparent text-slate-400 border-slate-700 hover:text-slate-200'
            }`}>
            {f}{f === 'pending' && pendingCount > 0 ? ` (${pendingCount})` : ''}
          </button>
        ))}
      </div>

      {loading && entries.length === 0 && (
        <div className="text-slate-500 text-sm py-8 text-center">Loading…</div>
      )}

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[11px] uppercase tracking-wider text-slate-500 border-b border-slate-800">
              <th className="px-4 py-2 font-medium">Shop</th>
              <th className="px-4 py-2 font-medium">Contact</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Source</th>
              <th className="px-4 py-2 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {shown.map(e => (
              <Fragment key={e.id}>
              <tr className="border-b border-slate-800/60 last:border-0 align-top">
                <td className="px-4 py-3">
                  {editing === e.id ? (
                    <div className="flex flex-col gap-1.5">
                      <input className={inputCls} value={String(draft.name ?? e.name)}
                        onChange={ev => setDraft(d => ({ ...d, name: ev.target.value }))} />
                      <input className={inputCls} placeholder="Services (comma-separated)"
                        value={String(draft.services ?? e.services)}
                        onChange={ev => setDraft(d => ({ ...d, services: ev.target.value }))} />
                      <input className={inputCls} placeholder="Chain (e.g. TA / Petro; empty = independent)"
                        value={String(draft.chain ?? e.chain)} list="chain-options"
                        onChange={ev => setDraft(d => ({ ...d, chain: ev.target.value }))} />
                    </div>
                  ) : (
                    <>
                      <div className="text-slate-200 font-medium">
                        {e.name}
                        {e.chain && (
                          <span className="ml-2 inline-block px-1.5 py-0.5 rounded-full border border-slate-600/60 text-slate-400 text-[11px] font-normal align-middle">
                            {e.chain}
                          </span>
                        )}
                      </div>
                      {e.services && <div className="text-xs text-slate-500 mt-0.5">{e.services}</div>}
                    </>
                  )}
                </td>
                <td className="px-4 py-3 text-xs">
                  {editing === e.id ? (
                    <div className="flex flex-col gap-1.5">
                      <input className={inputCls} placeholder="Address" value={String(draft.address ?? e.address)}
                        onChange={ev => setDraft(d => ({ ...d, address: ev.target.value }))} />
                      <input className={inputCls} placeholder="Phone" value={String(draft.phone ?? e.phone)}
                        onChange={ev => setDraft(d => ({ ...d, phone: ev.target.value }))} />
                    </div>
                  ) : (
                    <>
                      <div className="text-slate-300">{e.address || '—'}</div>
                      <div className="text-slate-500 mt-0.5">{e.phone || ''}</div>
                      <div className={`mt-1 inline-flex items-center gap-1 ${e.lat != null ? 'text-emerald-400' : 'text-slate-600'}`}>
                        <MapPin size={12} />
                        {e.lat != null && e.lng != null
                          ? `${e.lat.toFixed(5)}, ${e.lng.toFixed(5)}`
                          : 'No map location'}
                      </div>
                    </>
                  )}
                </td>
                <td className="px-4 py-3 whitespace-nowrap">
                  <span className={`inline-block px-2 py-0.5 rounded-full border text-[11px] font-medium capitalize ${STATUS_BADGE[e.status]}`}>
                    {e.status}
                  </span>
                </td>
                <td className="px-4 py-3 text-xs text-slate-500 whitespace-nowrap">
                  {e.source === 'suggestion'
                    ? `suggestion · acct #${e.suggested_by_account ?? '?'}`
                    : 'operator'}
                </td>
                <td className="px-4 py-3 text-right whitespace-nowrap">
                  <div className="inline-flex items-center gap-1.5 text-xs">
                    {editing === e.id ? (
                      <>
                        <button onClick={() => saveEdit(e.id)} disabled={busy === e.id}
                          className="px-2 py-1 rounded bg-slate-200 text-slate-900 font-medium hover:bg-white disabled:opacity-50">
                          Save
                        </button>
                        <button onClick={() => { setEditing(null); setDraft({}); }}
                          className="px-2 py-1 rounded border border-slate-700 text-slate-400 hover:text-slate-200">
                          Cancel
                        </button>
                      </>
                    ) : (
                      <>
                        {e.status === 'pending' && (
                          <>
                            <button onClick={() => approve(e.id)} disabled={busy === e.id}
                              className="px-2 py-1 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/25 disabled:opacity-50">
                              Approve
                            </button>
                            <button onClick={() => reject(e.id)} disabled={busy === e.id}
                              className="px-2 py-1 rounded border border-slate-700 text-slate-400 hover:text-slate-200 disabled:opacity-50">
                              Reject
                            </button>
                          </>
                        )}
                        {e.status === 'rejected' && (
                          <button onClick={() => approve(e.id)} disabled={busy === e.id}
                            className="px-2 py-1 rounded border border-slate-700 text-slate-400 hover:text-slate-200 disabled:opacity-50">
                            Re-approve
                          </button>
                        )}
                        <button onClick={() => { setEditing(e.id); setDraft({}); }}
                          className="px-2 py-1 rounded border border-slate-700 text-slate-400 hover:text-slate-200">
                          Edit
                        </button>
                        <button onClick={() => (geoOpen === e.id ? closeGeo() : openGeo(e))}
                          className={`px-2 py-1 rounded border inline-flex items-center gap-1 ${
                            geoOpen === e.id
                              ? 'bg-slate-200 text-slate-900 border-slate-200 font-medium'
                              : 'border-slate-700 text-slate-400 hover:text-slate-200'
                          }`}>
                          <MapPin size={12} /> Location
                        </button>
                      </>
                    )}
                  </div>
                </td>
              </tr>
              {geoOpen === e.id && (
                <tr className="border-b border-slate-800/60 last:border-0 bg-slate-950/50">
                  <td colSpan={5} className="px-4 py-3">
                    <div className="flex flex-wrap gap-4">
                      <div className="flex-1 min-w-72 max-w-xl">
                        {/* Geocode: server proxies Nominatim; results are
                            suggestions only — Save is the confirm step. */}
                        <div className="flex items-center gap-2">
                          <input className={inputCls} placeholder="Address to geocode"
                            value={geoQuery} onChange={ev => setGeoQuery(ev.target.value)}
                            onKeyDown={ev => { if (ev.key === 'Enter') geocode(); }} />
                          <button onClick={geocode} disabled={geoBusy || geoQuery.trim().length < 3}
                            className="px-2.5 py-1 rounded bg-slate-200 text-slate-900 text-xs font-medium hover:bg-white disabled:opacity-50 whitespace-nowrap">
                            {geoBusy ? 'Searching…' : 'Find coordinates'}
                          </button>
                        </div>
                        {geoErr && <div className="mt-2 text-xs text-amber-400">{geoErr}</div>}
                        {geoSug.length > 0 && (
                          <ul className="mt-2 flex flex-col gap-1">
                            {geoSug.map((s, i) => (
                              <li key={i} className="flex items-start gap-2 text-xs">
                                <button
                                  onClick={() => { setGeoLat(String(s.lat)); setGeoLng(String(s.lng)); }}
                                  className="shrink-0 px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/25">
                                  Use
                                </button>
                                <span className="text-slate-400 pt-0.5">{s.label}</span>
                              </li>
                            ))}
                          </ul>
                        )}
                        <div className="mt-3 flex items-center gap-2">
                          <input className={`${inputCls} max-w-36`} placeholder="Latitude"
                            value={geoLat} onChange={ev => setGeoLat(ev.target.value)} />
                          <input className={`${inputCls} max-w-36`} placeholder="Longitude"
                            value={geoLng} onChange={ev => setGeoLng(ev.target.value)} />
                          <button onClick={() => saveGeo(e.id)}
                            disabled={busy === e.id || (!geoValid && !geoEmpty)}
                            className="px-2.5 py-1 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 text-xs hover:bg-emerald-500/25 disabled:opacity-50">
                            {geoEmpty ? 'Clear location' : 'Save location'}
                          </button>
                          <button onClick={closeGeo}
                            className="px-2.5 py-1 rounded border border-slate-700 text-slate-400 text-xs hover:text-slate-200">
                            Close
                          </button>
                        </div>
                      </div>
                      {geoValid && (
                        <div className="w-80 shrink-0">
                          {/* Zero-dependency pin preview: OSM's embed page
                              with a marker at the chosen point. */}
                          <iframe
                            title="Pin preview"
                            className="w-full h-52 rounded border border-slate-800"
                            src={`https://www.openstreetmap.org/export/embed.html?bbox=${
                              `${(lngNum! - 0.008).toFixed(5)}%2C${(latNum! - 0.004).toFixed(5)}%2C${(lngNum! + 0.008).toFixed(5)}%2C${(latNum! + 0.004).toFixed(5)}`
                            }&layer=mapnik&marker=${latNum!.toFixed(6)}%2C${lngNum!.toFixed(6)}`}
                          />
                          <a
                            className="mt-1 inline-block text-[11px] text-slate-400 underline hover:text-slate-200"
                            href={`https://www.openstreetmap.org/?mlat=${latNum!.toFixed(6)}&mlon=${lngNum!.toFixed(6)}#map=16/${latNum!.toFixed(6)}/${lngNum!.toFixed(6)}`}
                            target="_blank" rel="noreferrer">
                            Verify on OpenStreetMap ↗
                          </a>
                        </div>
                      )}
                    </div>
                  </td>
                </tr>
              )}
              </Fragment>
            ))}
            {shown.length === 0 && !loading && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-600 text-sm">
                No entries{filter !== 'all' ? ` with status “${filter}”` : ' yet'}.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
      {filtered.length > shownCount && (
        <div className="mt-2 text-center">
          <button
            onClick={() => setShownCount(c => c + 200)}
            className="px-3 py-1 rounded border border-slate-700 text-slate-400 text-xs hover:text-slate-200"
          >
            Show more ({filtered.length - shownCount} hidden)
          </button>
        </div>
      )}

      {/* Review moderation queue — anonymous stars/comments submitted
          by accounts with verified usage.  The account id shown is
          operator audit only; approved reviews display publicly with
          NO attribution. */}
      <h2 className="text-sm font-semibold text-slate-200 mt-8 mb-2">
        Pending reviews{reviews.length > 0 ? ` (${reviews.length})` : ''}
      </h2>
      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
        {reviews.length === 0 ? (
          <p className="px-4 py-5 text-sm text-slate-600 text-center">No reviews awaiting moderation.</p>
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {reviews.map(r => (
                <tr key={r.id} className="border-b border-slate-800/60 last:border-0 align-top">
                  <td className="px-4 py-3">
                    <div className="text-slate-200 font-medium">{r.entry_name}</div>
                    <div className="text-xs text-slate-500 mt-0.5">
                      {'★'.repeat(r.rating)}{'☆'.repeat(5 - r.rating)} · acct #{r.account_id}
                    </div>
                    {r.comment && <div className="text-xs text-slate-400 mt-1">{r.comment}</div>}
                  </td>
                  <td className="px-4 py-3 text-right whitespace-nowrap">
                    <div className="inline-flex items-center gap-1.5 text-xs">
                      <button onClick={() => reviewAct(r.id, 'approve')} disabled={busy === r.id}
                        className="px-2 py-1 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/25 disabled:opacity-50">
                        Approve
                      </button>
                      <button onClick={() => reviewAct(r.id, 'reject')} disabled={busy === r.id}
                        className="px-2 py-1 rounded border border-slate-700 text-slate-400 hover:text-slate-200 disabled:opacity-50">
                        Reject
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
