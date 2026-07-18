import { useCallback, useEffect, useState } from 'react';
import { apiJSON } from '../api/client';

// Market intelligence launch cockpit — the switch that used to be an
// .env ritual, plus the readiness numbers and a preview trigger.
// Rollup cells stay INVISIBLE to customers while the switch is off,
// so "Compute now" is always safe to press.

interface Status {
  enabled: boolean;
  switch: boolean;
  env_override: boolean;
  sharing_accounts: number;
  vendor_cells: number;
  part_geo_cells: number;
  computed_at: string | null;
}

const btnCls =
  'px-3 py-1.5 rounded text-sm font-medium border transition disabled:opacity-50';

export default function MarketIntelPage() {
  const [st, setSt] = useState<Status | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState('');
  const [runMsg, setRunMsg] = useState('');

  const load = useCallback(async () => {
    try {
      setSt(await apiJSON<Status>('/system/market-intel'));
      setErr('');
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Load failed');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const flip = async (enabled: boolean) => {
    if (enabled && !window.confirm(
      'Turn market intelligence ON for every account?\n\n'
      + 'This publishes anonymized price ranges (3+ companies per cell, '
      + 'p25–p75, 12-month window) to sharing accounts. Flip only after '
      + 'the legal review of aggregated-pricing display.',
    )) return;
    setBusy('flip');
    try {
      await apiJSON('/system/market-intel', {
        method: 'PUT', body: JSON.stringify({ enabled }),
      });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Switch failed');
    } finally {
      setBusy('');
    }
  };

  const runNow = async () => {
    setBusy('run');
    setRunMsg('');
    try {
      const r = await apiJSON<{ vendor_cells: number; part_geo_cells: number }>(
        '/system/market-intel/rollup-now', { method: 'POST' },
      );
      setRunMsg(`Computed: ${r.vendor_cells} vendor cells · ${r.part_geo_cells} part-geo cells`);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Rollup failed');
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <h1 className="text-xl font-semibold text-slate-100 mb-1">Market Intelligence</h1>
      <p className="text-sm text-slate-500 mb-5">
        Anonymized price ranges computed nightly (04:40 UTC) from sharing accounts'
        invoices. This page is the launch switch — no server access needed.
      </p>

      {err && <div className="mb-4 text-sm text-rose-400 border border-rose-500/30 bg-rose-500/10 rounded px-3 py-2">{err}</div>}
      {!st ? <p className="text-slate-500 text-sm">Loading…</p> : (
        <>
          {/* ── The switch ── */}
          <div className={`mb-5 rounded-lg border p-4 flex flex-wrap items-center gap-4 ${
            st.enabled ? 'border-emerald-500/40 bg-emerald-500/5' : 'border-slate-800 bg-slate-900/40'
          }`}>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-slate-200">
                {st.enabled ? 'LIVE — ranges visible to sharing accounts' : 'DARK — nothing visible to any customer'}
              </p>
              <p className="text-xs text-slate-500 mt-0.5">
                {st.env_override
                  ? 'Forced ON by the MARKET_INTEL_ENABLED env var (emergency override) — the switch below has no effect until it is removed from .env.'
                  : 'Flip propagates to every API worker within 60 seconds. No restart.'}
              </p>
            </div>
            {st.enabled ? (
              <button className={`${btnCls} border-slate-700 text-slate-300 hover:bg-slate-800`}
                disabled={busy === 'flip' || st.env_override} onClick={() => flip(false)}>
                Turn off
              </button>
            ) : (
              <button className={`${btnCls} border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/10`}
                disabled={busy === 'flip'} onClick={() => flip(true)}>
                Turn on…
              </button>
            )}
          </div>

          {/* ── Readiness ── */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
            {[
              { label: 'Sharing accounts', value: st.sharing_accounts, hint: 'give-to-get opt-ins' },
              { label: 'Vendor cells', value: st.vendor_cells, hint: 'shop × part/task ranges' },
              { label: 'Part-geo cells', value: st.part_geo_cells, hint: 'part × state/national' },
              { label: 'Last computed', value: st.computed_at ? st.computed_at.slice(0, 16).replace('T', ' ') : '—', hint: 'UTC' },
            ].map((s) => (
              <div key={s.label} className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
                <p className="text-xs text-slate-500">{s.label}</p>
                <p className="text-lg font-semibold text-slate-200 tabular-nums">{String(s.value)}</p>
                <p className="text-xs text-slate-600">{s.hint}</p>
              </div>
            ))}
          </div>

          <div className="mb-6 flex items-center gap-3">
            <button className={`${btnCls} border-slate-700 text-slate-300 hover:bg-slate-800`}
              disabled={busy === 'run'} onClick={runNow}>
              {busy === 'run' ? 'Computing…' : 'Compute rollups now'}
            </button>
            <span className="text-xs text-slate-500">
              {runMsg || 'Safe while dark — cells stay invisible until the switch is on.'}
            </span>
          </div>

          {/* ── How the chain works (operator reference) ── */}
          <div className="rounded-lg border border-slate-800 p-4 text-sm text-slate-400 leading-relaxed">
            <p className="text-slate-300 font-medium mb-2">How the chain works</p>
            <ol className="list-decimal ml-5 space-y-1.5">
              <li><span className="text-slate-300">Invoice truth</span> — a company saves a work order: vendor, part lines, unit costs.</li>
              <li><span className="text-slate-300">Identity resolution</span> — the vendor auto-links to a Vendor Directory entry; the part auto-links to a Parts Catalog entry (your curation on this console is what makes name variants pool correctly).</li>
              <li><span className="text-slate-300">Consent filter</span> — only accounts with the give-to-get toggle ON contribute points (and only they may see ranges).</li>
              <li><span className="text-slate-300">Nightly rollup (04:40 UTC)</span> — two kinds of cells: <span className="text-slate-300">(shop × part/task)</span> for vendor profiles ("is this quote fair?") and <span className="text-slate-300">(part × state/national)</span> for Catalog estimates ("what should this cost around me?"). State comes from the shop's curated address.</li>
              <li><span className="text-slate-300">Privacy gate</span> — a cell publishes ONLY with 3+ distinct companies; ranges are p25–p75 over a 12-month window; no names, no invoices, nothing traceable.</li>
              <li><span className="text-slate-300">Display</span> — sharing accounts see ranges on directory shop profiles and on Catalog parts; non-sharing accounts see only "N ranges available — enable sharing".</li>
            </ol>
            <p className="mt-3 text-xs text-slate-500">
              City-level estimates are deliberately deferred: 3+ sharing companies per city per part
              is a high bar — state cells light up first, city can follow when density allows.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
