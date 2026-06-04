import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import {
  Cloud, CloudOff, HardDrive, AlertTriangle, Loader2,
  CheckCircle2, RefreshCcw,
} from 'lucide-react';
import { apiJSON } from '../../api/client';
import { toneClasses, toneText, type Tone } from '../../lib/status';

/**
 * Top-of-page status card.  Single source of truth for "what backend
 * is active right now and is anything wrong".  Renders differently
 * per backend so the user never sees a metric that doesn't apply to
 * their setup:
 *
 *   disk    → backend chip + plain explanation, no counters or gauge
 *   gdrive  → backend chip + Drive connection, no counters or gauge
 *   hybrid  → full picture: quota gauge + sync counters + Drive chip
 */

interface HealthResponse {
  backend: 'disk' | 'gdrive' | 'hybrid' | string;
  drive: { connected: boolean; email: string | null };
  quota: { used_bytes: number; quota_bytes: number; percent: number };
  queue: { pending: number; stuck: number };
  media: { local: number; syncing: number; remote: number; stuck: number };
}

const POLL_MS = 15_000;

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function quotaTone(pct: number): Tone {
  if (pct >= 95) return 'danger';
  if (pct >= 80) return 'warn';
  return 'ok';
}

export default function StorageHealthCard() {
  const { t } = useTranslation();
  const { data, isLoading } = useQuery<HealthResponse>({
    queryKey: ['storage-health'],
    queryFn: () => apiJSON<HealthResponse>('/storage/health'),
    refetchInterval: POLL_MS,
    placeholderData: (prev) => prev,
  });

  if (isLoading && !data) {
    return (
      <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground inline-flex items-center gap-2">
        <Loader2 size={14} className="animate-spin" />
        {t('storage.status.loading')}
      </div>
    );
  }
  if (!data) return null;

  const { backend, drive, quota, queue, media } = data;
  const tone = quotaTone(quota.percent);
  const quotaBar = tone === 'danger' ? 'bg-danger' : tone === 'warn' ? 'bg-warn' : 'bg-ok';
  const isHybrid = backend === 'hybrid';
  const isGDrive = backend === 'gdrive';

  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-3">
      {/* ── Header: title + backend pill ───────────────────────── */}
      <div className="flex items-center gap-2 flex-wrap">
        <h3 className="text-sm font-semibold">{t('storage.status.title')}</h3>
        <BackendPill backend={backend} />
        {drive.connected && (
          <span className="ml-auto inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <CheckCircle2 size={14} className="text-ok" />
            {t('storage.status.drive_connected_as', { email: drive.email ?? '—' })}
          </span>
        )}
        {!drive.connected && (isGDrive || isHybrid) && (
          <span className="ml-auto inline-flex items-center gap-1.5 text-xs text-warn">
            <AlertTriangle size={14} />
            {t('storage.status.drive_token_expired')}
          </span>
        )}
        {!drive.connected && !isGDrive && !isHybrid && (
          <span className="ml-auto inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <CloudOff size={14} />
            {t('storage.status.drive_not_connected')}
          </span>
        )}
      </div>

      {/* ── Per-backend body ──────────────────────────────────── */}
      {!isHybrid && (
        <p className="text-xs text-muted-foreground leading-relaxed">
          {isGDrive
            ? t('storage.status.gdrive_note')
            : t('storage.status.disk_note')}
        </p>
      )}

      {isHybrid && (
        <>
          {/* Quota gauge */}
          <div>
            <div className="flex items-center justify-between text-xs mb-1">
              <span className="text-muted-foreground inline-flex items-center gap-1.5">
                <HardDrive size={14} />
                {t('storage.status.local_cache')}
              </span>
              <span className={`tabular-nums font-medium ${toneText(tone)}`}>
                {formatBytes(quota.used_bytes)} / {formatBytes(quota.quota_bytes)} ({quota.percent.toFixed(0)} %)
              </span>
            </div>
            <div className="h-2 rounded-full bg-muted overflow-hidden">
              <div
                className={`${quotaBar} h-full transition-all duration-500`}
                style={{ width: `${Math.min(100, quota.percent)}%` }}
              />
            </div>
          </div>

          {/* Sync counters */}
          <div className="grid grid-cols-3 gap-3 pt-1">
            <CountChip
              icon={<Cloud size={16} />}
              label={t('storage.status.synced')}
              value={media.remote}
              tone={toneClasses('ok')}
            />
            <CountChip
              icon={<RefreshCcw size={16} />}
              label={t('storage.status.pending')}
              value={queue.pending}
              tone={toneClasses('info')}
            />
            <CountChip
              icon={<AlertTriangle size={16} />}
              label={t('storage.status.stuck')}
              value={queue.stuck}
              tone={toneClasses(queue.stuck > 0 ? 'danger' : 'neutral')}
            />
          </div>

          {queue.stuck > 0 && drive.connected && (
            <p className="text-2xs text-warn inline-flex items-center gap-1.5 pt-1">
              <AlertTriangle size={12} />
              {t('storage.status.stuck_hint')}
            </p>
          )}
        </>
      )}
    </div>
  );
}

function BackendPill({ backend }: { backend: string }) {
  const { t } = useTranslation();
  const palette: Record<string, { cls: string; icon: JSX.Element; label: string }> = {
    disk: {
      cls: 'bg-muted text-muted-foreground border-border',
      icon: <HardDrive size={12} />,
      label: t('storage.badge_disk'),
    },
    gdrive: {
      cls: 'bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30',
      icon: <Cloud size={12} />,
      label: t('storage.badge_gdrive'),
    },
    hybrid: {
      cls: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/30',
      icon: <RefreshCcw size={12} />,
      label: t('storage.badge_hybrid'),
    },
  };
  const p = palette[backend] ?? palette.disk;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border ${p.cls}`}>
      {p.icon}
      {p.label}
    </span>
  );
}

function CountChip({ icon, label, value, tone }: {
  icon: JSX.Element; label: string; value: number; tone: string;
}) {
  return (
    <div className={`rounded-md ${tone} p-3 flex flex-col`}>
      <span className="inline-flex items-center gap-1.5 text-xl font-bold tabular-nums leading-none">
        <span aria-hidden>{icon}</span>
        {value.toLocaleString()}
      </span>
      <span className="text-2xs mt-1 opacity-90">{label}</span>
    </div>
  );
}
