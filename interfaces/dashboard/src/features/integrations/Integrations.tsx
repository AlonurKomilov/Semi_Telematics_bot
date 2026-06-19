/**
 * Integrations — owner-only self-service control surface for the
 * telematics integration module.
 *
 * Renders one card per provider in the catalog.  Connected providers
 * surface their feature toggles + a backfill action; available-but-
 * unconnected providers surface a Connect form; coming-soon
 * providers render as inert preview cards.
 *
 * All actions go through the API layer in ``api.ts`` which is
 * wrapped by React Query here for caching + invalidation.  Toggle
 * updates optimistically refetch the integrations list so the UI
 * stays in sync without flicker.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plug } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { CardSkeleton, PageHeader } from '../../components/shell';
import IntegrationCard from './IntegrationCard';
import BackfillStatusBadge from './BackfillStatusBadge';
import {
  connectIntegration,
  disconnectIntegration,
  listIntegrations,
  testConnection,
  triggerBackfill,
  updateToggles,
} from './api';
import type {
  CatalogEntry,
  FeatureToggleMap,
  IntegrationsListResponse,
  TestConnectionResponse,
} from './types';

// Shape returned by the backfill trigger endpoint.  Inlined here
// (rather than added to types.ts) because it only appears at the
// mutation-handler boundary; nothing else consumes it.
type BackfillTriggerResponse = {
  state: string;
  days?: number;
  reason?: string;
  account_id?: number;
  provider_id?: string;
  triggered_by?: number;
};

export default function Integrations() {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const { data, isLoading, error } = useQuery<IntegrationsListResponse>({
    queryKey: ['integrations'],
    queryFn: listIntegrations,
  });

  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ['integrations'] });

  const connectMutation = useMutation({
    mutationFn: ({ providerId, creds }: { providerId: string; creds: Record<string, string> }) =>
      connectIntegration(providerId, creds),
    onSuccess: invalidate,
  });

  const disconnectMutation = useMutation({
    mutationFn: (providerId: string) => disconnectIntegration(providerId),
    onSuccess: invalidate,
  });

  const toggleMutation = useMutation({
    mutationFn: ({
      providerId,
      next,
    }: {
      providerId: string;
      next: FeatureToggleMap;
    }) => updateToggles(providerId, next as Record<string, Record<string, unknown>>),
    onSuccess: invalidate,
  });

  const testMutation = useMutation({
    mutationFn: (providerId: string) => testConnection(providerId),
    onSuccess: invalidate,
  });

  const backfillMutation = useMutation({
    mutationFn: ({ providerId, days }: { providerId: string; days: number }) =>
      triggerBackfill(providerId, days),
    onSuccess: (_, { providerId }) => {
      // Start polling the backfill status immediately.
      qc.invalidateQueries({ queryKey: ['integration-backfill-status', providerId] });
    },
  });

  if (isLoading) {
    return (
      <div>
        <PageHeader icon={Plug} title={t('integrations.title', 'Integrations')} description={t('integrations.description', 'Connect your telematics platform and choose which data syncs.')} />
        <div className="grid grid-cols-1 gap-4">
          <CardSkeleton height="h-48" />
          <CardSkeleton height="h-32" />
          <CardSkeleton height="h-32" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <PageHeader icon={Plug} title={t('integrations.title', 'Integrations')} />
        <p className="text-sm text-danger">
          {error instanceof Error ? error.message : String(error)}
        </p>
      </div>
    );
  }

  const catalog = data?.catalog ?? [];
  const integrations = data?.integrations ?? [];
  const integrationByProvider = new Map(
    integrations.map((ai) => [ai.provider_id, ai] as const),
  );

  // Group cards: connected/available first, coming_soon last.
  const available = catalog.filter((c) => c.status === 'available' || c.status === 'beta');
  const upcoming = catalog.filter((c) => c.status === 'coming_soon');

  return (
    <div>
      <PageHeader
        icon={Plug}
        title={t('integrations.title', 'Integrations')}
        description={t(
          'integrations.description',
          'Connect your telematics platform and choose which data syncs into 4truck.',
        )}
      />

      <div className="grid grid-cols-1 gap-4">
        {available.map((entry) =>
          renderCard(entry, integrationByProvider.get(entry.provider_id) ?? null, {
            connectMutation,
            disconnectMutation,
            toggleMutation,
            testMutation,
            backfillMutation,
          }),
        )}
      </div>

      {upcoming.length > 0 && (
        <>
          <h3 className="mt-6 mb-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {t('integrations.upcoming', 'Available integrations')}
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {upcoming.map((entry) =>
              renderCard(entry, null, {
                connectMutation,
                disconnectMutation,
                toggleMutation,
                testMutation,
                backfillMutation,
              }),
            )}
          </div>
        </>
      )}
    </div>
  );
}

// Render helper kept outside the component body to avoid recreating
// the closure on every render.  Each card receives the mutation
// objects the parent owns; the card itself stays pure.
//
// Test-connection + backfill-trigger callbacks RETURN the mutation
// result (rather than ``void``) so the card can render an inline
// success/error banner.  The other callbacks remain void since the
// table query refetch is the user-visible feedback.
function renderCard(
  entry: CatalogEntry,
  integration: ReturnType<typeof Map.prototype.get> extends infer T ? T : never,
  mutations: {
    connectMutation: ReturnType<typeof useMutation<unknown, Error, { providerId: string; creds: Record<string, string> }>>;
    disconnectMutation: ReturnType<typeof useMutation<unknown, Error, string>>;
    toggleMutation: ReturnType<typeof useMutation<unknown, Error, { providerId: string; next: FeatureToggleMap }>>;
    testMutation: ReturnType<typeof useMutation<TestConnectionResponse, Error, string>>;
    backfillMutation: ReturnType<typeof useMutation<BackfillTriggerResponse, Error, { providerId: string; days: number }>>;
  },
) {
  return (
    <IntegrationCard
      key={entry.provider_id}
      entry={entry}
      integration={integration as IntegrationsListResponse['integrations'][number] | null}
      onConnect={async (creds) =>
        void (await mutations.connectMutation.mutateAsync({
          providerId: entry.provider_id,
          creds,
        }))
      }
      onDisconnect={async () =>
        void (await mutations.disconnectMutation.mutateAsync(entry.provider_id))
      }
      onToggle={async (next) =>
        void (await mutations.toggleMutation.mutateAsync({
          providerId: entry.provider_id,
          next,
        }))
      }
      onTestConnection={async () =>
        mutations.testMutation.mutateAsync(entry.provider_id)
      }
      onTriggerBackfill={async (days) =>
        mutations.backfillMutation.mutateAsync({
          providerId: entry.provider_id,
          days,
        })
      }
      backfillStatusBadge={
        // Only providers with history backfill expose the status endpoint —
        // a TMS (Datatruck) has none, so rendering the badge there just spams
        // 404s.  Gate on the capability.
        integration && entry.capabilities.includes('history_backfill')
          ? <BackfillStatusBadge providerId={entry.provider_id} />
          : null
      }
    />
  );
}
