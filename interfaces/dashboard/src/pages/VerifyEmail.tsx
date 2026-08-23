import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, AlertTriangle, Loader2 } from 'lucide-react';
import { apiJSON } from '../api/client';

/**
 * Email-verification landing page.
 *
 * The user clicks the link in their inbox → arrives here with
 * ``?token=...``.  We POST it to ``/auth/verify-email``, which marks
 * the user verified.  Then we render a success view that points back
 * to the login screen.
 *
 * Verification is a one-time operation; re-redeeming the same token
 * returns the same error message as an unknown token, so we don't
 * leak that THIS particular token was ever valid.
 */
type Status = 'verifying' | 'success' | 'error';

export default function VerifyEmail() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const token = params.get('token') || '';
  const [status, setStatus] = useState<Status>('verifying');
  const [errorMessage, setErrorMessage] = useState('');

  useEffect(() => {
    if (!token) {
      setStatus('error');
      setErrorMessage(t('auth.verify_no_token'));
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        await apiJSON(`/auth/verify-email?token=${encodeURIComponent(token)}`);
        if (!cancelled) setStatus('success');
      } catch (err) {
        if (!cancelled) {
          setStatus('error');
          setErrorMessage(
            err instanceof Error ? err.message : t('auth.verify_failed'),
          );
        }
      }
    })();
    return () => { cancelled = true; };
  }, [token, t]);

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-background px-4">
      <div className="mb-8 text-center">
        <h1 className="text-4xl font-bold mb-2 text-foreground">4truck</h1>
        <p className="text-muted-foreground">{t('auth.verify_title')}</p>
      </div>

      <div className="bg-card rounded-xl p-8 shadow-lg border border-border w-full max-w-sm text-center space-y-4">
        {status === 'verifying' && (
          <>
            <div className="mx-auto w-12 h-12 rounded-full bg-muted flex items-center justify-center">
              <Loader2 className="animate-spin text-muted-foreground size-5" />
            </div>
            <h2 className="text-lg font-semibold">{t('auth.verify_in_progress')}</h2>
          </>
        )}
        {status === 'success' && (
          <>
            <div className="mx-auto w-12 h-12 rounded-full bg-ok-bg flex items-center justify-center">
              <CheckCircle2 className="text-ok size-5" />
            </div>
            <h2 className="text-lg font-semibold">{t('auth.verify_success_title')}</h2>
            <p className="text-sm text-muted-foreground">
              {t('auth.verify_success_body')}
            </p>
            <Link
              to="/login"
              className="inline-block w-full bg-primary text-primary-foreground rounded-md py-2 text-sm font-medium hover:bg-primary/90"
            >
              {t('auth.go_to_login')}
            </Link>
          </>
        )}
        {status === 'error' && (
          <>
            <div className="mx-auto w-12 h-12 rounded-full bg-warn-bg flex items-center justify-center">
              <AlertTriangle className="text-warn size-5" />
            </div>
            <h2 className="text-lg font-semibold">{t('auth.verify_failed_title')}</h2>
            <p className="text-sm text-muted-foreground">{errorMessage}</p>
            <Link
              to="/login"
              className="inline-block w-full bg-muted hover:bg-muted/80 text-foreground/80 rounded-md py-2 text-sm font-medium"
            >
              {t('auth.back_to_login')}
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
