import { useEffect, useState } from 'react';
import { Mail, X } from 'lucide-react';
import { apiJSON } from '../api/client';
import { useAuth } from '../context/AuthContext';

/**
 * Surfaces an "invite waiting for you" banner when an authenticated
 * user lands on the dashboard with ``?pending_invite=<code>``.
 *
 * Real scenario this closes: an existing 4truck user with an active
 * session clicks an invite email for a DIFFERENT account (fleet
 * owner invited to a customer's account; cross-customer accountant
 * invited to a new client).  App.tsx forwards them here with the
 * code as a query param so we can show "you're signed in to X but
 * this invite is for Y — sign out to accept" instead of silently
 * dropping the link.
 *
 * Preview comes from the same public ``/auth/invite-preview``
 * endpoint Login.tsx uses (rate-limited, uniform 404 on missing/
 * expired/used/revoked).  Sign-out + redirect back to ``/signup/<code>``
 * is the recovery path — they re-land on the unauthenticated Login
 * page with the code pre-filled, ready to register fresh.
 */
export default function PendingInviteBanner() {
  const { logout } = useAuth();
  const [code, setCode] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [preview, setPreview] = useState<{
    account_name: string;
    role_label: string;
    inviter_display_name: string;
  } | null>(null);

  // Read the query param once on mount.  Don't strip it from the
  // URL — the operator may refresh and we want the banner back.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const c = params.get('pending_invite');
    if (c) setCode(c);
  }, []);

  useEffect(() => {
    if (!code) { setPreview(null); return; }
    let cancelled = false;
    (async () => {
      try {
        const data = await apiJSON<{
          account_name: string;
          role_label: string;
          inviter_display_name: string;
        }>(`/auth/invite-preview?code=${encodeURIComponent(code)}`);
        if (!cancelled) setPreview(data);
      } catch {
        // 404 (uniform "not available") — no banner, no fuss.
        if (!cancelled) setPreview(null);
      }
    })();
    return () => { cancelled = true; };
  }, [code]);

  if (!code || dismissed || !preview) return null;

  const handleSignOutAndAccept = async () => {
    try {
      await logout();
    } catch { /* logout best-effort */ }
    // Redirect to the path-segment URL so Login.tsx pre-fills the
    // code from the path (the same flow as a fresh email click).
    window.location.href = `/signup/${encodeURIComponent(code)}`;
  };

  return (
    <div className="bg-primary/10 border-b border-primary/30 px-4 py-2.5 flex items-center gap-3 text-sm">
      <Mail size={16} className="text-primary flex-shrink-0" />
      <div className="flex-1 min-w-0">
        <span className="text-foreground">
          <strong>{preview.inviter_display_name}</strong> invited you to{' '}
          <strong>{preview.account_name}</strong> as a{' '}
          <strong>{preview.role_label}</strong>.
        </span>
        <span className="text-muted-foreground ml-2">
          You're signed in to a different account.
        </span>
      </div>
      <button
        type="button"
        onClick={handleSignOutAndAccept}
        className="bg-primary text-primary-foreground rounded px-3 py-1 text-xs font-medium hover:bg-primary/90 transition"
      >
        Sign out & accept
      </button>
      <button
        type="button"
        onClick={() => setDismissed(true)}
        className="text-muted-foreground hover:text-foreground"
        aria-label="Dismiss"
      >
        <X size={14} />
      </button>
    </div>
  );
}
