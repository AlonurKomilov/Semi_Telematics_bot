/**
 * Upload one document — the form that finally asks when it expires.
 *
 * The first upload flow sent only the file and its type, so no
 * document could ever carry an expiry date: the alert, the warn/danger
 * tones and the Expiring / Expired tabs were all reading a field the
 * UI never collected.  A whole compliance feature, dead on arrival,
 * behind a control that looked finished.
 *
 * One dialog serves both surfaces.  On a truck's own card the vehicle
 * is fixed and its field never renders; on the fleet page it is the
 * first thing asked.  Same request either way, so the two cannot drift
 * into sending different fields.
 *
 * It replaces the split "Upload ▾" button, which was the right fix for
 * a type picker shaped like a filter but optimised the wrong thing:
 * speed, on a flow whose most important field it could not ask for.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Upload } from 'lucide-react';
import { toast } from 'sonner';

import { apiFetch } from '../../../api/client';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../../components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../../../components/ui/select';
import { TYPE_ORDER, typeLabel } from './docTypes';

export interface UploadTargetVehicle {
  registry_id: number;
  name: string;
  company?: string;
}

export default function UploadDocumentDialog({
  open, onClose, onUploaded, vehicle, vehicles, docTypes,
}: {
  open: boolean;
  onClose: () => void;
  onUploaded: () => void;
  /** Fixed target — the truck's own card.  Omit on the fleet page. */
  vehicle?: UploadTargetVehicle | null;
  /** Choosable targets — the fleet page.  Ignored when `vehicle` is set. */
  vehicles?: UploadTargetVehicle[];
  /** Types the server declared; falls back to the local vocabulary. */
  docTypes?: string[];
}) {
  const [docType, setDocType] = useState('registration');
  const [vehicleId, setVehicleId] = useState<string>('');
  const [issued, setIssued] = useState('');
  const [expires, setExpires] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setDocType('registration');
    setVehicleId(vehicle ? String(vehicle.registry_id) : '');
    setIssued(''); setExpires(''); setFile(null); setError('');
  }, [open, vehicle]);

  const types = docTypes?.length ? docTypes : TYPE_ORDER;
  const targets = useMemo(
    () => (vehicle ? [vehicle] : (vehicles ?? [])).filter(
      (v) => v.registry_id != null),
    [vehicle, vehicles],
  );

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const target = vehicle?.registry_id ?? Number(vehicleId);
    if (!target || !file) {
      setError(!target ? 'Pick the vehicle this document belongs to.'
                       : 'Choose a file.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('doc_type', docType);
      // The two fields the old flow dropped.  Sent only when filled —
      // an empty string would store as a date nobody chose.
      if (issued) form.append('issued_at', issued);
      if (expires) form.append('expires_at', expires);
      const res = await apiFetch(
        `/vehicles/registry/${target}/documents`,
        { method: 'POST', body: form },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(
          typeof err.detail === 'string' ? err.detail : 'Upload failed');
      }
      toast.success(`${file.name} uploaded`);
      onUploaded();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Upload document</DialogTitle>
          <DialogDescription>
            Registration, cab card, insurance, annual inspection. Set the
            expiry and this truck warns you before it lapses.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="space-y-3">
          {!vehicle && (
            <div>
              <label className="block text-xs text-muted-foreground mb-1">
                Vehicle
              </label>
              <Select
                value={vehicleId} onValueChange={setVehicleId}
                items={targets.map((v) => ({
                  value: String(v.registry_id),
                  label: v.company ? `${v.name} · ${v.company}` : v.name,
                }))}
              >
                <SelectTrigger className="w-full" aria-label="Vehicle">
                  <SelectValue placeholder="Pick a truck" />
                </SelectTrigger>
                <SelectContent>
                  {targets.map((v) => (
                    <SelectItem key={v.registry_id} value={String(v.registry_id)}>
                      {v.company ? `${v.name} · ${v.company}` : v.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          <div>
            <label className="block text-xs text-muted-foreground mb-1">Type</label>
            <Select
              value={docType} onValueChange={setDocType}
              items={types.map((t) => ({ value: t, label: typeLabel(t) }))}
            >
              <SelectTrigger className="w-full" aria-label="Document type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {types.map((t) => (
                  <SelectItem key={t} value={t}>{typeLabel(t)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">
                Issued
              </label>
              <Input type="date" value={issued}
                     onChange={(e) => setIssued(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">
                Expires
              </label>
              <Input type="date" value={expires}
                     onChange={(e) => setExpires(e.target.value)} />
            </div>
          </div>
          <p className="text-2xs text-muted-foreground">
            Leave Expires empty for a document that never lapses — a title,
            a bill of sale. Anything with a date warns at 30, 14, 7 and 1
            days, then on the day.
          </p>

          <div>
            <label className="block text-xs text-muted-foreground mb-1">File</label>
            <input
              ref={fileRef} type="file"
              accept="application/pdf,image/*"
              className="block w-full text-xs text-muted-foreground file:mr-3 file:rounded-md file:border file:border-border file:bg-card file:px-3 file:py-1.5 file:text-xs file:text-foreground min-h-tap"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </div>

          {error && <p className="text-xs text-danger">{error}</p>}

          <DialogFooter>
            <Button type="button" variant="outline" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={busy}>
              {busy ? <Loader2 className="animate-spin" /> : <Upload />}
              Upload
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
