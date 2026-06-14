import { useState } from 'react';
import { apiJSON } from '../api/client';

interface Props {
  onClose: () => void;
  onCreated: (accountId: number) => void;
}

interface CreateResponse {
  account_id: number;
  name: string;
  owner_user_id: number;
  owner_email: string;
  provisioned_password: boolean;
  invite_email_sent: boolean;
  comp: { months: number; expires_at: string } | null;
}

// Operator-driven account create.  Replaces the curl/SQL workflow that
// used to bootstrap a new customer.  The form intentionally lives in a
// modal — operators tend to be in the middle of triaging another
// account when a "create one for X" request lands, so we don't want a
// full route navigation.
export default function NewAccountModal({ onClose, onCreated }: Props) {
  const [companyName, setCompanyName] = useState('');
  const [ownerEmail, setOwnerEmail] = useState('');
  const [ownerName, setOwnerName] = useState('');
  // Operator picks ONE of two onboarding modes per account:
  //   "invite" → backend emails a password-set link, operator never
  //              sees the credential.  Default; safest for shared
  //              ownership and matches Stripe / GitHub conventions.
  //   "password" → operator types the initial password.  Used for hand-
  //              off scenarios where the owner can't receive email
  //              immediately (e.g., onboarding during a phone call).
  const [mode, setMode] = useState<'invite' | 'password'>('invite');
  const [password, setPassword] = useState('');

  const [compEnabled, setCompEnabled] = useState(false);
  const [compMonths, setCompMonths] = useState(3);
  const [compReason, setCompReason] = useState('');

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr('');
    if (!companyName.trim() || !ownerEmail.trim()) {
      setErr('Company name and owner email are required.');
      return;
    }
    if (mode === 'password' && password.length < 8) {
      setErr('Password must be at least 8 characters.');
      return;
    }
    setBusy(true);
    try {
      const r = await apiJSON<CreateResponse>('/system/accounts', {
        method: 'POST',
        body: {
          company_name: companyName.trim(),
          owner_email: ownerEmail.trim(),
          owner_display_name: ownerName.trim(),
          password: mode === 'password' ? password : null,
          grant_comp: compEnabled
            ? { months: compMonths, reason: compReason.trim() }
            : null,
        },
      });
      onCreated(r.account_id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to create account');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
         onClick={onClose}>
      <form
        onSubmit={submit}
        onClick={(e) => e.stopPropagation()}
        className="bg-slate-900 border border-slate-700 rounded-lg w-full max-w-lg p-6 space-y-4"
      >
        <div>
          <h2 className="text-base font-semibold text-slate-100">Create new account</h2>
          <p className="text-xs text-slate-500 mt-1">
            Bootstraps a tenant account + an owner user.  The owner gets an email
            with a password-set link unless you provide a password below.
          </p>
        </div>

        <Field label="Company name" required>
          <input
            type="text"
            value={companyName}
            onChange={(e) => setCompanyName(e.target.value)}
            placeholder="ACME Trucking Inc."
            className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm"
            autoFocus
          />
        </Field>

        <Field label="Owner email" required>
          <input
            type="email"
            value={ownerEmail}
            onChange={(e) => setOwnerEmail(e.target.value)}
            placeholder="owner@example.com"
            className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm"
          />
        </Field>

        <Field label="Owner display name">
          <input
            type="text"
            value={ownerName}
            onChange={(e) => setOwnerName(e.target.value)}
            placeholder="Defaults to the email's local part"
            className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm"
          />
        </Field>

        {/* Onboarding mode toggle */}
        <div>
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1.5">
            Owner first-login
          </div>
          <div className="flex gap-2">
            <label className={`flex-1 cursor-pointer rounded border px-3 py-2 text-xs ${
              mode === 'invite'
                ? 'border-accent/60 bg-accent/10 text-accent'
                : 'border-slate-700 text-slate-400 hover:border-slate-600'}`}>
              <input
                type="radio"
                checked={mode === 'invite'}
                onChange={() => setMode('invite')}
                className="sr-only"
              />
              <div className="font-semibold mb-0.5">Email invite (default)</div>
              <div className="text-slate-500">Owner picks their own password via emailed link.</div>
            </label>
            <label className={`flex-1 cursor-pointer rounded border px-3 py-2 text-xs ${
              mode === 'password'
                ? 'border-accent/60 bg-accent/10 text-accent'
                : 'border-slate-700 text-slate-400 hover:border-slate-600'}`}>
              <input
                type="radio"
                checked={mode === 'password'}
                onChange={() => setMode('password')}
                className="sr-only"
              />
              <div className="font-semibold mb-0.5">Set password now</div>
              <div className="text-slate-500">You type the initial password and share it out-of-band.</div>
            </label>
          </div>
        </div>

        {mode === 'password' && (
          <Field label="Initial password (min 8 chars)" required>
            <input
              type="text"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Owner can change after first login"
              className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm font-mono"
            />
          </Field>
        )}

        {/* Comp grant — optional sales sweetener */}
        <div className="border-t border-slate-800 pt-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={compEnabled}
              onChange={(e) => setCompEnabled(e.target.checked)}
              className="rounded border-slate-700"
            />
            <span className="text-sm text-slate-200">Grant comp (free months)</span>
          </label>
          {compEnabled && (
            <div className="mt-2 pl-6 space-y-2">
              <div className="flex gap-2 items-center">
                <select
                  value={compMonths}
                  onChange={(e) => setCompMonths(Number(e.target.value))}
                  className="bg-slate-950 border border-slate-700 rounded px-2 py-1 text-sm"
                >
                  <option value={1}>1 month</option>
                  <option value={3}>3 months</option>
                  <option value={6}>6 months</option>
                  <option value={12}>12 months</option>
                </select>
                <span className="text-xs text-slate-500">comp</span>
              </div>
              <input
                type="text"
                value={compReason}
                onChange={(e) => setCompReason(e.target.value)}
                placeholder="Reason (visible in audit log)"
                className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm"
              />
            </div>
          )}
        </div>

        {err && (
          <div className="bg-danger/10 border border-danger/40 text-danger text-xs rounded px-3 py-2">
            {err}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} disabled={busy}
                  className="text-xs px-3 py-1.5 text-slate-400 hover:text-slate-200">
            Cancel
          </button>
          <button type="submit" disabled={busy}
                  className="bg-accent text-white text-xs px-4 py-1.5 rounded hover:bg-accent/90 disabled:opacity-50">
            {busy ? 'Creating…' : 'Create account'}
          </button>
        </div>
      </form>
    </div>
  );
}

function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">
        {label} {required && <span className="text-danger">*</span>}
      </div>
      {children}
    </div>
  );
}
