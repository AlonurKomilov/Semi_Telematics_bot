import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import {
  HardDrive, Cloud, RefreshCcw, ExternalLink, Unlink, CheckCircle2,
  AlertTriangle, Loader2,
} from 'lucide-react';
import { apiJSON } from '../../api/client';

/**
 * Storage settings card — backend chooser + Google Drive connection.
 *
 * Three backends as radio-style cards:
 *  - disk    (Local only)
 *  - gdrive  (Google Drive only)
 *  - hybrid  (Disk → Drive, recommended)
 *
 * gdrive/hybrid require a connected Drive; the buttons are disabled
 * (with a "Connect Drive first" hint) when the token isn't present.
 *
 * Drive whole-quota progress bar at the bottom — only rendered when
 * connected, since it has no meaning otherwise.
 */

type Backend = 'disk' | 'gdrive' | 'hybrid';

interface StorageConfig {
  backend: Backend | string;
  gdrive: {
    connected: boolean;
    user_email: string | null;
    root_folder_id: string | null;
  };
  usage: {
    used_by_app_bytes: number;
    drive_quota: {
      limit_bytes: number | null;
      usage_bytes: number | null;
      usage_in_drive_bytes: number | null;
    } | null;
  };
}

function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '—';
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const v = bytes / Math.pow(1024, i);
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

const ERROR_MESSAGES: Record<string, string> = {
  expired: 'The connection request expired. Please try again.',
  oauthlib_missing: 'Server is missing google-auth-oauthlib. Contact the administrator.',
  token_exchange: 'Google rejected the authorization code. Please try again.',
  no_refresh_token: 'Google did not return a refresh token. Revoke the prior grant in your Google account and retry.',
  drive_setup: 'Could not create the root folder on your Drive. Check permissions and retry.',
  access_denied: 'You declined the consent prompt.',
};

export default function StorageBackendCard() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [switching, setSwitching] = useState<Backend | null>(null);

  const { data, isLoading, error } = useQuery<StorageConfig>({
    queryKey: ['storage-config'],
    queryFn: () => apiJSON<StorageConfig>('/storage/config'),
  });

  useEffect(() => {
    if (params.get('gdrive_connected') === '1') {
      toast.success(t('storage.connect_success'));
      qc.invalidateQueries({ queryKey: ['storage-config'] });
      qc.invalidateQueries({ queryKey: ['storage-health'] });
      params.delete('gdrive_connected');
      setParams(params, { replace: true });
    }
    const errCode = params.get('gdrive_error');
    if (errCode) {
      toast.error(ERROR_MESSAGES[errCode] || `Connection failed: ${errCode}`);
      params.delete('gdrive_error');
      setParams(params, { replace: true });
    }
  }, [params, setParams, qc, t]);

  const handleConnect = async () => {
    setConnecting(true);
    try {
      const res = await apiJSON<{ authorize_url: string }>(
        '/storage/google/start', { method: 'POST' },
      );
      window.location.href = res.authorize_url;
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not start Google OAuth');
      setConnecting(false);
    }
  };

  const handleDisconnect = async () => {
    if (!window.confirm(
      'Disconnect Google Drive?\n\n'
      + 'The account will revert to local storage. Files already on '
      + 'your Drive stay where they are — you can keep, delete, or '
      + 'move them anytime.',
    )) return;
    setDisconnecting(true);
    try {
      await apiJSON('/storage/google/disconnect', { method: 'POST' });
      toast.success(t('storage.disconnect_success'));
      qc.invalidateQueries({ queryKey: ['storage-config'] });
      qc.invalidateQueries({ queryKey: ['storage-health'] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Disconnect failed');
    } finally {
      setDisconnecting(false);
    }
  };

  const handleSwitch = async (backend: Backend) => {
    setSwitching(backend);
    try {
      await apiJSON('/storage/backend', {
        method: 'POST',
        body: { backend },
      });
      toast.success(t('storage.settings.switch_done', { backend }));
      qc.invalidateQueries({ queryKey: ['storage-config'] });
      qc.invalidateQueries({ queryKey: ['storage-health'] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('storage.settings.switch_failed'));
    } finally {
      setSwitching(null);
    }
  };

  if (isLoading) {
    return (
      <div className="bg-card border border-border rounded-lg p-4">
        <p className="text-sm text-muted-foreground inline-flex items-center gap-2">
          <Loader2 size={14} className="animate-spin" />
          {t('storage.loading')}
        </p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-card border border-border rounded-lg p-4">
        <div className="inline-flex items-center gap-2 text-sm text-destructive">
          <AlertTriangle size={14} />
          {error instanceof Error ? error.message : 'Could not load storage settings'}
        </div>
      </div>
    );
  }

  const cfg = data!;
  const driveConnected = cfg.gdrive.connected;
  const activeBackend = (cfg.backend as Backend) || 'disk';

  return (
    <div className="bg-card border border-border rounded-lg p-4 space-y-5">
      <h3 className="text-sm font-semibold">{t('storage.settings.title')}</h3>

      {/* ── Drive connection block (single source) ───────────── */}
      <section>
        <div className="flex items-start gap-3">
          <Cloud size={18} className={driveConnected ? 'text-blue-500 mt-0.5' : 'text-muted-foreground mt-0.5'} />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium">{t('storage.settings.drive_section')}</p>
            {driveConnected ? (
              <p className="text-xs text-muted-foreground mt-0.5">
                {t('storage.status.drive_connected_as', { email: cfg.gdrive.user_email ?? '—' })}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
                {t('storage.settings.drive_help')}
              </p>
            )}
            {driveConnected && (
              <p className="text-[11px] text-muted-foreground mt-1.5">
                {t('storage.settings.drive_folder')} · {t('storage.settings.drive_scope')}
              </p>
            )}
          </div>
          <div className="shrink-0">
            {driveConnected ? (
              <button
                type="button"
                onClick={handleDisconnect}
                disabled={disconnecting}
                className="inline-flex items-center gap-1 px-2.5 py-1 text-xs rounded-md border border-border hover:bg-muted disabled:opacity-50"
              >
                {disconnecting ? <Loader2 size={11} className="animate-spin" /> : <Unlink size={11} />}
                {disconnecting ? t('storage.disconnecting') : t('storage.disconnect_button')}
              </button>
            ) : (
              <button
                type="button"
                onClick={handleConnect}
                disabled={connecting}
                className="inline-flex items-center gap-1.5 px-3 py-1 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-xs font-medium text-primary-foreground transition"
              >
                {connecting ? <Loader2 size={11} className="animate-spin" /> : <ExternalLink size={11} />}
                {connecting ? t('storage.redirecting') : t('storage.connect_button')}
              </button>
            )}
          </div>
        </div>

        {/* Drive whole-quota progress bar — only when connected */}
        {driveConnected && <DriveQuotaBar usage={cfg.usage} />}
      </section>

      {/* ── Backend chooser ──────────────────────────────────── */}
      <section>
        <p className="text-sm font-medium mb-2">{t('storage.settings.backend_section')}</p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2.5">
          <BackendOption
            icon={<HardDrive size={16} />}
            title={t('storage.settings.backend_disk_title')}
            description={t('storage.settings.backend_disk_desc')}
            active={activeBackend === 'disk'}
            needsDrive={false}
            driveConnected={driveConnected}
            switching={switching === 'disk'}
            onSwitch={() => handleSwitch('disk')}
          />
          <BackendOption
            icon={<Cloud size={16} />}
            title={t('storage.settings.backend_gdrive_title')}
            description={t('storage.settings.backend_gdrive_desc')}
            active={activeBackend === 'gdrive'}
            needsDrive
            driveConnected={driveConnected}
            switching={switching === 'gdrive'}
            onSwitch={() => handleSwitch('gdrive')}
          />
          <BackendOption
            icon={<RefreshCcw size={16} />}
            title={t('storage.settings.backend_hybrid_title')}
            description={t('storage.settings.backend_hybrid_desc')}
            active={activeBackend === 'hybrid'}
            needsDrive
            driveConnected={driveConnected}
            switching={switching === 'hybrid'}
            onSwitch={() => handleSwitch('hybrid')}
            recommended
          />
        </div>
      </section>

      {/* ── Routing table ────────────────────────────────────── */}
      <RoutingTable connectedToDrive={driveConnected} />
    </div>
  );
}

function BackendOption({
  icon, title, description, active, needsDrive, driveConnected,
  switching, onSwitch, recommended,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  active: boolean;
  needsDrive: boolean;
  driveConnected: boolean;
  switching: boolean;
  onSwitch: () => void;
  recommended?: boolean;
}) {
  const { t } = useTranslation();
  const blocked = needsDrive && !driveConnected;
  return (
    <div className={`p-3 rounded-lg border flex flex-col gap-2 ${
      active ? 'border-primary/50 bg-primary/5' : 'border-border'
    }`}>
      <div className="flex items-start gap-2">
        <span className={active ? 'text-primary mt-0.5' : 'text-muted-foreground mt-0.5'}>{icon}</span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium inline-flex items-center gap-1.5">
            {title}
            {active && <CheckCircle2 size={12} className="text-primary" />}
            {recommended && !active && (
              <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-700 dark:text-emerald-400">
                ★
              </span>
            )}
          </p>
          <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
            {description}
          </p>
        </div>
      </div>
      {active ? (
        <span className="text-[11px] text-primary font-medium inline-flex items-center gap-1 self-start">
          <CheckCircle2 size={11} />
          {t('storage.settings.active')}
        </span>
      ) : blocked ? (
        <span className="text-[11px] text-muted-foreground inline-flex items-center gap-1 self-start">
          <AlertTriangle size={11} />
          {t('storage.settings.needs_drive')}
        </span>
      ) : (
        <button
          type="button"
          onClick={onSwitch}
          disabled={switching}
          className="self-start inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border border-border hover:bg-muted disabled:opacity-50"
        >
          {switching && <Loader2 size={11} className="animate-spin" />}
          {switching ? t('storage.settings.switching') : t('storage.settings.switch_button')}
        </button>
      )}
    </div>
  );
}

function RoutingTable({ connectedToDrive }: { connectedToDrive: boolean }) {
  const { t } = useTranslation();
  const driveItems = [
    'Work order invoices & shop photos',
    'Driver documents (CDL, medical card, training)',
    'PTI inspection photos & signatures',
    'Dashcam captures & parking-event maps',
  ];
  const platformItems = [
    'User profile avatars (small, platform-shared cache)',
  ];
  return (
    <section>
      <p className="text-sm font-medium mb-2">{t('storage.settings.routing_title')}</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="p-3 rounded-lg border border-border bg-muted/30">
          <p className="text-xs font-medium inline-flex items-center gap-1.5 mb-2">
            <Cloud size={12} className={connectedToDrive ? 'text-blue-500' : 'text-muted-foreground'} />
            {connectedToDrive
              ? t('storage.settings.routing_drive_header_connected')
              : t('storage.settings.routing_drive_header_disconnected')}
          </p>
          <ul className="text-[11px] text-muted-foreground space-y-0.5">
            {driveItems.map(s => (
              <li key={s} className="flex items-start gap-1.5">
                <CheckCircle2 size={10} className="text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                {s}
              </li>
            ))}
          </ul>
        </div>
        <div className="p-3 rounded-lg border border-border bg-muted/30">
          <p className="text-xs font-medium inline-flex items-center gap-1.5 mb-2">
            <HardDrive size={12} className="text-muted-foreground" />
            {t('storage.settings.routing_platform_header')}
          </p>
          <ul className="text-[11px] text-muted-foreground space-y-0.5">
            {platformItems.map(s => (
              <li key={s} className="flex items-start gap-1.5">
                <span className="text-muted-foreground/60 mt-0.5">•</span>
                {s}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

function DriveQuotaBar({ usage }: { usage: StorageConfig['usage'] }) {
  const usedByApp = usage.used_by_app_bytes;
  const quota = usage.drive_quota;
  const limit = quota?.limit_bytes ?? null;
  const userTotalUsed = quota?.usage_bytes ?? null;

  const pctTotal = (limit && userTotalUsed !== null)
    ? Math.min(100, (userTotalUsed / limit) * 100)
    : null;
  const pctApp = limit ? Math.min(100, (usedByApp / limit) * 100) : 0;
  const pctOther = pctTotal !== null ? Math.max(0, pctTotal - pctApp) : 0;

  const appBarColor = pctTotal === null
    ? 'bg-muted'
    : pctTotal >= 90 ? 'bg-red-500'
    : pctTotal >= 70 ? 'bg-orange-500'
    : 'bg-primary';

  if (pctTotal === null) {
    return (
      <p className="text-[11px] text-muted-foreground mt-3">
        Drive quota unavailable (unlimited plan or API hiccup).
      </p>
    );
  }

  return (
    <div className="mt-3">
      <div className="h-2 bg-muted rounded-full overflow-hidden flex">
        {pctOther > 0 && (
          <div
            className="h-full bg-muted-foreground/40 transition-all"
            style={{ width: `${pctOther}%` }}
            title={`Your other files: ${formatBytes((userTotalUsed ?? 0) - usedByApp)}`}
          />
        )}
        {pctApp > 0 && (
          <div
            className={`h-full ${appBarColor} transition-all`}
            style={{ width: `${pctApp}%` }}
            title={`4truck: ${formatBytes(usedByApp)}`}
          />
        )}
      </div>
      <div className="flex items-center justify-between text-[11px] text-muted-foreground mt-1.5">
        <span className="inline-flex items-center gap-2">
          <span className="inline-flex items-center gap-1">
            <span className={`inline-block w-2 h-2 rounded-sm ${appBarColor}`} />
            4truck: <span className="font-medium text-foreground tabular-nums">{formatBytes(usedByApp)}</span>
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block w-2 h-2 rounded-sm bg-muted-foreground/40" />
            Other: <span className="font-medium text-foreground tabular-nums">{formatBytes((userTotalUsed ?? 0) - usedByApp)}</span>
          </span>
        </span>
        <span className="tabular-nums">
          {formatBytes(userTotalUsed)} / {formatBytes(limit)} ({Math.round(pctTotal)}%)
        </span>
      </div>
    </div>
  );
}
