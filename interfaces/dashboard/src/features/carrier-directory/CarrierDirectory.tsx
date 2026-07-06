// Carrier Knowledge Base — list of the external carriers the recruiting team
// works with.  Read for every recruiter; a recruiting MANAGER (recruiter +
// is_manager, i.e. can_manage_carrier_directory) can add a carrier — which
// opens its profile to fill in.  Info-only (v1) — no links to the apply flow
// or any other feature.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, ChevronRight, Building2 } from 'lucide-react';
import { toast } from 'sonner';
import { apiJSON } from '../../api/client';
import PageHeader from '../../components/shell/PageHeader';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import DataTable from '../../components/DataTable';
import type { AnyColumn } from '../../types';
import { useRoleView } from '../../context/RoleViewContext';
import { toneClasses } from '../../lib/status';

interface CarrierRow {
  id: number; name: string; website: string; experience_summary: string;
  intake_review_pending?: number | boolean;
}

export default function CarrierDirectory() {
  const { viewHasAny } = useRoleView();
  const canEdit = viewHasAny('can_manage_carrier_directory');
  const nav = useNavigate();
  const [rows, setRows] = useState<CarrierRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiJSON<{ items: CarrierRow[] }>('/carrier-directory/carriers');
      setRows(r.items || []);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load carriers');
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const createCarrier = async () => {
    const name = newName.trim();
    if (!name) return;
    setBusy(true);
    try {
      const created = await apiJSON<{ id: number }>('/carrier-directory/carriers', {
        method: 'POST', body: { name },
      });
      toast.success('Carrier added');
      nav(`/workforce/carrier-directory/${created.id}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not add carrier');
    } finally {
      setBusy(false);
    }
  };

  const columns: AnyColumn[] = useMemo(() => [
    {
      key: 'name', label: 'Carrier', sortable: true, filterable: true,
      render: (v: unknown, row: Record<string, unknown>) => (
        <span className="inline-flex items-center gap-2">
          <span className="font-medium text-foreground">{String(v || '')}</span>
          {Boolean(row.intake_review_pending) && (
            <span className={`rounded-full border px-2 py-0.5 text-2xs font-medium ${toneClasses('info')}`}>
              Carrier updated
            </span>
          )}
        </span>
      ),
    },
    {
      key: 'experience_summary', label: 'Accepted Experience Levels',
      render: (v: unknown) => (
        <span className="text-muted-foreground">{v ? String(v) : '—'}</span>
      ),
    },
    {
      key: 'id', label: '', sortable: false,
      render: () => <ChevronRight size={16} className="text-muted-foreground" />,
    },
  ], []);

  return (
    <div>
      <PageHeader
        title="Carrier Directory"
        icon={Building2}
        description="Reference info for the external carriers we recruit for — pre-qual criteria, presentation details and process notes."
        actions={canEdit && (adding ? (
          <div className="flex items-center gap-2">
            <Input autoFocus placeholder="Carrier name" value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') createCarrier();
                if (e.key === 'Escape') { setAdding(false); setNewName(''); }
              }} />
            <Button size="sm" onClick={createCarrier} disabled={busy || !newName.trim()}>Add</Button>
            <Button size="sm" variant="ghost" onClick={() => { setAdding(false); setNewName(''); }}>Cancel</Button>
          </div>
        ) : (
          <Button size="sm" onClick={() => setAdding(true)}><Plus size={16} /> Add carrier</Button>
        ))}
      />
      {loading ? (
        <p className="text-sm text-muted-foreground">Loading carriers…</p>
      ) : (
        <DataTable
          columns={columns}
          data={rows as unknown as Record<string, unknown>[]}
          searchKey={['name', 'experience_summary']}
          searchPlaceholder="Search carriers…"
          tableId="carrier-directory"
          onRowClick={(row) => nav(`/workforce/carrier-directory/${(row as unknown as CarrierRow).id}`)}
        />
      )}
    </div>
  );
}
