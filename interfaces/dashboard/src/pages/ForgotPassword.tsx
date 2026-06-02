import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Mail, ArrowLeft } from 'lucide-react';
import { apiJSON } from '../api/client';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';

/**
 * Password-reset request page.
 *
 * Submits the email to ``POST /auth/forgot-password``.  The endpoint
 * always returns 200 regardless of whether the email is registered,
 * so the success view doesn't reveal account existence to a probing
 * attacker — we always say "if an account exists, a link is on its
 * way", never "no such email."
 */
export default function ForgotPassword() {
  const { t } = useTranslation();
  const [email, setEmail] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      await apiJSON('/auth/forgot-password', {
        method: 'POST',
        body: { email },
      });
      setSubmitted(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('auth.forgot_failed'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-background px-4">
      <div className="mb-8 text-center">
        <h1 className="text-4xl font-bold mb-2 text-foreground">4truck</h1>
        <p className="text-muted-foreground">{t('auth.forgot_title')}</p>
      </div>

      <div className="bg-card rounded-xl p-8 shadow-lg border border-border w-full max-w-sm">
        {submitted ? (
          <div className="space-y-4 text-center">
            <div className="mx-auto w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center">
              <Mail className="text-primary" size={20} />
            </div>
            <h2 className="text-lg font-semibold">{t('auth.forgot_sent_title')}</h2>
            <p className="text-sm text-muted-foreground">
              {t('auth.forgot_sent_body')}
            </p>
            <Link
              to="/login"
              className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
            >
              <ArrowLeft size={13} />
              {t('auth.back_to_login')}
            </Link>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <h2 className="text-lg font-semibold">{t('auth.forgot_heading')}</h2>
            <p className="text-sm text-muted-foreground">
              {t('auth.forgot_helper')}
            </p>
            <Input
              type="email"
              placeholder={t('auth.email')}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
            />
            {error && <p className="text-destructive text-xs">{error}</p>}
            <Button
              type="submit"
              disabled={submitting || !email}
              className="w-full"
            >
              {submitting ? '...' : t('auth.send_reset_link')}
            </Button>
            <div className="pt-2 text-center">
              <Link
                to="/login"
                className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
              >
                <ArrowLeft size={11} />
                {t('auth.back_to_login')}
              </Link>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
