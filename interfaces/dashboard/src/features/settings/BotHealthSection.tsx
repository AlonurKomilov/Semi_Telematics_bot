/**
 * Delivery-health readout for the account bot — Settings → Telegram
 * Bot, owner-only (rides the card's CONNECTION half).
 *
 * Renders the last stored probe report (GET /admin/bot-health; the
 * backend re-probes daily and right after connect) and offers an
 * on-demand re-check.  Quiet when healthy — one line, no list; when a
 * surface is broken it names the surface and says what to do in task
 * language ("add the bot back to the group as admin"), because the
 * failures this catches are exactly the ones that otherwise die in a
 * server log the customer never sees.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, RefreshCw } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { toneClasses, toneText } from '../../lib/status';

interface HealthCheck {
  id: string;
  status: 'ok' | 'warn' | 'fail';
  username?: string;
  expected?: string;
  title?: string;
  live?: string;
  stored?: string;
  error?: string;
  member_status?: string;
}

interface HealthReport {
  checked_at: number;
  summary: 'ok' | 'warn' | 'fail' | 'none';
  checks: HealthCheck[];
}

/** Map a machine check id to its localized label + fix hint. */
function describe(
  c: HealthCheck,
  t: (k: string, o?: Record<string, unknown>) => string,
): { label: string; fix: string } {
  const parts = c.id.split(':');
  const bad = c.status !== 'ok';
  if (parts[0] === 'group' || parts[0] === 'subbot') {
    const kind = parts[2] ?? 'bot';
    const name = c.title || c.live || parts[1];
    if (kind === 'topic') {
      return {
        label: t('bot_health.row_topic', { topic: parts[3] ?? '' }),
        fix: bad ? t('bot_health.fix_topic') : '',
      };
    }
    if (kind === 'renamed') {
      return {
        label: t('bot_health.row_renamed', { name: c.live ?? '' }),
        fix: t('bot_health.fix_renamed'),
      };
    }
    if (parts[0] === 'subbot' && parts.length === 2) {
      return {
        label: t('bot_health.row_subbot', { persona: parts[1] }),
        fix: bad ? t('bot_health.fix_token') : '',
      };
    }
    return {
      label: t('bot_health.row_group', { name }),
      fix: bad ? t('bot_health.fix_member') : '',
    };
  }
  const fixes: Record<string, string> = {
    token: 'fix_token',
    webhook: 'fix_webhook',
    avatar: 'fix_avatar',
    menu: 'fix_autoset',
    branding: 'fix_autoset',
    bindings: 'fix_bindings',
  };
  return {
    label: t(`bot_health.row_${parts[0]}`),
    fix: bad && fixes[parts[0]] ? t(`bot_health.${fixes[parts[0]]}`) : '',
  };
}

export default function BotHealthSection({ hasBot }: { hasBot: boolean }) {
  const { t } = useTranslation();
  const [report, setReport] = useState<HealthReport | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    if (!hasBot) return;
    let cancelled = false;
    apiJSON<{ report: HealthReport | null }>('/admin/bot-health')
      .then(async (r) => {
        if (cancelled) return;
        if (r.report) { setReport(r.report); return; }
        // No stored report (fresh connect racing the background
        // probe, or Redis expiry): run one check instead of showing
        // "Not checked yet" and making the human do the pressing.
        setChecking(true);
        try {
          const fresh = await apiJSON<{ report: HealthReport }>(
            '/admin/bot-health/check', { method: 'POST' },
          );
          if (!cancelled) setReport(fresh.report);
        } catch { /* stays "Not checked yet"; the button remains */ }
        finally { if (!cancelled) setChecking(false); }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [hasBot]);

  const runCheck = async () => {
    setChecking(true);
    try {
      const r = await apiJSON<{ report: HealthReport }>(
        '/admin/bot-health/check', { method: 'POST' },
      );
      setReport(r.report);
    } catch {
      /* fetch errors leave the previous report visible */
    } finally {
      setChecking(false);
    }
  };

  if (!hasBot) return null;

  const problems = (report?.checks ?? []).filter((c) => c.status !== 'ok');
  const tone = report == null || report.summary === 'none'
    ? 'neutral'
    : report.summary === 'ok' ? 'ok'
    : report.summary === 'warn' ? 'warn' : 'danger';

  return (
    <div className="mb-4">
      <div className="mb-1.5 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
        {t('bot_health.title')}
      </div>
      <div className="flex items-center gap-3 flex-wrap mb-2">
        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium ${toneClasses(tone)}`}>
          {report == null
            ? t('bot_health.never_checked')
            : report.summary === 'ok'
              ? t('bot_health.all_ok', { count: report.checks.length })
              : report.summary === 'fail'
                ? t('bot_health.summary_fail')
                : t('bot_health.summary_warn')}
        </span>
        <button
          type="button"
          onClick={runCheck}
          disabled={checking}
          className="inline-flex items-center gap-1.5 h-8 px-3 border border-border hover:border-ring rounded text-xs font-medium text-foreground transition disabled:opacity-50"
        >
          {checking
            ? <Loader2 className="animate-spin size-3.5" />
            : <RefreshCw className="size-3.5" />}
          {checking ? t('bot_health.checking') : t('bot_health.check_now')}
        </button>
      </div>
      {problems.length > 0 && (
        <ul className="space-y-1.5">
          {problems.map((c) => {
            const d = describe(c, t);
            return (
              <li key={c.id} className="flex items-start gap-2 text-sm">
                <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${c.status === 'fail' ? 'bg-danger' : 'bg-warn'}`} />
                <span className="min-w-0">
                  <span className={`font-medium ${c.status === 'fail' ? toneText('danger') : toneText('warn')}`}>
                    {d.label}
                  </span>
                  {d.fix && (
                    <span className="block text-xs text-muted-foreground">{d.fix}</span>
                  )}
                  {c.error && (
                    <span className="block text-xs text-muted-foreground/70">{c.error}</span>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
