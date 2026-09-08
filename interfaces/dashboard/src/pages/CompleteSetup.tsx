/**
 * The second half of a Google company sign-up.
 *
 * Google gave us a verified email and nothing else.  Before this
 * account can be signed in to — by any method — its owner chooses a
 * password and names the company.  The page holds a fifteen-minute
 * setup token (sessionStorage, never localStorage: it is not a session
 * and must not survive the tab), and the API refuses that token
 * everywhere but here.  Success mints the real session and the trial
 * starts; leaving means signing in with Google again, which brings the
 * person straight back.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Card } from '@/components/ui/card';
import { apiJSON, setToken } from '../api/client';
import { toneClasses, toneText } from '../lib/status';
import { clearSetupHandoff, readSetupHandoff } from '../lib/setupHandoff';

export default function CompleteSetup() {
  const { t } = useTranslation();
  const [{ token, email }] = useState(readSetupHandoff);
  const [companyName, setCompanyName] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!token) window.location.replace('/login?mode=register&kind=new-company');
  }, [token]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (password !== confirm) { setError(t('setup.pw_mismatch', "Passwords don't match.")); return; }
    if (password.length < 8 || !/[A-Za-z]/.test(password) || !/\d/.test(password)) {
      setError(t('setup.pw_rule', 'Password must be at least 8 characters and include one letter and one digit.'));
      return;
    }
    setSaving(true);
    try {
      const res = await apiJSON<{ access_token: string }>('/auth/complete-setup', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: { company_name: companyName.trim(), password, display_name: displayName.trim() },
      });
      clearSetupHandoff();
      setToken(res.access_token, false);
      window.location.replace('/');
    } catch (err) {
      const msg = err instanceof Error ? err.message : '';
      // A setup token that expired (15 min) or was already used: the
      // way back is one Google click on the login page.
      if (/expired|invalid|not a setup token|revoked/i.test(msg)) {
        setError(t('setup.token_expired', 'This setup link has expired. Sign in with Google again to continue where you left off.'));
      } else {
        setError(msg || t('setup.failed', 'Could not finish setup. Please try again.'));
      }
    } finally {
      setSaving(false);
    }
  };

  if (!token) return null;
  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-background px-4">
      <div className="mb-8 text-center">
        <h1 className="text-4xl font-bold mb-2 text-foreground">4truck</h1>
        <p className="text-muted-foreground">{t('setup.tagline', 'One more step, and your company is ready.')}</p>
      </div>
      <Card className="w-full max-w-md p-6">
        <h2 className="text-lg font-semibold mb-1">{t('setup.title', 'Set up your company')}</h2>
        <p className="text-sm text-muted-foreground mb-5">
          {t('setup.intro', 'Signed in with Google as')} <span className="font-medium text-foreground">{email}</span>.{' '}
          {t('setup.intro_2', 'Choose a password for this account and name your company. Nothing else is needed today.')}
        </p>
        <form onSubmit={submit} className="space-y-4">
          <label className="block space-y-1">
            <span className="text-sm">{t('setup.company_name', 'Company name')}</span>
            <Input value={companyName} onChange={(e) => setCompanyName(e.target.value)} required minLength={2} maxLength={100} autoFocus />
          </label>
          <label className="block space-y-1">
            <span className="text-sm">{t('setup.display_name', 'Your name')} <span className="text-muted-foreground">({t('setup.optional', 'optional')})</span></span>
            <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={100} />
          </label>
          <label className="block space-y-1">
            <span className="text-sm">{t('setup.password', 'Password')}</span>
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} autoComplete="new-password" />
          </label>
          <label className="block space-y-1">
            <span className="text-sm">{t('setup.confirm', 'Confirm password')}</span>
            <Input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required minLength={8} autoComplete="new-password" />
          </label>
          {error && (
            <p className={`text-sm px-3 py-2 rounded-md border ${toneClasses('danger')} ${toneText('danger')}`}>{error}</p>
          )}
          <Button type="submit" className="w-full" disabled={saving}>
            {saving ? '…' : t('setup.submit', 'Finish and open my dashboard')}
          </Button>
        </form>
        <p className="text-xs text-muted-foreground mt-4">
          {t('setup.password_why', 'The password lets you sign in even when Google is unavailable, and lets you disconnect Google later without losing access.')}
        </p>
      </Card>
    </div>
  );
}
