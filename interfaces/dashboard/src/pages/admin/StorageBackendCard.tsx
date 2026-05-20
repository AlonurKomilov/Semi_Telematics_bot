import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { HardDrive, Cloud, ExternalLink, Unlink, CheckCircle2, AlertTriangle } from 'lucide-react';
import { apiJSON } from '../../api/client';

// Response shape from GET /api/storage/config.  Mirrors the server
// dataclass — kept inline rather than promoted to the global types
// file since this card is the only consumer for now.
interface StorageConfig {
  backend: 'disk' | 'gdrive' | string;
  gdrive: {
    connected: boolean;
    user_email: string | null;
    root_folder_id: string | null;
  };
  usage: {
    /** Bytes uploaded by THIS app — sum of work_order_attachments.file_size
     *  for the account.  Authoritative for "what we've added to the user's
     *  storage". */
    used_by_app_bytes: number;
    /** Whole-Drive quota from Google.  ``null`` when not connected or when
     *  Google didn't return numbers (some unlimited / Workspace tiers). */
    drive_quota: {
      limit_bytes: number | null;
      usage_bytes: number | null;
      usage_in_drive_bytes: number | null;
    } | null;
  };
}

// Bytes → readable string ("187 MB", "1.4 GB").  Chosen so we don't
// pull in a 5KB ``filesize`` library for one function.  Matches the
// "binary" convention (1024) common to Drive's own UI.
function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '—';
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const v = bytes / Math.pow(1024, i);
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

// Maps the ?gdrive_error=... codes the OAuth callback redirects with
// to human-readable error messages.  Codes match the server-side
// keys in interfaces/api/routes/storage.py so the two surfaces stay
// in sync.
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

  // Pull current storage config.  This drives whether we render the
  // "Connect" CTA or the "Connected as user@…" status block.
  const { data, isLoading, error } = useQuery<StorageConfig>({
    queryKey: ['storage-config'],
    queryFn: () => apiJSON<StorageConfig>('/storage/config'),
  });

  // OAuth callback drops the user back at this page with a query
  // parameter — either ?gdrive_connected=1 (success) or
  // ?gdrive_error=<code>.  Surface as a toast and clean the URL so a
  // refresh doesn't re-toast.
  useEffect(() => {
    if (params.get('gdrive_connected') === '1') {
      toast.success('Google Drive connected.  Future attachments will be saved to your Drive.');
      qc.invalidateQueries({ queryKey: ['storage-config'] });
      params.delete('gdrive_connected');
      setParams(params, { replace: true });
    }
    const errCode = params.get('gdrive_error');
    if (errCode) {
      toast.error(ERROR_MESSAGES[errCode] || `Connection failed: ${errCode}`);
      params.delete('gdrive_error');
      setParams(params, { replace: true });
    }
  }, [params, setParams, qc]);

  const handleConnect = async () => {
    setConnecting(true);
    try {
      const res = await apiJSON<{ authorize_url: string }>(
        '/storage/google/start', { method: 'POST' },
      );
      // Full-page redirect rather than window.open — popups get
      // blocked by default in many browsers, and the redirect-back
      // experience is cleaner than juggling a child window.
      window.location.href = res.authorize_url;
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not start Google OAuth');
      setConnecting(false);
    }
  };

  const handleDisconnect = async () => {
    if (!window.confirm(
      'Disconnect Google Drive?\n\n'
      + 'New attachments will be saved to local storage.\n'
      + 'Files already on your Drive will stay where they are — '
      + 'you can keep them, delete them, or move them anytime.',
    )) return;
    setDisconnecting(true);
    try {
      await apiJSON('/storage/google/disconnect', { method: 'POST' });
      toast.success('Google Drive disconnected — back on local storage.');
      qc.invalidateQueries({ queryKey: ['storage-config'] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Disconnect failed');
    } finally {
      setDisconnecting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="bg-card border border-border rounded-xl p-5 mb-5">
        <p className="text-sm text-muted-foreground">Loading storage settings…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-card border border-border rounded-xl p-5 mb-5">
        <div className="inline-flex items-center gap-2 text-sm text-destructive">
          <AlertTriangle size={14} />
          {error instanceof Error ? error.message : 'Could not load storage settings'}
        </div>
      </div>
    );
  }

  const cfg = data!;
  const isGDrive = cfg.backend === 'gdrive' && cfg.gdrive.connected;

  return (
    <div className="bg-card border border-border rounded-xl p-5 mb-5">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h2 className="text-base font-semibold inline-flex items-center gap-2">
            {isGDrive ? <Cloud size={16} className="text-blue-500" /> : <HardDrive size={16} className="text-muted-foreground" />}
            Attachment Storage
          </h2>
          <p className="text-xs text-muted-foreground mt-1 max-w-prose">
            Where uploaded attachments and per-fleet captures are saved.
            Bring your own Google Drive to keep these bytes in your
            account and cut platform storage costs.  See the per-content
            breakdown below.
          </p>
        </div>
        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${
          isGDrive
            ? 'bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30'
            : 'bg-muted text-muted-foreground border-border/50'
        }`}>
          {isGDrive ? t('storage.badge_gdrive') : t('storage.badge_disk')}
        </span>
      </div>

      {/* Two backend cards side by side.  Active backend gets a check
          mark + accent border; the other shows its CTA. */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <BackendOption
          icon={<HardDrive size={18} />}
          name="Local storage"
          description="Default. Files saved on the platform server. No setup required, but counts against platform storage."
          active={cfg.backend !== 'gdrive'}
        />
        <BackendOption
          icon={<Cloud size={18} />}
          name="Google Drive (yours)"
          description={
            isGDrive
              ? `Connected as ${cfg.gdrive.user_email || 'unknown'}.  Files live in "4truck" on your Drive.`
              : 'Connect your Google Drive — files saved to your account.  Free up to 15 GB.'
          }
          active={isGDrive}
        >
          {isGDrive ? (
            <button
              type="button"
              onClick={handleDisconnect}
              disabled={disconnecting}
              className="inline-flex items-center gap-1 text-xs text-destructive hover:underline disabled:opacity-50"
            >
              <Unlink size={11} />
              {disconnecting ? t('storage.disconnecting') : t('storage.disconnect_button')}
            </button>
          ) : (
            <button
              type="button"
              onClick={handleConnect}
              disabled={connecting}
              className="inline-flex items-center gap-1.5 px-3 py-1 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-xs font-medium text-primary-foreground transition"
            >
              <ExternalLink size={11} />
              {connecting ? t('storage.redirecting') : t('storage.connect_button')}
            </button>
          )}
        </BackendOption>
      </div>

      {/* Explicit breakdown of where each content type lands.  Showed
          up in user testing as a surprise — operators connecting Drive
          assumed it would absorb dashcam captures too.  Honesty about
          the routing matters more than aspirational marketing. */}
      <StorageRoutingTable connectedToDrive={isGDrive} />

      {/* Drive disk-usage indicator.  Shown for any backend so the
          user can see "files we've stored" even on Local storage.
          Drive quota right-hand-side only renders when connected. */}
      <DiskUsageBar usage={cfg.usage} connected={isGDrive} />

      {isGDrive && (
        <div className="mt-3 text-xs text-muted-foreground space-y-1">
          <p>
            <span className="font-medium text-foreground">Folder:</span>{' '}
            <code className="font-mono bg-muted px-1 py-0.5 rounded">My Drive / 4truck /</code>
          </p>
          <p>
            <span className="font-medium text-foreground">Scope granted:</span>{' '}
            <code className="font-mono bg-muted px-1 py-0.5 rounded">drive.file</code>
            {' — '}
            we can only read and write files we created.  We cannot see other files on your Drive.
          </p>
        </div>
      )}
    </div>
  );
}

function StorageRoutingTable({ connectedToDrive }: { connectedToDrive: boolean }) {
  // Two-column breakdown of content types and where each one lands.
  // Lives between the backend cards and the disk-usage bar so the user
  // reads "Connect Drive" → "...for this content" → "current usage"
  // in a natural top-down flow.
  const driveItems = [
    'Work order invoices & shop photos',
    'Driver documents (CDL, medical card, training)',
    'Dashcam captures from Samsara',
    'Parking event map screenshots',
  ];
  const platformItems = [
    'User profile avatars (small, platform-shared cache)',
  ];
  return (
    <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
      <div className="p-3 rounded-lg border border-border bg-muted/30">
        <p className="text-xs font-medium inline-flex items-center gap-1.5 mb-2">
          <Cloud size={12} className={connectedToDrive ? 'text-blue-500' : 'text-muted-foreground'} />
          {connectedToDrive ? 'Saved to your Drive' : 'Would save to your Drive (if connected)'}
        </p>
        <ul className="text-[11px] text-muted-foreground space-y-0.5">
          {driveItems.map(s => (
            <li key={s} className="flex items-start gap-1.5">
              <span className="text-green-600 dark:text-green-400 mt-0.5">✓</span>
              {s}
            </li>
          ))}
        </ul>
      </div>
      <div className="p-3 rounded-lg border border-border bg-muted/30">
        <p className="text-xs font-medium inline-flex items-center gap-1.5 mb-2">
          <HardDrive size={12} className="text-muted-foreground" />
          Always on platform storage
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
  );
}

function DiskUsageBar({
  usage, connected,
}: {
  usage: StorageConfig['usage'];
  connected: boolean;
}) {
  const usedByApp = usage.used_by_app_bytes;
  const quota = usage.drive_quota;
  const limit = quota?.limit_bytes ?? null;
  const userTotalUsed = quota?.usage_bytes ?? null;

  // Two-segment progress bar: the user's *other* Drive files (anything
  // not created by 4truck) sit on the left in muted gray; 4truck's
  // own slice stacks immediately after in the brand blue.  Both are
  // expressed as a percentage of the whole-Drive quota so the user
  // sees how much room is left across the entire account, not just
  // our corner of it.
  const pctTotal = (connected && limit && userTotalUsed !== null)
    ? Math.min(100, (userTotalUsed / limit) * 100)
    : null;
  const pctApp = (connected && limit)
    ? Math.min(100, (usedByApp / limit) * 100)
    : 0;
  // The "other files" slice is whatever the user filled the Drive
  // with outside of 4truck.  Clamped at zero so a momentarily
  // inconsistent quota snapshot (Drive lags ~30 s behind writes)
  // can't render a negative width.
  const pctOther = pctTotal !== null
    ? Math.max(0, pctTotal - pctApp)
    : 0;
  const pctTotalRounded = pctTotal !== null ? Math.round(pctTotal) : null;

  // Threshold colour for the 4truck slice — when total drive usage
  // is nearing the plan limit we tint the bar to a warning hue so
  // the operator notices before Drive starts throttling uploads.
  const appBarColor = pctTotal === null
    ? 'bg-muted'
    : pctTotal >= 90 ? 'bg-red-500'
    : pctTotal >= 70 ? 'bg-orange-500'
    : 'bg-primary';

  return (
    <div className="mt-4 p-3 bg-muted/40 border border-border rounded-lg">
      <div className="flex items-baseline justify-between text-xs mb-2">
        <span className="text-muted-foreground">Storage used by 4truck</span>
        <span className="font-medium tabular-nums">{formatBytes(usedByApp)}</span>
      </div>
      {connected && pctTotal !== null && (
        <>
          {/* Single bar, two stacked segments: left = your other
              Drive files (muted), right = 4truck's slice (brand
              colour).  Both share the bar so the operator sees one
              progress meter instead of two competing ones. */}
          <div className="h-2 bg-background rounded-full overflow-hidden mb-1.5 flex">
            {pctOther > 0 && (
              <div
                className="h-full bg-muted-foreground/40 transition-all"
                style={{ width: `${pctOther}%` }}
                title={`Other Drive files: ${formatBytes((userTotalUsed ?? 0) - usedByApp)}`}
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

          {/* Legend explaining which colour is which slice. */}
          <div className="flex items-center gap-3 text-[11px] text-muted-foreground mb-1">
            <span className="flex items-center gap-1.5">
              <span className="inline-block w-2 h-2 rounded-sm bg-muted-foreground/40" />
              Your other files
            </span>
            <span className="flex items-center gap-1.5">
              <span className={`inline-block w-2 h-2 rounded-sm ${appBarColor}`} />
              4truck
            </span>
          </div>

          <div className="flex items-baseline justify-between text-[11px] text-muted-foreground">
            <span>
              Whole-Drive total: <span className="font-medium text-foreground">{formatBytes(userTotalUsed)}</span> of {formatBytes(limit)}
            </span>
            <span className="font-medium">{pctTotalRounded}%</span>
          </div>
        </>
      )}
      {connected && pctTotal === null && (
        <p className="text-[11px] text-muted-foreground">
          Drive quota unavailable (unlimited plan or API hiccup).
        </p>
      )}
      {!connected && (
        <p className="text-[11px] text-muted-foreground">
          Connect Google Drive to free up local storage and see your Drive quota.
        </p>
      )}
    </div>
  );
}

function BackendOption({
  icon, name, description, active, children,
}: {
  icon: React.ReactNode;
  name: string;
  description: string;
  active: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div className={`p-3 rounded-lg border ${
      active ? 'border-primary/40 bg-primary/5' : 'border-border'
    }`}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-2">
          <span className="text-muted-foreground mt-0.5">{icon}</span>
          <div>
            <p className="text-sm font-medium inline-flex items-center gap-1">
              {name}
              {active && <CheckCircle2 size={12} className="text-primary" />}
            </p>
            <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
              {description}
            </p>
          </div>
        </div>
      </div>
      {children && <div className="mt-2 ml-7">{children}</div>}
    </div>
  );
}
