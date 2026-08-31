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
 * Upload/delete need can_manage_vehicle_docs — its own grant, so
 * filing an insurance certificate no longer requires the power to
 * archive a tractor; everyone who can see the page reads and
 * downloads.  The affordances hide rather than 403: a button leading
 * to a refusal is worse than no button.
 *
 * Uploading opens the shared dialog rather than a bare file picker,
 * because the expiry date has to be asked for — without it the whole
 * expiry-warning chain reads a field nothing ever sets.
 */
import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, FileText, Trash2, Upload } from 'lucide-react';
import { toast } from 'sonner';

import { apiFetch, apiJSON } from '../../../api/client';
import { toneText } from '../../../lib/status';
import { typeLabel } from './docTypes';
import UploadDocumentDialog from './UploadDocumentDialog';
import { Button } from '../../../components/ui/button';
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

  const [uploadOpen, setUploadOpen] = useState(false);

  // Stepping a superseded paper aside — last year's registration once
  // this year's is filed.  Offered BEFORE delete because it is almost
  // always the right act: the old document still proves the truck was
  // legal for the period it covered, which is what an audit asks.
  const archive = async (d: Doc) => {
    try {
      await apiJSON(`/vehicles/documents/${d.id}/archive`, { method: 'POST' });
      await qc.invalidateQueries({ queryKey: ['vehicle-documents', registryId] });
      await qc.invalidateQueries({ queryKey: ['fleet-documents'] });
      toast.success(`${d.file_name} archived`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Archive failed');
    }
  };

  const remove = async (d: Doc) => {
    if (!confirm(
      `Delete ${d.file_name}? The file is removed from storage.\n\n` +
      'To keep it as a record of a period that has passed, use Archive ' +
      'instead.')) return;
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
          <Button
            type="button" variant="outline" size="sm"
            onClick={() => setUploadOpen(true)}
          >
            <Upload />
            Upload
          </Button>
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
                  {typeLabel(d.doc_type)}
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
                  aria-label={`Archive ${d.file_name}`}
                  onClick={() => void archive(d)}
                >
                  <Archive className="text-muted-foreground" />
                </Button>
              )}
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
      {canManage && (
        <UploadDocumentDialog
          open={uploadOpen}
          onClose={() => setUploadOpen(false)}
          onUploaded={() => {
            void qc.invalidateQueries({
              queryKey: ['vehicle-documents', registryId] });
            void qc.invalidateQueries({ queryKey: ['fleet-documents'] });
          }}
          vehicle={{ registry_id: registryId, name: vehicleName,
                     company: company ?? undefined }}
          docTypes={data?.doc_types}
        />
      )}
    </Card>
  );
}
