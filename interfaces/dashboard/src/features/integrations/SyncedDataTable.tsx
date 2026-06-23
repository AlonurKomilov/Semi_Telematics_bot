/**
 * Telematics (Samsara) adapter for the shared <FeedsTable>.
 *
 * Fetches per-feed freshness from /integrations/{provider}/feeds and
 * maps it to the normalized FeedRow shape.  Freshness-only: continuous
 * feeds have no manual "Sync now" (the history feed's backfill lives in
 * the card's existing "Refresh history" button), so no row action.
 */

import { useQuery } from '@tanstack/react-query';
import { getProviderFeeds } from './api';
import { capabilityLabel, formatCadence, formatSyncTimestamp, isDerivedCapability } from './labels';
import FeedsTable, { type FeedRow } from './FeedsTable';
import { useTimezone } from '../../hooks/useTimezone';
import type { FeatureToggleMap, ProviderFeedsResponse } from './types';

export default function SyncedDataTable({
  providerId,
  toggles,
  defaults,
  onToggle,
}: {
  providerId: string;
  /** Account's effective feature toggles — carry cadence overrides. */
  toggles: FeatureToggleMap;
  /** Catalog defaults — cadence fallback when no override is set. */
  defaults: FeatureToggleMap;
  /** Enable/disable a feed's capability — folds the old checklist in. */
  onToggle: (capability: string, enabled: boolean) => void;
}) {
  const tz = useTimezone();
  const { data, isLoading, error } = useQuery<ProviderFeedsResponse>({
    queryKey: ['provider-feeds', providerId],
    queryFn: () => getProviderFeeds(providerId),
    staleTime: 30_000,
  });

  const rows: FeedRow[] = (data?.feeds ?? []).map((f) => {
    const t = toggles[f.capability] || {};
    const d = defaults[f.capability] || {};
    const cadence = formatCadence(
      t.cron || t.interval_sec || t.interval_min || t.interval_hour ? t : d,
    );
    const freshness = f.stored.last_synced_at
      ? `${f.stored.count.toLocaleString()} rows · ${formatSyncTimestamp(f.stored.last_synced_at, tz)}`
      : 'nothing synced yet';
    // Prefer the account's feature_toggles for the checkbox state so a
    // toggle reflects immediately (the /feeds ``enabled`` lags a refetch).
    const enabled = toggles[f.capability]?.enabled ?? f.enabled;
    return {
      key: f.capability,
      label: capabilityLabel(f.capability),
      freshness,
      enabled,
      cadence: cadence || undefined,
      feature: f.feature || undefined,
      originTag: isDerivedCapability(f.capability)
        ? {
            text: 'computed here',
            title: 'Derived locally by downsampling the live feed — not a separate pull from the provider.',
          }
        : undefined,
      toggle: { enabled, onChange: (en: boolean) => onToggle(f.capability, en) },
    };
  });

  return <FeedsTable rows={rows} loading={isLoading} error={Boolean(error)} />;
}
