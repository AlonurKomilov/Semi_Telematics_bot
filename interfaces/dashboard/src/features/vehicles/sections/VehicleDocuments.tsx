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
import { FileText, Loader2, Trash2, Upload } from 'lucide-react';
import { toast } from 'sonner';

import { apiFetch, apiJSON } from '../../../api/client';
import { Button } from '../../../components/ui/button';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../../../components/ui/select';
import { Card } from '@/components/ui/card';
import { SectionHeader } from '@/components/shell';
import { useViewPermissions } from '../../../hooks/useViewPermissions';
import { useVehicle } from './_shared/useVehicle';
import type { VehicleSectionProps } from './_shared/types';

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

function fmtSize(bytes: number | null): string {
  if (!bytes) return '';
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function VehicleDocuments({ vehicleName, company }: VehicleSectionProps) {
  const { has } = useViewPermissions();
  const canManage = has('can_manage_vehicles');
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
          <div className="flex items-center gap-2">
            <Select
              value={docType}
              onValueChange={setDocType}
              items={(data?.doc_types ?? Object.keys(TYPE_LABEL)).map(
                (t) => ({ value: t, label: TYPE_LABEL[t] ?? t }),
              )}
            >
              <SelectTrigger aria-label="Document type for upload">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(data?.doc_types ?? Object.keys(TYPE_LABEL)).map((t) => (
                  <SelectItem key={t} value={t}>
                    {TYPE_LABEL[t] ?? t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <input
              ref={fileRef} type="file" className="hidden"
              accept="application/pdf,image/*"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void upload(f);
              }}
            />
            <Button
              type="button" variant="outline" size="sm"
              disabled={busy}
              onClick={() => fileRef.current?.click()}
            >
              {busy ? <Loader2 className="animate-spin" /> : <Upload />}
              Upload
            </Button>
          </div>
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
                  {d.expires_at ? ` · expires ${d.expires_at}` : ''}
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
