/**
 * Documents — the paperwork this truck carries.
 *
 * Registration, title, insurance, annual-inspection certificates.
 * Files live in the company's own folder tree
 * ({COMPANY}/vehicles/{unit}/, mirrored into the customer's Drive),
 * follow the truck into the archive tree when it is retired, and come
 * home when it is restored — so this card works unchanged on an
 * archived truck, which is the point of keeping the record.
 *
 * Upload/delete need can_manage_vehicles; everyone who can see the
 * page can read and download.  The affordances hide rather than 403:
 * a button leading to a refusal is worse than no button.
 */
import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, FileText, Loader2, Trash2, Upload } from 'lucide-react';
import { toast } from 'sonner';

import { apiFetch, apiJSON } from '../../../api/client';
import { toneText } from '../../../lib/status';
import { Button } from '../../../components/ui/button';
import { ActionMenu } from '../../../components/ui/context-menu';
import { Card } from '@/components/ui/card';
import { SectionHeader } from '@/components/shell';
import { useViewPermissions } from '../../../hooks/useViewPermissions';
import { useVehicle } from '../sections/_shared/useVehicle';
import type { VehicleSectionProps } from '../sections/_shared/types';

interface Doc {
  id: number;
  doc_type: string;
  file_name: string;
  file_size: number | null;
  uploaded_at: string;
  expires_at: string | null;
}

const TYPE_LABEL: Record<string, string> = {
  registration: 'Registration',
  title: 'Title',
  insurance: 'Insurance',
  annual_inspection: 'Annual inspection',
  lease: 'Lease',
  purchase: 'Purchase',
  other: 'Other',
};

/** How an expiry date should READ, not just what it says.
 *
 *  The date rendered in the same muted grey as the file size, so a
 *  lapsed insurance certificate looked exactly like a current one and
 *  the first anyone learned of it was a roadside inspection.  The
 *  alert now warns ahead of time; this is the same fact on the page
 *  the operator is already looking at. */
function expiryTone(iso: string | null): { text: string; cls: string } | null {
  if (!iso) return null;
  const day = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(day.getTime())) return null;
  const today = new Date();
  const days = Math.round(
    (day.getTime() - Date.UTC(
      today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate(),
    )) / 86_400_000,
  );
  if (days < 0) return { text: `expired ${iso}`, cls: toneText('danger') };
  if (days === 0) return { text: 'expires today', cls: toneText('danger') };
  if (days <= 30) return { text: `expires in ${days}d`, cls: toneText('warn') };
  return { text: `expires ${iso}`, cls: 'text-muted-foreground' };
}

function fmtSize(bytes: number | null): string {
  if (!bytes) return '';
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function DocumentsCard({ vehicleName, company }: VehicleSectionProps) {
  const { has } = useViewPermissions();
  const canManage = has('can_manage_vehicle_docs');
  const qc = useQueryClient();
  const { vehicle } = useVehicle(vehicleName, company);
  const registryId = vehicle?.registry_id ?? null;

  const { data, isLoading } = useQuery<{ documents: Doc[]; doc_types: string[] }>({
    queryKey: ['vehicle-documents', registryId],
    queryFn: () => apiJSON(`/vehicles/registry/${registryId}/documents`),
    enabled: registryId != null,
  });

  const fileRef = useRef<HTMLInputElement>(null);
  const [docType, setDocType] = useState('registration');
  const [busy, setBusy] = useState(false);

  const upload = async (file: File) => {
    if (registryId == null) return;
    setBusy(true);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('doc_type', docType);
      const res = await apiFetch(
        `/vehicles/registry/${registryId}/documents`,
        { method: 'POST', body: form },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(
          typeof err.detail === 'string' ? err.detail : 'Upload failed');
      }
      await qc.invalidateQueries({ queryKey: ['vehicle-documents', registryId] });
      toast.success(`${file.name} uploaded`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const remove = async (d: Doc) => {
    if (!confirm(`Delete ${d.file_name}? The file is removed from storage.`)) return;
    try {
      await apiJSON(`/vehicles/documents/${d.id}`, { method: 'DELETE' });
      await qc.invalidateQueries({ queryKey: ['vehicle-documents', registryId] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  const download = async (d: Doc) => {
    // Through apiFetch for the auth header, then a blob URL — a plain
    // <a href> would arrive without the bearer token.
    const res = await apiFetch(`/vehicles/documents/${d.id}/download`, {});
    if (!res.ok) { toast.error('Could not open the document'); return; }
    const url = URL.createObjectURL(await res.blob());
    window.open(url, '_blank', 'noopener');
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };

  // A live-only vehicle the registry has not caught yet has nowhere to
  // hang a document.  Render nothing rather than a card that cannot
  // work — the registry overlay catches up on the next ingest.
  if (registryId == null) return null;

  const docs = data?.documents ?? [];

  return (
    <Card className="mt-6">
      <div className="flex items-center justify-between mb-3">
        <SectionHeader>Documents</SectionHeader>
        {canManage && (
          <>
            <input
              ref={fileRef} type="file" className="hidden"
              accept="application/pdf,image/*"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void upload(f);
              }}
            />
            {/* The type is part of DECIDING to upload, not a setting
                parked beside the button.  It used to be a Select in the
                header — the shape this app uses for FILTERING
                everywhere else — so it read as "show me registrations"
                while it actually meant "file the next one as a
                registration".  One control now: pick the kind, the
                picker opens. */}
            <ActionMenu
              items={(data?.doc_types ?? Object.keys(TYPE_LABEL)).map((t) => ({
                key: t,
                label: TYPE_LABEL[t] ?? t,
                onSelect: () => {
                  setDocType(t);
                  fileRef.current?.click();
                },
              }))}
            >
              <Button type="button" variant="outline" size="sm" disabled={busy}>
                {busy ? <Loader2 className="animate-spin" /> : <Upload />}
                Upload
                <ChevronDown />
              </Button>
            </ActionMenu>
          </>
        )}
      </div>

      {isLoading && (
        <p className="text-sm text-muted-foreground">Loading documents…</p>
      )}
      {!isLoading && docs.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No documents for this vehicle yet
          {canManage ? ' — registration, title, insurance and annual inspections belong here.' : '.'}
        </p>
      )}
      {docs.length > 0 && (
        <ul className="divide-y divide-border">
          {docs.map((d) => (
            <li key={d.id} className="flex items-center gap-3 py-2 text-sm">
              <FileText className="size-4 shrink-0 text-muted-foreground" />
              <button
                type="button"
                className="min-w-0 flex-1 text-left min-h-tap"
                onClick={() => void download(d)}
              >
                <span className="block truncate text-foreground">{d.file_name}</span>
                <span className="block text-xs text-muted-foreground">
                  {TYPE_LABEL[d.doc_type] ?? d.doc_type}
                  {(() => {
                    const e = expiryTone(d.expires_at);
                    return e ? (
                      <> · <span className={e.cls}>{e.text}</span></>
                    ) : null;
                  })()}
                  {d.file_size ? ` · ${fmtSize(d.file_size)}` : ''}
                </span>
              </button>
              {canManage && (
                <Button
                  type="button" variant="ghost" size="sm"
                  aria-label={`Delete ${d.file_name}`}
                  onClick={() => void remove(d)}
                >
                  <Trash2 className="text-muted-foreground" />
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
