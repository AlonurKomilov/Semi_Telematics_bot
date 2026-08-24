/**
 * Email notification channel — the settings surface for the email
 * delivery lifecycle (connect → verify → choose types + cadence).
 *
 * A user's alert email is its OWN address (not their login email), so it
 * carries its own connect + verification.  Until it's verified nothing
 * sends, so the card walks: enter address → "check your inbox" → verified,
 * then reveals the per-type toggles + the delivery cadence.  Email
 * defaults to a daily digest — per-alert email at fleet volume is
 * unusable.
 */
import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Mail, CheckCircle2, Clock } from 'lucide-react';
import { apiJSON } from '@/api/client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Card } from '@/components/ui/card';

interface EmailPrefs {
  relevant_types: string[];
  email: {
    connected: boolean;
    verified: boolean;
    address: string;
    enabled_master: boolean;
    cadence: string;
    types: Record<string, boolean>;
  };
}

const CADENCE_LABEL: Record<string, string> = {
  immediate: 'Immediately',
  hourly: 'Hourly digest',
  daily: 'Daily digest',
};

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function EmailChannelCard({ onChanged }: { onChanged: () => void }) {
  const [prefs, setPrefs] = useState<EmailPrefs | null>(null);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  // Inline validation on BLUR — the submit path also validates, but a
  // toast that only appears after you press the button makes you hunt for
  // what was wrong.  Reset on edit so it doesn't nag mid-typing.
  const [touched, setTouched] = useState(false);

  const load = async () => {
    try {
      const data = await apiJSON<EmailPrefs>('/notifications/prefs/email');
      setPrefs(data);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load email settings');
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void load(); }, []);

  // Only complain once the field has been left AND has content — an empty
  // untouched field isn't an error, it's the starting state.
  const showEmailError = touched && draft.trim() !== '' && !EMAIL_RE.test(draft.trim());

  // One path for connect / change / resend — the backend upsert is
  // idempotent, so a resend is just a re-submit of the known address.
  const submit = async (address: string, isResend = false) => {
    address = address.trim();
    if (!EMAIL_RE.test(address)) {
      toast.error('Enter a valid email address');
      return;
    }
    setBusy(true);
    try {
      const res = await apiJSON<{ ok: boolean; sent?: boolean; already_verified?: boolean }>(
        '/notifications/channels/email', { method: 'POST', body: { address } });
      if (!res.ok) throw new Error('Could not save address');
      toast.success(
        res.already_verified ? 'Already verified'
          : !res.sent ? 'Address saved — but the confirmation email couldn’t be sent. Check email settings.'
          : isResend ? `Verification link re-sent to ${address}`
          : `Verification link sent to ${address}`);
      setEditing(false);
      setDraft('');
      await load();
      onChanged();                       // matrix column state may have changed
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to connect email');
    } finally {
      setBusy(false);
    }
  };

  const setCadence = async (cadence: string) => {
    if (!prefs) return;
    const prevCadence = prefs.email.cadence;
    setPrefs(p => p && ({ ...p, email: { ...p.email, cadence } }));
    try {
      await apiJSON('/notifications/prefs/email/cadence', {
        method: 'PUT', body: { cadence },
      });
    } catch (e) {
      setPrefs(p => p && ({ ...p, email: { ...p.email, cadence: prevCadence } }));
      toast.error(e instanceof Error ? e.message : 'Save failed');
    }
  };

  if (loading) {
    return (
      <Card render={<section />}>
        <p className="text-sm text-muted-foreground">Loading email settings…</p>
      </Card>
    );
  }
  if (!prefs) return null;

  const { email } = prefs;
  const showForm = editing || !email.connected;

  return (
    <Card render={<section />}>
      {/* Channel header + status */}
      <div className="flex items-center justify-between gap-2 mb-1">
        <p className="text-base font-semibold inline-flex items-center gap-2">
          <Mail className="size-4" /> Email
        </p>
        {email.connected && (
          email.verified
            ? <span className="inline-flex items-center gap-1 text-xs font-medium text-ok">
                <CheckCircle2 className="size-3.5" /> Verified
              </span>
            : <span className="inline-flex items-center gap-1 text-xs font-medium text-warn">
                <Clock className="size-3.5" /> Pending
              </span>
        )}
      </div>
      <p className="text-xs text-muted-foreground mb-3">
        Get the alerts you choose by email, on your own schedule. Separate from your login email.
      </p>

      {/* Connect / change form */}
      {showForm ? (
        <div className="flex flex-col gap-2">
          {editing && email.verified && (
            <p className="text-xs text-warn">
              Changing your address pauses email alerts until you confirm the new one.
            </p>
          )}
          <div className="flex flex-col sm:flex-row gap-2">
          <Input
            type="email"
            inputMode="email"
            placeholder="you@company.com"
            value={draft}
            aria-invalid={showEmailError || undefined}
            aria-describedby={showEmailError ? 'email-channel-error' : undefined}
            onChange={e => { setDraft(e.target.value); setTouched(false); }}
            onBlur={() => setTouched(true)}
            onKeyDown={e => { if (e.key === 'Enter') void submit(draft); }}
            className="flex-1"
          />
          <div className="flex gap-2">
            <Button onClick={() => void submit(draft)} disabled={busy}>
              {busy ? 'Sending…' : 'Send verification link'}
            </Button>
            {email.connected && (
              <Button variant="ghost" onClick={() => { setEditing(false); setDraft(''); }}>
                Cancel
              </Button>
            )}
          </div>
          </div>
          {showEmailError && (
            <p id="email-channel-error" className="text-xs text-danger">
              Enter a full address, e.g. you@company.com
            </p>
          )}
        </div>
      ) : (
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm truncate">{email.address}</span>
          <Button variant="ghost" size="sm" onClick={() => { setEditing(true); setDraft(email.address); }}>
            Change
          </Button>
        </div>
      )}

      {/* Pending hint */}
      {email.connected && !email.verified && !showForm && (
        <p className="text-xs text-muted-foreground mt-2">
          We sent a confirmation link to <span className="font-medium">{email.address}</span>. Click it to start receiving email alerts.{' '}
          <button className="text-primary hover:underline py-0.5 -my-0.5 min-h-tap"
                  onClick={() => { void load().then(onChanged); }}>
            Already confirmed? Refresh
          </button>. Didn’t arrive?{' '}
          <button className="text-primary hover:underline disabled:opacity-50 py-0.5 -my-0.5 min-h-tap" disabled={busy}
                  onClick={() => void submit(email.address, true)}>
            Resend
          </button>.
        </p>
      )}

      {/* Verified → cadence + per-type toggles */}
      {email.verified && (
        <div className="mt-4 pt-4 border-t border-border/60 space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium">Delivery</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                How often email alerts are grouped and sent.
              </p>
            </div>
            <Select value={email.cadence} onValueChange={v => void setCadence(v)}>
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {['immediate', 'hourly', 'daily'].map(c => (
                  <SelectItem key={c} value={c}>{CADENCE_LABEL[c]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {/* WHICH alert types go to email is chosen in the "Notify me
              when" matrix below — this card only manages the connection
              and the delivery cadence. */}
        </div>
      )}
    </Card>
  );
}
