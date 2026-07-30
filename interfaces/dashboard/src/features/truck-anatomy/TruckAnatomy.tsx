/**
 * Truck Anatomy — the taxonomy as a walkable machine.
 *
 * The left panel IS the classification language (18 systems, 112
 * assemblies, live from the same endpoints Part Detail's dropdown
 * reads — the 3D view can never disagree with the product).  The
 * scene renders one node per assembly: real GLB when one exists under
 * /models/truck-anatomy/<key>.glb, labeled placeholder otherwise.
 *
 * DARK by permission (owner decision): gated on can_truck_anatomy,
 * seeded to NOBODY — the owner included — so nothing is marketed
 * before it's ready, anywhere.  The owner self-grants in the
 * Permissions matrix (the flag is deliberately not owner-protected);
 * the nav entry appears only for holders.  The whole feature lives in
 * THIS folder — delete it plus one route line and it's gone (owner
 * requirement: remove as a unit).
 */
import { Suspense, lazy, useMemo, useState } from 'react';
import { ChevronRight, RotateCcw, Search, Truck } from 'lucide-react';
import { PageHeader } from '../../components/shell';
import { Button } from '../../components/ui/button';
import { Switch } from '../../components/ui/switch';
import { Input } from '../../components/ui/input';
import { useAssemblies } from '../parts/useAssemblies';
import type { SceneSelection } from './Scene';

// The 3D chunk (three.js + r3f) loads only when this page opens.
const Scene = lazy(() => import('./Scene'));

export default function TruckAnatomy() {
  const { all, groups, labelOf } = useAssemblies();
  const [selection, setSelection] = useState<SceneSelection>({
    systemKey: null, assemblyKey: null,
  });
  const [explode, setExplode] = useState(false);
  const [query, setQuery] = useState('');

  const systemsOrder = useMemo(() => groups.map((g) => g.systemKey), [groups]);
  const systemLabels = useMemo(() => {
    const m = new Map<string, string>();
    for (const g of groups) m.set(g.systemKey, g.systemLabel);
    return m;
  }, [groups]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return null;
    return new Set(
      all.filter((a) => a.label.toLowerCase().includes(q)).map((a) => a.key),
    );
  }, [query, all]);

  const selectedAssembly = selection.assemblyKey
    ? all.find((a) => a.key === selection.assemblyKey) ?? null
    : null;

  const pick = (assemblyKey: string | null) => {
    if (!assemblyKey) {
      setSelection((s) => {
        const next = s.assemblyKey
          ? { ...s, assemblyKey: null }
          : { systemKey: null, assemblyKey: null };
        // Explode is a per-system posture: it must not stay latched
        // behind a disabled switch and detonate the NEXT selection.
        if (!next.systemKey) setExplode(false);
        return next;
      });
      return;
    }
    const a = all.find((x) => x.key === assemblyKey);
    setSelection({ systemKey: a?.system_key ?? null, assemblyKey });
  };

  return (
    <div className="h-full flex flex-col min-h-0">
      <PageHeader
        icon={Truck}
        title="Truck Anatomy"
        description="The whole rig as a walkable model, organized the way the product already thinks: System → Assembly → Part. Grey boxes are placeholders — each becomes a real 3D part as its model lands, one assembly at a time."
        actions={(
          <div className="flex items-center gap-3">
            <label className="inline-flex items-center gap-2 text-sm text-muted-foreground">
              <Switch
                size="sm"
                checked={explode}
                onCheckedChange={setExplode}
                disabled={!selection.systemKey}
                aria-label="Explode the selected system"
              />
              Explode
            </label>
            <Button
              size="sm" variant="outline"
              onClick={() => { setSelection({ systemKey: null, assemblyKey: null }); setExplode(false); setQuery(''); }}
            >
              <RotateCcw size={14} /> Reset view
            </Button>
          </div>
        )}
      />

      <div className="flex-1 min-h-0 flex gap-4">
        {/* ── The taxonomy panel — the same language, browsable ── */}
        <aside className="w-80 shrink-0 flex flex-col min-h-0 bg-card border border-border rounded-lg overflow-hidden">
          <div className="p-2 border-b border-border">
            <div className="relative">
              <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search assemblies"
                className="h-8 pl-7 text-xs"
                aria-label="Search assemblies"
              />
            </div>
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto p-1">
            {groups.map((g) => {
              const visible = matches
                ? g.items.filter((a) => matches.has(a.key))
                : g.items;
              if (matches && visible.length === 0) return null;
              const active = selection.systemKey === g.systemKey;
              return (
                <div key={g.systemKey} className="mb-0.5">
                  <button
                    type="button"
                    onClick={() => {
                      const clearing = active && !selection.assemblyKey;
                      if (clearing) setExplode(false);
                      setSelection(clearing
                        ? { systemKey: null, assemblyKey: null }
                        : { systemKey: g.systemKey, assemblyKey: null });
                    }}
                    className={`w-full flex items-center justify-between gap-2 px-2 py-1.5 rounded text-xs font-medium uppercase tracking-wide transition-colors ${
                      active ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'
                    }`}
                  >
                    {g.systemLabel}
                    <span className="tabular-nums font-normal normal-case tracking-normal text-2xs">
                      {visible.length}
                    </span>
                  </button>
                  {(active || matches) && visible.map((a) => (
                    <button
                      key={a.key}
                      type="button"
                      onClick={() => pick(a.key)}
                      className={`w-full flex items-center gap-1.5 pl-5 pr-2 py-1 rounded text-sm text-left transition-colors ${
                        selection.assemblyKey === a.key
                          ? 'bg-primary/10 text-primary font-medium'
                          : 'text-foreground hover:bg-muted/60'
                      }`}
                    >
                      <ChevronRight size={12} className="shrink-0 text-muted-foreground/60" />
                      <span className="min-w-0 truncate">{a.label}</span>
                    </button>
                  ))}
                </div>
              );
            })}
          </div>
        </aside>

        {/* ── The stage ── */}
        <div className="relative flex-1 min-h-0 bg-card border border-border rounded-lg overflow-hidden">
          <Suspense fallback={(
            <div className="h-full flex items-center justify-center text-sm text-muted-foreground">
              Loading the rig…
            </div>
          )}>
            <Scene
              assemblies={all}
              systemsOrder={systemsOrder}
              systemLabels={systemLabels}
              selection={selection}
              explode={explode}
              matches={matches}
              onPick={pick}
            />
          </Suspense>

          {/* Info card — the future data-layer socket. */}
          {selectedAssembly && (
            <div className="absolute bottom-3 right-3 z-20 w-72 bg-card/95 border border-border rounded-lg p-3 shadow-lg">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {systemLabels.get(selectedAssembly.system_key) ?? selectedAssembly.system_key}
              </p>
              <p className="text-base font-semibold text-foreground">
                {labelOf.get(selectedAssembly.key) ?? selectedAssembly.key}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Model: placeholder until{' '}
                <span className="font-mono text-2xs">{selectedAssembly.key}.glb</span>{' '}
                lands. This card is the socket where real fleet data
                (spend, failures, intervals) plugs in later.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
