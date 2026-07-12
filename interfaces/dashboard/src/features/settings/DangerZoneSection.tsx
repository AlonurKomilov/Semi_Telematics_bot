import { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { useAuth } from '../../context/AuthContext';
import { toneClasses } from '../../lib/status';

interface Lifecycle {
  account_id: number;
  name: string;
  is_active: boolean;
  suspended: boolean;
  deleted_at: string | null;
  purge_at: string | null;
}

// Owner-only account deletion with a 90-day retention window.
//
// Why retention instead of instant erasure: motor carriers are subject
// to FMCSA recordkeeping rules (driver qualification files, inspection
// and maintenance records).  Erasing those the moment an owner clicks
// "delete" could put THEIR company out of compliance — so the data is
// frozen and recoverable for 90 days, then permanently purged.  Files
// in the company's own Google Drive are never touched; they belong to
// the customer.
export default function DangerZoneSection() {
  const { user } = useAuth();
  const [lc, setLc] = useState<Lifecycle | null>(null);
  const [step, setStep] = useState<'idle' | 'code'>('idle');
  const [code, setCode] = useState('');
  const [codeEmail, setCodeEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [notice, setNotice] = useState('');

  const isOwner = user?.role === 'owner';

  const load = () => {
    apiJSON<Lifecycle>('/user/account/lifecycle')
      .then(setLc)
      .catch(() => { /* non-fatal — section just stays hidden */ });
  };
  useEffect(() => { if (isOwner) load(); }, [isOwner]);

  if (!isOwner || !lc) return null;

  const deletionPending = !!lc.deleted_at;
  const purgeDate = lc.purge_at?.slice(0, 10) ?? '';
  const daysLeft = lc.purge_at
    ? Math.max(0, Math.ceil((Date.parse(lc.purge_at) - Date.now()) / 86_400_000))
    : 0;

  const requestCode = async () => {
    setBusy(true);
    setErr('');
    try {
      const r = await apiJSON<{ email: string }>('/user/account/delete/request', {
        method: 'POST',
      });
      setCodeEmail(r.email);
      setStep('code');
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not start deletion');
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async () => {
    if (code.trim().length !== 6) {
      setErr('Enter the 6-digit code from your email.');
      return;
    }
    setBusy(true);
    setErr('');
    try {
      await apiJSON('/user/account/delete/confirm', {
        method: 'POST',
        body: { code: code.trim() },
      });
      setStep('idle');
      setCode('');
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Confirmation failed');
    } finally {
      setBusy(false);
    }
  };

  const cancelDeletion = async () => {
    setBusy(true);
    setErr('');
    try {
      await apiJSON('/user/account/delete/cancel', { method: 'POST' });
      setNotice('Account reactivated — nothing was removed.');
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Reactivation failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="bg-card rounded-lg border border-destructive/40 p-6 mt-6">
      <h2 className="text-lg font-semibold flex items-center gap-2 text-destructive">
        <AlertTriangle size={18} />
        Danger zone
      </h2>

      {notice && (
        <div className={`mt-3 rounded-md px-3 py-2 text-sm ${toneClasses('ok')}`}>
          {notice}
        </div>
      )}
      {err && (
        <div className={`mt-3 rounded-md px-3 py-2 text-sm ${toneClasses('danger')}`}>
          {err}
        </div>
      )}

      {deletionPending ? (
        <div className="mt-4 space-y-3">
          <div className={`rounded-md px-4 py-3 text-sm ${toneClasses('danger')}`}>
            <p className="font-semibold">
              Account deletion is scheduled — permanent erasure on {purgeDate}
              {daysLeft > 0 && <> ({daysLeft} day{daysLeft === 1 ? '' : 's'} left)</>}.
            </p>
            <p className="mt-1.5">
              All logins except yours are blocked. Your data is retained until
              the date above so your company can meet FMCSA recordkeeping
              obligations and recover if this was a mistake. Export any
              compliance records you need before that date — after the purge,
              recovery is impossible. Files in your own Google Drive are not
              affected.
            </p>
          </div>
          <button
            onClick={cancelDeletion}
            disabled={busy}
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {busy ? '…' : 'Reactivate account'}
          </button>
        </div>
      ) : step === 'code' ? (
        <div className="mt-4 space-y-3 max-w-md">
          <p className="text-sm text-muted-foreground">
            We emailed a 6-digit confirmation code to{' '}
            <strong className="text-foreground">{codeEmail}</strong>. Enter it
            below to confirm deletion. The code expires in 15 minutes.
          </p>
          <input
            type="text"
            inputMode="numeric"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
            placeholder="000000"
            className="w-40 rounded-md border border-border bg-background px-3 py-2 text-center text-lg tracking-[6px]"
            autoFocus
          />
          <div className="flex gap-2">
            <button
              onClick={confirmDelete}
              disabled={busy || code.length !== 6}
              className="rounded-md bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50"
            >
              {busy ? '…' : 'Confirm deletion'}
            </button>
            <button
              onClick={() => { setStep('idle'); setCode(''); setErr(''); }}
              disabled={busy}
              className="rounded-md px-4 py-2 text-sm text-muted-foreground hover:text-foreground"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-4 space-y-3">
          <p className="text-sm text-muted-foreground max-w-2xl">
            Deleting the account deactivates it immediately for every user.
            Your data is then held for a <strong className="text-foreground">90-day
            retention window</strong> — motor carriers are subject to FMCSA
            recordkeeping requirements, and the window also lets you undo an
            accidental deletion. After 90 days everything is permanently erased
            from 4truck. Files stored in your own Google Drive stay with you.
          </p>
          <button
            onClick={requestCode}
            disabled={busy}
            className="rounded-md border border-destructive/50 px-4 py-2 text-sm font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50"
          >
            {busy ? '…' : 'Delete this account…'}
          </button>
        </div>
      )}
    </section>
  );
}
