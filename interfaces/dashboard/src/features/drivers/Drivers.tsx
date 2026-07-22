import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  IdCard, X, Truck, Upload, FileText, Trash2, Download, Save,
  AlertTriangle, Calendar, Plus, ClipboardCheck, GraduationCap, Clock,
  Check, Search, Link2, Link2Off, UserPlus, Copy,
} from 'lucide-react';
import { apiJSON, apiFetch } from '../../api/client';
import { buildSignupInviteUrl, useSignupBase } from '../../lib/inviteUrl';
import { Button } from '../../components/ui/button';
import { InfoTip } from '../../components/tooltip';
import { toneClasses, toneText } from '../../lib/status';
import DataGrid from '../../components/datagrid';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import type {
  DriverProfile,
  DriverDetail,
  DriverVehicleAssignment,
  DriverDocument,
  SamsaraDriver,
  SamsaraDriversResponse,
  AnyColumn,
} from '../../types';
import { useShellConfig } from '../../hooks/useShellConfig';
import { useRoleView } from '../../context/RoleViewContext';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '../../components/ui/dialog';
import {
  tabsForPersona, showsExpiringBanner, type DriverDetailTab,
} from '../../features/drivers/personaConfig';

const DOC_TYPES: Array<{ key: string; label: string }> = [
  { key: 'cdl',              label: 'CDL' },
  { key: 'medical_card',     label: 'Medical Card' },
  { key: 'mvr',              label: 'MVR' },
  { key: 'drug_screen',      label: 'Drug Screen' },
  { key: 'background_check', label: 'Background Check' },
  { key: 'training_cert',    label: 'Training Cert' },
  { key: 'other',            label: 'Other' },
];

const DOC_LABEL: Record<string, string> = Object.fromEntries(
  DOC_TYPES.map((d) => [d.key, d.label]),
);

const DOC_TYPE_ITEMS = DOC_TYPES.map((d) => ({ value: d.key, label: d.label }));

const CDL_CLASS_ITEMS = [
  { value: '', label: '—' },
  { value: 'A', label: 'A' },
  { value: 'B', label: 'B' },
  { value: 'C', label: 'C' },
];

// Drawer tabs are persona-composed (Shared-tier feature): the available
// set per role lives in features/drivers/personaConfig.ts.
type DetailTab = DriverDetailTab;

function daysUntil(iso: string | null): number | null {
  if (!iso) return null;
  const target = new Date(iso + 'T00:00:00');
  if (isNaN(target.getTime())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - today.getTime()) / 86_400_000);
}

function ExpirationChip({ iso }: { iso: string | null }) {
  if (!iso) return <span className="text-muted-foreground text-xs">—</span>;
  const d = daysUntil(iso);
  if (d == null) return <span className="text-muted-foreground text-xs">{iso}</span>;
  let cls = 'bg-muted text-muted-foreground border-transparent';
  if (d <= 30) cls = toneClasses('danger');
  else if (d <= 90) cls = toneClasses('warn');
  return (
    <span className={`px-2 py-0.5 rounded-md text-2xs border ${cls} tabular-nums`}>
      {iso} {d < 0 ? `(${-d}d ago)` : `(${d}d)`}
    </span>
  );
}

function StorageBadge({ driveId }: { driveId: string | null }) {
  if (driveId) {
    return (
      <span className="px-1.5 py-0.5 rounded text-3xs bg-primary/10 text-primary border border-primary/30">
        Drive
      </span>
    );
  }
  return (
    <span className="px-1.5 py-0.5 rounded text-3xs bg-muted text-muted-foreground border border-border">
      Local
    </span>
  );
}

const driverColumns: AnyColumn[] = [
  {
    key: 'display_name',
    label: 'Name',
    sortable: true,
    render: (_v, row) => {
      const p = row as unknown as DriverProfile;
      const parts = p.display_name.trim().split(/\s+/);
      const ini = parts.length >= 2
        ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
        : (parts[0]?.[0] || '?').toUpperCase();
      const terminated = !!p.termination_date;
      return (
        <div className="flex items-center gap-2">
          <div className={`w-7 h-7 rounded-full flex items-center justify-center text-3xs font-bold ${
            terminated ? 'bg-muted text-muted-foreground' : 'bg-primary/15 text-primary'
          }`}>{ini}</div>
          <span>{p.display_name}</span>
        </div>
      );
    },
  },
  { key: 'cdl_number', label: 'CDL #', render: (v) => v ? <span className="tabular-nums">{String(v)}</span> : <span className="text-muted-foreground text-xs">—</span> },
  { key: 'cdl_expires', label: 'CDL Exp.', sortable: true,
    filterable: true, filterMode: 'date-range',
    render: (v) => <ExpirationChip iso={(v as string) ?? null} /> },
  { key: 'med_card_expires', label: 'Med. Card', sortable: true,
    filterable: true, filterMode: 'date-range',
    render: (v) => <ExpirationChip iso={(v as string) ?? null} /> },
  {
    key: 'phone',
    label: 'Phone',
    render: (v) => v ? <span className="text-xs tabular-nums">{String(v)}</span> : <span className="text-muted-foreground text-xs">—</span>,
  },
  {
    key: 'termination_date',
    label: 'Status',
    sortable: true,
    // Filter on the DERIVED Active/Terminated state, not the raw
    // date — that's the distinction operators actually narrow by.
    filterable: true,
    filterValue: (row) => (row as unknown as DriverProfile).termination_date ? 'terminated' : 'active',
    filterLabel: (row) => (row as unknown as DriverProfile).termination_date ? 'Terminated' : 'Active',
    render: (v) =>
      v
        ? <span className="px-2 py-0.5 rounded-md text-xs bg-muted text-muted-foreground border border-border">Terminated</span>
        : <span className={`px-2 py-0.5 rounded-md text-xs border ${toneClasses('ok')}`}>Active</span>,
  },
];

/** Invite a new driver — role hard-locked to Driver server-side, optional
 *  truck bound at claim time.  Gated on can_manage_drivers (the roster admin
 *  right — NOT the staff-wide can_invite).  Returns a link/code the manager
 *  hands to the driver; it lands in the shared Invites table (revocable). */
function InviteDriverButton({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [truck, setTruck] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [result, setResult] = useState<{ code: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const signupBase = useSignupBase();

  const joinUrl = result ? buildSignupInviteUrl(result.code, signupBase) : '';

  const reset = () => { setTruck(''); setErr(''); setResult(null); setCopied(false); };

  const submit = async () => {
    setBusy(true); setErr('');
    try {
      const r = await apiJSON<{ code: string }>('/admin/drivers/invite', {
        method: 'POST',
        body: { truck_num: truck.trim() || null },
      });
      setResult(r);
      onCreated();
    } catch (e) { setErr(e instanceof Error ? e.message : 'Failed'); }
    finally { setBusy(false); }
  };

  return (
    <>
      <Button onClick={() => { reset(); setOpen(true); }}>
        <UserPlus size={16} /> Invite driver
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Invite a driver</DialogTitle>
          </DialogHeader>
          {result ? (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Share this link with the driver. It's also in the Invites list, where you can revoke it.
              </p>
              <div className="flex items-center gap-2">
                <input
                  readOnly value={joinUrl}
                  className="flex-1 px-3 py-2 rounded-lg border border-border bg-muted/40 text-foreground text-xs font-mono"
                />
                <Button
                  variant="outline"
                  onClick={() => { navigator.clipboard?.writeText(joinUrl); setCopied(true); }}
                >
                  {copied ? <Check size={14} className="text-ok" /> : <Copy size={14} />}
                  {copied ? 'Copied' : 'Copy'}
                </Button>
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Creates a driver-only invite. Optionally assign a truck now — it binds when they sign in.
              </p>
              <label className="block">
                <span className="text-xs font-medium text-muted-foreground">Truck number (optional)</span>
                <input
                  value={truck} onChange={(e) => setTruck(e.target.value)}
                  placeholder="e.g. 107"
                  className="mt-1 w-full px-3 py-2 rounded-lg border border-border bg-background text-foreground text-sm"
                />
              </label>
              {err && <p className={`text-sm rounded-md px-3 py-2 ${toneClasses('danger')}`}>{err}</p>}
            </div>
          )}
          <DialogFooter>
            {result ? (
              <Button onClick={() => setOpen(false)}>Done</Button>
            ) : (
              <>
                <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
                <Button onClick={submit} disabled={busy}>
                  {busy ? 'Creating…' : 'Create invite'}
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

export default function Drivers() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { viewHas } = useRoleView();
  const canManageDrivers = viewHas('can_manage_drivers');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>('profile');
  const [includeTerminated, setIncludeTerminated] = useState(false);
  // Persona composition: resolve the active persona's tab set once at
  // the page wrapper; the drawer + tab bodies never read persona state.
  const { persona } = useShellConfig();
  // The Integrations tab (per-driver Samsara/TMS links) is additionally
  // gated on the driver-roster permission — hide it for anyone who can't act.
  const visibleTabs = useMemo(
    () => tabsForPersona(persona).filter((tb) => tb !== 'integrations' || canManageDrivers),
    [persona, canManageDrivers],
  );
  useEffect(() => {
    // Live persona switch (View-As) can strand the open tab on one the
    // new persona doesn't have — snap back to its first tab.
    if (!visibleTabs.includes(detailTab)) setDetailTab(visibleTabs[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona]);

  const { data: listData, isLoading, error: listError } = useQuery({
    queryKey: ['drivers', includeTerminated],
    queryFn: () => apiJSON<{ drivers: DriverProfile[]; count: number }>(
      `/drivers?include_terminated=${includeTerminated}`,
    ),
  });
  const drivers = useMemo(() => listData?.drivers ?? [], [listData]);

  useEffect(() => {
    if (listError) setError(listError instanceof Error ? listError.message : 'Failed');
  }, [listError]);

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ['driver-detail', selectedId],
    queryFn: () => apiJSON<DriverDetail>(`/drivers/${selectedId}`),
    enabled: selectedId != null,
  });

  const refetchDetail = useCallback(
    () => qc.invalidateQueries({ queryKey: ['driver-detail', selectedId] }),
    [qc, selectedId],
  );
  const refetchList = useCallback(
    () => qc.invalidateQueries({ queryKey: ['drivers'] }),
    [qc],
  );

  useEffect(() => {
    if (!success) return;
    const tid = setTimeout(() => setSuccess(''), 2500);
    return () => clearTimeout(tid);
  }, [success]);

  // Cross-fleet expiring docs (for the banner) — only fetched for
  // personas that action documents (see personaConfig).
  const { data: expiringData } = useQuery({
    queryKey: ['drivers-expiring'],
    queryFn: () => apiJSON<{ documents: DriverDocument[]; count: number }>(
      '/drivers/documents/expiring?within_days=30',
    ),
    enabled: showsExpiringBanner(persona),
  });
  const expiringCount = showsExpiringBanner(persona) ? (expiringData?.count ?? 0) : 0;

  const close = () => { setSelectedId(null); setDetailTab('profile'); };

  return (
    <div>
      <PageHeader
        icon={IdCard}
        title={t('pages.drivers_title', 'Drivers')}
        description={t('pages.drivers_desc', 'Profiles, vehicle assignments, and documents (CDL, medical card, training)')}
        meta={
          <span className="text-sm text-muted-foreground">
            {drivers.filter((d) => !d.termination_date).length} active / {drivers.length} total
          </span>
        }
      />

      {error && <div className="mb-3"><ErrorState message={error} /></div>}
      {success && <p className="text-ok text-sm mb-3">{success}</p>}

      {expiringCount > 0 && (
        <div className={`mb-4 p-3 rounded-lg border flex items-center gap-2 text-sm ${toneClasses('warn')}`}>
          <AlertTriangle size={16} className={`shrink-0 ${toneText('warn')}`} />
          <span>
            <strong>{expiringCount}</strong> document{expiringCount === 1 ? '' : 's'} expiring in the next 30 days across the fleet.
          </span>
        </div>
      )}

      <div className="flex items-center gap-3 mb-4">
        <label className="inline-flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
          <input
            type="checkbox"
            checked={includeTerminated}
            onChange={(e) => setIncludeTerminated(e.target.checked)}
            className="accent-primary"
          />
          Include terminated
        </label>
        {canManageDrivers && (
          <div className="ml-auto">
            <InviteDriverButton onCreated={() => { setSuccess('Driver invite created'); refetchList(); }} />
          </div>
        )}
      </div>

      {isLoading && drivers.length === 0 ? (
        <TableSkeleton rows={6} cols={6} />
      ) : drivers.length === 0 ? (
        <EmptyState
          icon={IdCard}
          title="No drivers yet"
          description={canManageDrivers
            ? 'Use "Invite driver" above to onboard your first driver.'
            : 'Drivers appear here once they\'re invited and sign in.'}
        />
      ) : (
        <DataGrid
          tableId="drivers"
          columns={driverColumns}
          data={drivers as unknown as Record<string, unknown>[]}
          searchKey={['display_name', 'cdl_number', 'phone']}
          onRowClick={(row) => setSelectedId((row as unknown as DriverProfile).user_id)}
        />
      )}

      {selectedId != null && (
        <div className="fixed inset-0 bg-black/60 z-50 flex justify-end" onClick={close}>
          <div className="w-full max-w-lg bg-card border-l border-border overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            {detailLoading && !detail ? (
              <div className="p-6"><TableSkeleton rows={6} cols={2} /></div>
            ) : detail ? (
              <DriverDrawer
                detail={detail}
                tab={detailTab}
                tabs={visibleTabs}
                onTab={setDetailTab}
                onClose={close}
                onSaved={() => { refetchDetail(); refetchList(); setSuccess('Saved'); }}
                onError={(m) => setError(m)}
              />
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

function DriverDrawer({
  detail, tab, tabs, onTab, onClose, onSaved, onError,
}: {
  detail: DriverDetail;
  tab: DetailTab;
  tabs: DetailTab[];
  onTab: (t: DetailTab) => void;
  onClose: () => void;
  onSaved: () => void;
  onError: (m: string) => void;
}) {
  const p = detail.profile;
  const initials = (() => {
    const parts = p.display_name.trim().split(/\s+/);
    return parts.length >= 2
      ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
      : (parts[0]?.[0] || '?').toUpperCase();
  })();

  return (
    <>
      <div className="p-6 pb-4 border-b border-border">
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className={`w-12 h-12 rounded-full flex items-center justify-center font-bold text-base ${
              p.termination_date ? 'bg-muted text-muted-foreground' : 'bg-primary/15 text-primary'
            }`}>{initials}</div>
            <div>
              <h2 className="text-lg font-semibold">{p.display_name}</h2>
              <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                {p.telegram_id ? (
                  <span
                    title="Telegram ID — captured when this driver joined via invite link"
                    className="inline-flex items-center gap-1 text-3xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground tabular-nums"
                  >
                    tg:{p.telegram_id}
                  </span>
                ) : (
                  <span
                    title="No Telegram linked yet — invite the driver via Telegram or share the bot deep-link"
                    className={`inline-flex items-center gap-1 text-3xs px-1.5 py-0.5 rounded-md border ${toneClasses('warn')}`}
                  >
                    Not linked
                  </span>
                )}
                <SamsaraIdentityCard samsaraDriverId={p.samsara_driver_id} />
              </div>
            </div>
          </div>
          <button onClick={onClose} aria-label="Close" className="text-muted-foreground hover:text-foreground p-1">
            <X size={16} />
          </button>
        </div>

        <div className="grid grid-cols-3 gap-1 bg-muted/50 rounded-lg p-0.5">
          {([
            { key: 'profile' as DetailTab,     label: 'Profile',     icon: <IdCard size={12} />,         soon: false },
            { key: 'vehicles' as DetailTab,    label: 'Vehicles',    icon: <Truck size={12} />,          soon: false },
            { key: 'documents' as DetailTab,   label: 'Documents',   icon: <FileText size={12} />,       soon: false },
            { key: 'inspections' as DetailTab, label: 'Inspections', icon: <ClipboardCheck size={12} />, soon: true  },
            { key: 'trainings' as DetailTab,   label: 'Trainings',   icon: <GraduationCap size={12} />,  soon: true  },
            { key: 'hos' as DetailTab,         label: 'HOS',         icon: <Clock size={12} />,          soon: true  },
            { key: 'integrations' as DetailTab, label: 'Integrations', icon: <Link2 size={12} />,        soon: false },
          ]).filter((tt) => tabs.includes(tt.key)).map((tt) => (
            <button
              key={tt.key}
              onClick={() => onTab(tt.key)}
              className={`relative px-2 py-1.5 rounded-md text-xs font-medium transition flex items-center justify-center gap-1.5 ${
                tab === tt.key ? 'bg-muted/80 text-foreground' : 'text-muted-foreground hover:text-foreground/80'
              }`}
            >
              {tt.icon}{tt.label}
              {tt.soon && (
                <span className="absolute -top-1 -right-1 px-1 py-0.5 rounded-full bg-primary/15 text-primary text-3xs font-semibold uppercase tracking-wider">
                  soon
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      <div className="p-6">
        {tab === 'profile' && (
          <ProfileTab profile={p} onSaved={onSaved} onError={onError} />
        )}
        {tab === 'vehicles' && (
          <VehiclesTab userId={p.user_id} assignments={detail.assignments} onSaved={onSaved} onError={onError} />
        )}
        {tab === 'documents' && (
          <DocumentsTab userId={p.user_id} documents={detail.documents} onSaved={onSaved} onError={onError} />
        )}
        {tab === 'inspections' && (
          <ComingSoonTab
            icon={<ClipboardCheck size={24} className="text-muted-foreground" />}
            title="DOT inspections"
            description="Pre-trip, post-trip, annual, and DOT roadside inspection records — including defects, vehicle, and inspector — will be available here."
          />
        )}
        {tab === 'trainings' && (
          <ComingSoonTab
            icon={<GraduationCap size={24} className="text-muted-foreground" />}
            title="Training records"
            description="Defensive driving, hazmat, cargo securement, and other certifications with expiration tracking. Certificates link back to the Documents tab."
          />
        )}
        {tab === 'hos' && (
          <ComingSoonTab
            icon={<Clock size={24} className="text-muted-foreground" />}
            title="Hours of Service"
            description="Live duty status (on duty / off duty / driving / sleeper berth) plus drive-time, on-duty time, and cycle/shift remaining — synced from Samsara HOS."
          />
        )}
        {tab === 'integrations' && (
          <IntegrationsTab profile={p} onSaved={onSaved} onError={onError} />
        )}
      </div>
    </>
  );
}

/** Per-driver Integrations — the Samsara (telematics), Datatruck (TMS) and
 *  load-name links, together in one tab instead of scattered on Profile.  All
 *  actions hit the driver-roster admin endpoints (gated can_manage_drivers).
 *  Samsara + Datatruck save IMMEDIATELY (not part of the profile form). */
function IntegrationsTab({
  profile, onSaved, onError,
}: {
  profile: DriverProfile;
  onSaved: (m: string) => void;
  onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const userId = profile.user_id;

  const [savingSamsara, setSavingSamsara] = useState(false);
  const saveSamsara = async (sid: string | null) => {
    setSavingSamsara(true);
    try {
      await apiJSON(`/admin/users/${userId}/samsara-driver-id`, {
        method: 'PUT', body: { samsara_driver_id: sid },
      });
      onSaved('Samsara link updated');
      qc.invalidateQueries({ queryKey: ['driver-detail', userId] });
    } catch (e) { onError(e instanceof Error ? e.message : 'Failed'); }
    finally { setSavingSamsara(false); }
  };

  const { data: sources } = useQuery({
    queryKey: ['integration-sources'],
    queryFn: () => apiJSON<{
      datatruck: Array<{ external_id: string; name: string; linked_user_id: number | null }>;
    }>('/admin/users/integration-sources'),
    staleTime: 60_000,
  });
  const datatruck = sources?.datatruck ?? [];
  const currentDt = datatruck.find((d) => d.linked_user_id === userId) ?? null;
  const [dtBusy, setDtBusy] = useState(false);
  const linkDatatruck = async (externalId: string) => {
    setDtBusy(true);
    try {
      const r = await apiJSON<{ loads_backfilled: number }>(
        `/admin/users/${userId}/link-datatruck-driver`,
        { method: 'POST', body: { external_id: externalId } },
      );
      onSaved(externalId
        ? `Datatruck linked${r.loads_backfilled ? ` — ${r.loads_backfilled} loads backfilled` : ''}`
        : 'Datatruck unlinked');
      qc.invalidateQueries({ queryKey: ['integration-sources'] });
    } catch (e) { onError(e instanceof Error ? e.message : 'Failed'); }
    finally { setDtBusy(false); }
  };

  return (
    <div className="space-y-5">
      <Section title="Samsara (telematics)">
        <SamsaraDriverPicker
          value={profile.samsara_driver_id ?? null}
          onChange={(sid) => saveSamsara(sid)}
          currentUserId={userId}
        />
        <p className="text-2xs text-muted-foreground mt-1 inline-flex items-center gap-1">
          <InfoTip size={12} label="Binds this driver to their Samsara identity — powers HOS, coaching, and driver-pay self-service." />
          Samsara identity link{savingSamsara ? ' — saving…' : ''}
        </p>
      </Section>

      <Section title="Datatruck (TMS)">
        {currentDt ? (
          <div className="flex items-center justify-between gap-2 rounded-lg border border-border bg-muted/30 p-3">
            <div className="min-w-0">
              <p className="text-sm font-medium inline-flex items-center gap-1.5">
                <Link2 size={14} className="text-primary shrink-0" /> {currentDt.name}
              </p>
              <p className="text-2xs text-muted-foreground">Linked — the driver's Datatruck loads attribute to them automatically.</p>
            </div>
            <button
              disabled={dtBusy}
              onClick={() => linkDatatruck('')}
              className="shrink-0 text-xs text-danger hover:opacity-80 inline-flex items-center gap-1 disabled:opacity-50"
            >
              <Link2Off size={12} /> Unlink
            </button>
          </div>
        ) : (
          <div className="space-y-1.5">
            <p className="text-2xs text-muted-foreground">Bind a synced Datatruck driver — their loads backfill onto this member automatically.</p>
            <Select value="" onValueChange={(v) => { if (v) linkDatatruck(v); }} disabled={dtBusy}>
              <SelectTrigger className={inputCls}>
                <SelectValue placeholder={datatruck.some((d) => d.linked_user_id == null) ? 'Select a Datatruck driver…' : 'No unlinked Datatruck drivers'} />
              </SelectTrigger>
              <SelectContent>
                {datatruck.filter((d) => d.linked_user_id == null).map((d) => (
                  <SelectItem key={d.external_id} value={d.external_id}>{d.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </Section>
    </div>
  );
}

function ProfileTab({
  profile, onSaved, onError,
}: {
  profile: DriverProfile;
  onSaved: () => void;
  onError: (m: string) => void;
}) {
  type Draft = Partial<Pick<DriverProfile,
    'cdl_number' | 'cdl_state' | 'cdl_class' | 'cdl_expires' | 'med_card_expires' |
    'hire_date' | 'termination_date' | 'dob' | 'phone' | 'home_address' | 'driver_notes' | 'samsara_driver_id'>>;
  const [draft, setDraft] = useState<Draft>({});
  const [saving, setSaving] = useState(false);

  // Reset draft whenever the profile prop changes (different driver selected).
  useEffect(() => { setDraft({}); }, [profile.user_id]);

  const v = (k: keyof DriverProfile) =>
    draft[k as keyof Draft] !== undefined
      ? (draft[k as keyof Draft] ?? '')
      : (profile[k] ?? '');

  const set = (k: keyof Draft, val: string) =>
    setDraft((prev) => ({ ...prev, [k]: val === '' ? null : val }));

  const dirty = Object.keys(draft).length > 0;

  const save = async () => {
    setSaving(true);
    try {
      const body: Record<string, string | null> = {};
      for (const [k, val] of Object.entries(draft)) body[k] = val ?? null;
      await apiJSON(`/drivers/${profile.user_id}`, { method: 'PUT', body });
      setDraft({});
      onSaved();
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      <Section title="CDL">
        <div className="grid grid-cols-2 gap-2">
          <Field label="Number">
            <input className={inputCls} value={String(v('cdl_number'))} onChange={(e) => set('cdl_number', e.target.value)} />
          </Field>
          <Field label="Class">
            <Select value={String(v('cdl_class'))} onValueChange={(val) => set('cdl_class', val)} items={CDL_CLASS_ITEMS}>
              <SelectTrigger className="w-full" aria-label="CDL class"><SelectValue /></SelectTrigger>
              <SelectContent>
                {CDL_CLASS_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </Field>
          <Field label="State">
            <input className={inputCls} maxLength={4} value={String(v('cdl_state'))} onChange={(e) => set('cdl_state', e.target.value.toUpperCase())} />
          </Field>
          <Field label="Expires">
            <input type="date" className={inputCls} value={String(v('cdl_expires'))} onChange={(e) => set('cdl_expires', e.target.value)} />
          </Field>
        </div>
      </Section>

      <Section title="Medical & Identity">
        <div className="grid grid-cols-2 gap-2">
          <Field label="Med. Card Expires">
            <input type="date" className={inputCls} value={String(v('med_card_expires'))} onChange={(e) => set('med_card_expires', e.target.value)} />
          </Field>
          <Field label="DOB">
            <input type="date" className={inputCls} value={String(v('dob'))} onChange={(e) => set('dob', e.target.value)} />
          </Field>
        </div>
      </Section>

      <Section title="Employment">
        <div className="grid grid-cols-2 gap-2">
          <Field label="Hire Date">
            <input type="date" className={inputCls} value={String(v('hire_date'))} onChange={(e) => set('hire_date', e.target.value)} />
          </Field>
          <Field label="Termination">
            <input type="date" className={inputCls} value={String(v('termination_date'))} onChange={(e) => set('termination_date', e.target.value)} />
          </Field>
        </div>
        {/* Samsara link moved to the Integrations tab (roster action, gated on
            can_manage_drivers) — it no longer rides the profile save. */}
      </Section>

      <Section title="Contact">
        <div className="space-y-2">
          <Field label="Phone">
            <input className={inputCls} value={String(v('phone'))} onChange={(e) => set('phone', e.target.value)} />
          </Field>
          <Field label="Address">
            <input className={inputCls} value={String(v('home_address'))} onChange={(e) => set('home_address', e.target.value)} />
          </Field>
        </div>
      </Section>

      <Section title="Notes">
        <textarea
          className={`${inputCls} min-h-[60px]`}
          value={String(v('driver_notes'))}
          onChange={(e) => set('driver_notes', e.target.value)}
        />
      </Section>

      <div className="flex justify-end pt-2">
        <button
          onClick={save}
          disabled={!dirty || saving}
          className="flex items-center gap-1.5 bg-primary text-primary-foreground px-3 py-1.5 rounded text-sm font-medium disabled:opacity-50"
        >
          <Save size={14} />{saving ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </div>
  );
}

function VehiclesTab({
  userId, assignments, onSaved, onError,
}: {
  userId: number;
  assignments: DriverVehicleAssignment[];
  onSaved: () => void;
  onError: (m: string) => void;
}) {
  const [vehicleName, setVehicleName] = useState('');
  const [isPrimary, setIsPrimary] = useState(true);
  const [adding, setAdding] = useState(false);
  const [endingId, setEndingId] = useState<number | null>(null);

  const { data: vehiclesData } = useQuery({
    queryKey: ['drivers-vehicles'],
    queryFn: () => apiJSON<{ vehicles: Array<{ name: string }> }>('/vehicles'),
  });
  const fleetNames = (vehiclesData?.vehicles ?? []).map((v) => v.name);

  const add = async () => {
    if (!vehicleName.trim()) return;
    setAdding(true);
    try {
      await apiJSON(`/drivers/${userId}/assignments`, {
        method: 'POST',
        body: { vehicle_name: vehicleName.trim(), is_primary: isPrimary },
      });
      setVehicleName('');
      onSaved();
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Failed to assign');
    } finally {
      setAdding(false);
    }
  };

  const end = async (id: number) => {
    setEndingId(id);
    try {
      await apiJSON(`/drivers/assignments/${id}`, { method: 'DELETE' });
      onSaved();
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Failed to end assignment');
    } finally {
      setEndingId(null);
    }
  };

  return (
    <div className="space-y-4">
      <Section title="Active assignments">
        {assignments.length === 0 ? (
          <p className="text-xs text-muted-foreground">No vehicles currently assigned.</p>
        ) : (
          <ul className="space-y-1.5">
            {assignments.map((a) => (
              <li key={a.id} className="flex items-center justify-between bg-muted/40 rounded px-2 py-1.5 text-sm">
                <span className="flex items-center gap-2">
                  <Truck size={14} className="text-muted-foreground" />
                  <span className="font-medium">{a.vehicle_name}</span>
                  {a.is_primary && (
                    <span className="px-1.5 py-0.5 text-3xs rounded bg-primary/15 text-primary">Primary</span>
                  )}
                  <span className="text-xs text-muted-foreground tabular-nums">since {a.assigned_at.slice(0, 10)}</span>
                </span>
                <button
                  onClick={() => end(a.id)}
                  disabled={endingId === a.id}
                  className="text-xs text-destructive hover:underline disabled:opacity-50"
                >
                  {endingId === a.id ? 'Ending…' : 'End'}
                </button>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Assign a vehicle">
        <div className="space-y-2">
          <Field label="Vehicle">
            <input
              list="fleet-vehicle-names"
              className={inputCls}
              value={vehicleName}
              onChange={(e) => setVehicleName(e.target.value)}
              placeholder="e.g. 107"
            />
            <datalist id="fleet-vehicle-names">
              {fleetNames.map((n) => <option key={n} value={n} />)}
            </datalist>
          </Field>
          <label className="inline-flex items-center gap-1.5 text-xs">
            <input
              type="checkbox"
              checked={isPrimary}
              onChange={(e) => setIsPrimary(e.target.checked)}
              className="accent-primary"
            />
            Set as primary (demotes any existing primary)
          </label>
          <button
            onClick={add}
            disabled={!vehicleName.trim() || adding}
            className="flex items-center gap-1.5 bg-primary text-primary-foreground px-3 py-1.5 rounded text-sm font-medium disabled:opacity-50"
          >
            <Plus size={14} />{adding ? 'Assigning…' : 'Assign'}
          </button>
        </div>
      </Section>
    </div>
  );
}

function DocumentsTab({
  userId, documents, onSaved, onError,
}: {
  userId: number;
  documents: DriverDocument[];
  onSaved: () => void;
  onError: (m: string) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [docType, setDocType] = useState('cdl');
  const [expiresAt, setExpiresAt] = useState('');
  const [uploading, setUploading] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const upload = async (file: File) => {
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('doc_type', docType);
      if (expiresAt) fd.append('expires_at', expiresAt);
      const res = await apiFetch(`/drivers/${userId}/documents`, {
        method: 'POST',
        body: fd,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail?.message ?? body?.detail ?? `${res.status}`);
      }
      setExpiresAt('');
      if (fileRef.current) fileRef.current.value = '';
      onSaved();
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const download = async (doc: DriverDocument) => {
    try {
      const res = await apiFetch(`/drivers/documents/${doc.id}`);
      if (!res.ok) throw new Error(`${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = doc.file_name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Download failed');
    }
  };

  const remove = async (id: number) => {
    if (!confirm('Delete this document? File will be removed from storage.')) return;
    setDeletingId(id);
    try {
      await apiJSON(`/drivers/documents/${id}`, { method: 'DELETE' });
      onSaved();
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Delete failed');
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="space-y-4">
      <Section title="Upload">
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <Field label="Type">
              <Select value={docType} onValueChange={(v) => setDocType(v)} items={DOC_TYPE_ITEMS}>
                <SelectTrigger className="w-full" aria-label="Type"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {DOC_TYPE_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Expires (optional)">
              <input type="date" className={inputCls} value={expiresAt} onChange={(e) => setExpiresAt(e.target.value)} />
            </Field>
          </div>
          <div className="flex items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept="application/pdf,image/jpeg,image/png,image/webp,image/heic,image/heif"
              className="text-xs file:mr-2 file:px-2 file:py-1 file:rounded file:border-0 file:bg-muted file:text-foreground"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void upload(f);
              }}
              disabled={uploading}
            />
            {uploading && (
              <span className="text-xs text-muted-foreground flex items-center gap-1">
                <Upload size={12} className="animate-pulse" /> Uploading…
              </span>
            )}
          </div>
        </div>
      </Section>

      <Section title={`Active documents (${documents.length})`}>
        {documents.length === 0 ? (
          <p className="text-xs text-muted-foreground">No documents uploaded yet.</p>
        ) : (
          <ul className="space-y-1.5">
            {documents.map((d) => (
              <li key={d.id} className="bg-muted/40 rounded px-2 py-1.5 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <FileText size={14} className="text-muted-foreground shrink-0" />
                    <span className="font-medium truncate">{d.file_name}</span>
                    <StorageBadge driveId={d.drive_file_id} />
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      onClick={() => download(d)}
                      className="p-1 text-muted-foreground hover:text-foreground rounded"
                      aria-label="Download"
                    >
                      <Download size={14} />
                    </button>
                    <button
                      onClick={() => remove(d.id)}
                      disabled={deletingId === d.id}
                      className="p-1 text-destructive hover:bg-destructive/10 rounded disabled:opacity-50"
                      aria-label="Delete"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
                <div className="flex items-center gap-2 mt-1 text-2xs text-muted-foreground">
                  <span>{DOC_LABEL[d.doc_type] ?? d.doc_type}</span>
                  <span>·</span>
                  <Calendar size={12} />
                  <ExpirationChip iso={d.expires_at} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

// ── Small UI primitives ─────────────────────────────────────────

const inputCls =
  'w-full bg-muted border border-border rounded px-2 py-1 text-sm placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-primary';

// ── Samsara driver picker + verification card ──────────────────
//
// Two consumers of the same `/api/drivers/samsara` fetch:
//   * SamsaraDriverPicker — searchable combobox; replaces the
//     free-text input on the Profile tab.  Avoids typos and shows
//     "already linked" warnings.
//   * SamsaraIdentityCard — read-only line in the drawer header
//     showing the live Samsara name attached to a linked ID, or
//     "Unlinked" when blank.  Makes wrong/stale IDs visible at a
//     glance instead of waiting for a downstream failure.
//
// Both share a single react-query key so the data is fetched once
// per drawer open and cached for the standard query TTL.

function useSamsaraDrivers() {
  return useQuery({
    queryKey: ['samsara-drivers'],
    queryFn: () => apiJSON<SamsaraDriversResponse>('/drivers/samsara'),
    staleTime: 60_000,
  });
}

function SamsaraDriverPicker({
  value, onChange, currentUserId,
}: {
  value: string | null;
  onChange: (sid: string | null) => void;
  currentUserId: number;
}) {
  const { data, isLoading, error } = useSamsaraDrivers();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  const drivers = data?.drivers ?? [];
  const samsaraUnavailable = data?.error === 'samsara_unavailable';

  const selected = useMemo(
    () => drivers.find((d) => d.samsara_driver_id === value) ?? null,
    [drivers, value],
  );

  const filtered = useMemo(() => {
    if (!query.trim()) return drivers;
    const q = query.toLowerCase();
    return drivers.filter((d) =>
      d.name.toLowerCase().includes(q) ||
      d.username.toLowerCase().includes(q) ||
      d.samsara_driver_id.includes(q),
    );
  }, [drivers, query]);

  // Fallback: when Samsara is unreachable, degrade to a plain text
  // input so the admin can still type the ID manually.
  if (samsaraUnavailable || error) {
    return (
      <div className="space-y-1">
        <input
          className={inputCls}
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value || null)}
          placeholder="Samsara driver ID (autocomplete unavailable)"
        />
        <p className={`text-3xs ${toneText('warn')}`}>
          Couldn't reach Samsara — type the ID manually.
        </p>
      </div>
    );
  }

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`${inputCls} text-left flex items-center justify-between gap-2`}
      >
        {selected ? (
          <span className="flex items-center gap-2 min-w-0">
            <Link2 size={12} className="text-primary shrink-0" />
            <span className="truncate font-medium">{selected.name}</span>
            {selected.username && (
              <span className="text-3xs text-muted-foreground tabular-nums">
                {selected.username}
              </span>
            )}
          </span>
        ) : value ? (
          <span className="flex items-center gap-2 min-w-0">
            <AlertTriangle size={12} className={`shrink-0 ${toneText('warn')}`} />
            <span className="truncate text-muted-foreground">
              ID {value} (not in fleet)
            </span>
          </span>
        ) : (
          <span className="flex items-center gap-2 text-muted-foreground">
            <Link2Off size={12} />
            <span>Not linked</span>
          </span>
        )}
        <span className="text-muted-foreground text-xs shrink-0">▾</span>
      </button>

      {open && (
        <div className="absolute z-20 mt-1 left-0 right-0 bg-card border border-border rounded shadow-lg max-h-80 overflow-hidden flex flex-col">
          <div className="p-1.5 border-b border-border flex items-center gap-1.5">
            <Search size={12} className="text-muted-foreground" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by name, username, or ID…"
              className="flex-1 bg-transparent text-xs focus:outline-none"
            />
            {value && (
              <button
                type="button"
                onClick={() => { onChange(null); setOpen(false); setQuery(''); }}
                className="text-xs text-destructive hover:underline"
              >
                Unlink
              </button>
            )}
          </div>
          <div className="overflow-y-auto">
            {isLoading ? (
              <div className="p-3 text-xs text-muted-foreground text-center">Loading…</div>
            ) : filtered.length === 0 ? (
              <div className="p-3 text-xs text-muted-foreground text-center">
                No matches
              </div>
            ) : (
              filtered.map((d) => {
                const linkedElsewhere =
                  d.linked_user_id != null && d.linked_user_id !== currentUserId;
                const isSelected = d.samsara_driver_id === value;
                return (
                  <button
                    key={d.samsara_driver_id}
                    type="button"
                    onClick={() => {
                      if (linkedElsewhere) return;
                      onChange(d.samsara_driver_id);
                      setOpen(false);
                      setQuery('');
                    }}
                    disabled={linkedElsewhere}
                    className={`w-full text-left px-2 py-1.5 text-xs flex items-center gap-2 hover:bg-muted/50 ${
                      isSelected ? 'bg-primary/10' : ''
                    } ${linkedElsewhere ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    {isSelected ? (
                      <Check size={12} className="text-primary shrink-0" />
                    ) : (
                      <span className="w-3 shrink-0" />
                    )}
                    <span className="flex-1 min-w-0">
                      <span className="font-medium truncate block">{d.name || '—'}</span>
                      <span className="text-3xs text-muted-foreground tabular-nums">
                        {d.username || `id:${d.samsara_driver_id}`}
                        {d.company_code && ` · ${d.company_code}`}
                        {d.deactivated && ' · deactivated'}
                      </span>
                    </span>
                    {linkedElsewhere && (
                      <span className={`text-3xs shrink-0 ${toneText('warn')}`}>
                        already linked
                      </span>
                    )}
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function SamsaraIdentityCard({ samsaraDriverId }: { samsaraDriverId: string | null }) {
  const { data, isLoading } = useSamsaraDrivers();
  if (!samsaraDriverId) {
    return (
      <span className="inline-flex items-center gap-1 text-3xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
        <Link2Off size={12} />
        Unlinked
      </span>
    );
  }
  if (isLoading) {
    return (
      <span className="text-3xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
        samsara:{samsaraDriverId}
      </span>
    );
  }
  const match = data?.drivers.find((d) => d.samsara_driver_id === samsaraDriverId);
  if (!match) {
    return (
      <span
        title="This Samsara driver ID is not in the fleet roster — possibly stale or wrong."
        className={`inline-flex items-center gap-1 text-3xs px-1.5 py-0.5 rounded-md border ${toneClasses('warn')}`}
      >
        <AlertTriangle size={12} />
        ID {samsaraDriverId} — not in Samsara
      </span>
    );
  }
  return (
    <span
      title={`Linked to Samsara driver ${match.name} (${match.username || match.samsara_driver_id})`}
      className="inline-flex items-center gap-1 text-3xs px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/30"
    >
      <Link2 size={12} />
      {match.name}
      {match.deactivated && (
        <span className={`ml-1 ${toneText('warn')}`}>(deactivated)</span>
      )}
    </span>
  );
}

function ComingSoonTab({
  icon, title, description,
}: { icon: React.ReactNode; title: string; description: string }) {
  return (
    <div className="flex flex-col items-center text-center py-8 px-4 gap-3">
      <div className="w-14 h-14 rounded-full bg-muted flex items-center justify-center">
        {icon}
      </div>
      <h3 className="text-sm font-semibold">{title}</h3>
      <p className="text-xs text-muted-foreground max-w-[320px] leading-relaxed">
        {description}
      </p>
      <span className="mt-2 px-2 py-0.5 rounded-full text-3xs uppercase tracking-wider bg-primary/15 text-primary border border-primary/30">
        Coming soon
      </span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="text-2xs font-semibold text-muted-foreground uppercase tracking-wider mb-1.5">{title}</h3>
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-3xs text-muted-foreground uppercase tracking-wider">{label}</span>
      {children}
    </label>
  );
}
