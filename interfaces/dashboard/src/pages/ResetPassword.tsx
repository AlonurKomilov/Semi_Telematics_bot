import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, AlertTriangle } from 'lucide-react';
import { apiJSON } from '../api/client';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';

/**
 * Token-based password reset completion page.
 *
 * Reads ``?token=...`` from the URL (the link the email points at),
 * collects a new password + confirmation, posts to
 * ``/auth/reset-password``.  On success we redirect to the login
 * screen so the user proves the new credentials work.  The endpoint
 * does NOT auto-sign-them-in.
 */
export default function ResetPassword() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get('token') || '';

  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);

  // Local pre-validation matches the server policy exactly so the user
  // never sees a generic 422 — they get the specific rule before the
  // network round-trip.
  const passwordIssues = useMemo(() => {
    const out: string[] = [];
    if (password.length < 8) out.push(t('auth.pwd_min_length'));
    else if (!/[A-Za-z]/.test(password)) out.push(t('auth.pwd_needs_letter'));
    else if (!/[0-9]/.test(password)) out.push(t('auth.pwd_needs_digit'));
    return out;
  }, [password, t]);

  const mismatched = confirm !== '' && confirm !== password;
  const canSubmit = (
    !!token && !!password && !!confirm
    && !mismatched && passwordIssues.length === 0
    && !submitting
  );

  // If we land here without a token (someone manually visited the
  // URL), short-circuit to the request page rather than render a form
  // that will always 400.
  useEffect(() => {
    if (!token) navigate('/forgot-password', { replace: true });
  }, [token, navigate]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      await apiJSON('/auth/reset-password', {
        method: 'POST',
        body: { token, password },
      });
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('auth.reset_failed'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-background px-4">
      <div className="mb-8 text-center">
        <h1 className="text-4xl font-bold mb-2 text-foreground">4truck</h1>
        <p className="text-muted-foreground">{t('auth.reset_title')}</p>
      </div>

      <div className="bg-card rounded-xl p-8 shadow-lg border border-border w-full max-w-sm">
        {done ? (
          <div className="space-y-4 text-center">
            <div className="mx-auto w-12 h-12 rounded-full bg-ok-bg flex items-center justify-center">
              <CheckCircle2 className="text-ok" size={20} />
            </div>
            <h2 className="text-lg font-semibold">{t('auth.reset_success_title')}</h2>
            <p className="text-sm text-muted-foreground">
              {t('auth.reset_success_body')}
            </p>
            <Link
              to="/login"
              className="inline-block w-full bg-primary text-primary-foreground rounded-md py-2 text-sm font-medium hover:bg-primary/90"
            >
              {t('auth.go_to_login')}
            </Link>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <h2 className="text-lg font-semibold">{t('auth.reset_heading')}</h2>
            <p className="text-sm text-muted-foreground">
              {t('auth.reset_helper')}
            </p>

            <Input
              type="password"
              placeholder={t('auth.new_password')}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              autoFocus
            />
            <Input
              type="password"
              placeholder={t('auth.confirm_password')}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
            />

            {passwordIssues.length > 0 && password.length > 0 && (
              <ul className="text-xs text-muted-foreground space-y-0.5 list-disc list-inside">
                {passwordIssues.map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            )}
            {mismatched && (
              <p className="text-xs text-danger">
                {t('auth.pwd_mismatch')}
              </p>
            )}
            {error && (
              <p className="text-xs text-danger inline-flex items-center gap-1">
                <AlertTriangle size={12} />
                {error}
              </p>
            )}

            <Button
              type="submit"
              disabled={!canSubmit}
              className="w-full"
            >
              {submitting ? '...' : t('auth.reset_submit')}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
