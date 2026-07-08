/**
 * A single provider card on the Integrations page.
 *
 * Renders different content based on the catalog status:
 *   * `available` + connected   → status badge, feature toggles, actions
 *   * `available` + not connected → Connect button + form
 *   * `coming_soon`             → inert preview card with "Coming soon" badge
 *
 * Designed to be composable — the card never assumes which provider
 * it's rendering.  All provider-specific shape lives in the catalog
 * metadata.
 */

import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Check, Plug, RefreshCw, X, AlertTriangle, Loader2,
  Pencil, Plus, Trash2, ChevronDown, ChevronRight,
} from 'lucide-react';
import StatusBadge from '../../components/StatusBadge';
import { Button } from '../../components/ui/button';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';
import { toneClasses } from '../../lib/status';
import {
  getBackfillStatus,
  getProviderFeeds,
  listProviderCompanies,
  setCompanyCredential,
  removeCompanyCredential,
  testCompanyConnection,
  triggerCompanyBackfill,
} from './api';
import type {
  AccountIntegration,
  BackfillStatus,
  CatalogEntry,
  FeatureToggleMap,
  ProviderCompaniesResponse,
  ProviderCompanyEntry,
  ProviderFeedsResponse,
  TestCompanyResponse,
  TestConnectionResponse,
} from './types';
import DatatruckSyncPanel from './DatatruckSyncPanel';
import SyncedDataTable from './SyncedDataTable';
import CredentialsSection from './CredentialsSection';
import SingleCredentialPanel from './SingleCredentialPanel';
import {
  capabilityLabel,
  formatCadence,
  formatSyncTimestamp as formatBackfillTimestamp,
} from './labels';

interface Props {
  entry: CatalogEntry;
  integration: AccountIntegration | null;
  onConnect: (creds: Record<string, string>) => Promise<void>;
  onDisconnect: () => Promise<void>;
  onToggle: (next: FeatureToggleMap) => Promise<void>;
  /**
   * Returns the raw test_connection response so the card can render
   * an inline OK / error banner.  ``null`` return means the caller
   * already surfaced the error elsewhere; the card stays quiet.
   */
  onTestConnection: () => Promise<TestConnectionResponse | null>;
  /**
   * Returns the backfill trigger response (``state`` is "queued" on
   * success or "skipped"/"error" otherwise) so the card can show
   * an immediate confirmation toast even before the polling badge
   * picks the status up.
   */
  onTriggerBackfill: (days: number) => Promise<{ state: string; days?: number; reason?: string } | null>;
  backfillStatusBadge?: React.ReactNode;
}

type ActionFeedback = {
  kind: 'success' | 'error' | 'info';
  message: string;
} | null;

const HISTORY_WINDOW_ITEMS = [
  { value: '7',  label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days' },
  { value: '90', label: 'Last 90 days' },
];

export default function IntegrationCard({
  entry,
  integration,
  onConnect,
  onDisconnect,
  onToggle,
  onTestConnection,
  onTriggerBackfill,
  backfillStatusBadge,
}: Props) {
  const [showConnect, setShowConnect] = useState(false);
  const [showDisconnect, setShowDisconnect] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ActionFeedback>(null);
  const [triggering, setTriggering] = useState(false);
  const [triggerResult, setTriggerResult] = useState<ActionFeedback>(null);
  // The card-level "history window" every sync on this card reads — the
  // Samsara backfill depth AND the Datatruck date-ranged-sync window —
  // so the control lives in one consistent place per integration.  We
  // expose the three common presets (1–90 within the backend's bounds).
  const [historyWindow, setHistoryWindow] = useState(30);

  // Collapse state: when the page grows past a single integration,
  // the densely-rendered toggles + companies + actions per card
  // become a focus sink.  Default open for the actively-connected
  // integration (operator probably came here to manage it), default
  // closed for "coming soon" placeholders.  Persisted per-provider
  // in localStorage so the choice survives a refresh.
  const _storageKey = `integration-card-open:${entry.provider_id}`;
  const [expanded, setExpanded] = useState<boolean>(() => {
    try {
      const saved = localStorage.getItem(_storageKey);
      if (saved !== null) return saved === '1';
    } catch {
      // localStorage unavailable (Safari private mode, etc.) —
      // fall through to the default.
    }
    return Boolean(integration);  // open if connected, else collapsed
  });
  const toggleExpanded = () => {
    setExpanded((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(_storageKey, next ? '1' : '0');
      } catch {
        // ignore — visual state still flips
      }
      return next;
    });
  };

  // QueryClient handle so ``handleTrigger`` can optimistically seed
  // the BackfillStatusBadge cache with a ``queued`` payload after a
  // successful trigger — without that, the badge's ``refetchInterval``
  // never starts because it only polls on running/queued states.
  const qc = useQueryClient();

  // Shared query key with BackfillStatusBadge — React Query dedupes
  // the network call, so adding this read in the card doesn't double
  // up on Redis traffic.  ``data?.state`` drives the Run-now button's
  // disabled state so the user can't fire a duplicate while one is
  // running (the backend would 409 anyway, but the UI feedback was
  // otherwise just a stale "Queuing…" toast cycle).
  const { data: backfillStatus } = useQuery<BackfillStatus>({
    queryKey: ['integration-backfill-status', entry.provider_id],
    queryFn: () => getBackfillStatus(entry.provider_id),
    // Only providers with history backfill expose this endpoint — gating
    // it stops a TMS (Datatruck) from polling a route that 404s.
    enabled: Boolean(integration) && entry.capabilities.includes('history_backfill'),
    refetchInterval: (q) => {
      const s = q.state.data?.state;
      return s === 'running' || s === 'queued' ? 5_000 : false;
    },
    staleTime: 4_000,
  });

  // Which capabilities are shown (and now toggled) in the "Synced data"
  // table — so the checklist below can EXCLUDE them and stop showing the
  // same data type in two places.  Telematics feeds come from the
  // (deduped) feeds query; TMS feeds are all the tms_* capabilities.
  const { data: feedsData } = useQuery<ProviderFeedsResponse>({
    queryKey: ['provider-feeds', entry.provider_id],
    queryFn: () => getProviderFeeds(entry.provider_id),
    enabled: Boolean(integration) && entry.kind !== 'tms',
    staleTime: 30_000,
  });
  const feedCaps = useMemo(
    () =>
      entry.kind === 'tms'
        ? new Set(entry.capabilities.filter((c) => c.startsWith('tms_')))
        : new Set((feedsData?.feeds ?? []).map((f) => f.capability)),
    [entry.kind, entry.capabilities, feedsData],
  );
  const checklistCaps = entry.capabilities.filter((c) => !feedCaps.has(c));

  // Persist one capability's enabled flag — used by the feed-row toggles
  // in the Synced-data table (the merged-in checklist).
  const toggleCapability = (capability: string, enabled: boolean) => {
    if (!integration) return;
    void Promise.resolve(
      onToggle({
        ...integration.feature_toggles,
        [capability]: { ...(integration.feature_toggles[capability] ?? {}), enabled },
      }),
    ).then(() => {
      // Refresh the freshness/run views so the row's enabled state and
      // greying settle to the persisted value.
      qc.invalidateQueries({ queryKey: ['provider-feeds', entry.provider_id] });
      qc.invalidateQueries({ queryKey: ['datatruck-sync-status'] });
    });
  };
  const backfillRunning =
    backfillStatus?.state === 'running' || backfillStatus?.state === 'queued';

  // Auto-dismiss the inline action banners after 8 seconds so the
  // card doesn't accumulate stale messages over a session.
  useEffect(() => {
    if (!testResult) return;
    const id = setTimeout(() => setTestResult(null), 8000);
    return () => clearTimeout(id);
  }, [testResult]);

  useEffect(() => {
    if (!triggerResult) return;
    const id = setTimeout(() => setTriggerResult(null), 8000);
    return () => clearTimeout(id);
  }, [triggerResult]);

  const isComingSoon = entry.status === 'coming_soon';
  const isConnected = integration?.status === 'connected';
  const isErrored = integration?.status === 'error';

  const handleTest = async () => {
    if (testing) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await onTestConnection();
      if (result) {
        setTestResult({
          kind: result.ok ? 'success' : 'error',
          message: result.ok
            ? `Connected${result.message ? ' — ' + result.message : ''}`
            : `Failed: ${result.message || 'unknown error'}`,
        });
      }
    } catch (err) {
      setTestResult({
        kind: 'error',
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setTesting(false);
    }
  };

  const handleTrigger = async (days: number) => {
    if (triggering) return;
    setTriggering(true);
    setTriggerResult(null);
    try {
      const result = await onTriggerBackfill(days);
      if (result) {
        if (result.state === 'queued') {
          setTriggerResult({
            kind: 'success',
            message: `Backfill queued for ${result.days ?? days} days — progress shows above`,
          });
          // Optimistic update: seed the React Query cache with a
          // ``queued`` payload so the BackfillStatusBadge's
          // ``refetchInterval`` (which only polls on running/queued
          // states) kicks in immediately rather than waiting for
          // ``staleTime`` to elapse against the prior idle/null
          // cache.  Within 5s the real Redis-backed state will
          // overwrite this seed via the polling refetch.
          qc.setQueryData(
            ['integration-backfill-status', entry.provider_id],
            {
              state: 'queued',
              account_id: integration?.account_id,
              provider_id: entry.provider_id,
              days_requested: result.days ?? days,
              days_done: 0,
              days_total: 0,
              rows_inserted: 0,
            },
          );
          // Also invalidate so the badge does a fresh fetch right
          // away — covers the case where the spawned task already
          // wrote ``running`` to Redis before this line runs.
          qc.invalidateQueries({
            queryKey: ['integration-backfill-status', entry.provider_id],
          });
        } else if (result.state === 'skipped') {
          setTriggerResult({
            kind: 'info',
            message: `Skipped: ${result.reason || 'another backfill is already running'}`,
          });
        } else {
          setTriggerResult({
            kind: 'info',
            message: `State: ${result.state}`,
          });
        }
      }
    } catch (err) {
      setTriggerResult({
        kind: 'error',
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setTriggering(false);
    }
  };

  return (
    <div
      className={[
        'bg-card border rounded-xl p-5',
        isComingSoon ? 'border-border opacity-70' : 'border-border',
      ].join(' ')}
    >
      {/* Header is the click target for collapse/expand on connected
          integrations.  Coming-soon cards are never collapsible —
          they're already minimal. */}
      <div
        className={[
          'flex items-start gap-3',
          isComingSoon ? '' : 'cursor-pointer select-none',
        ].join(' ')}
        onClick={isComingSoon ? undefined : toggleExpanded}
        role={isComingSoon ? undefined : 'button'}
        tabIndex={isComingSoon ? undefined : 0}
        onKeyDown={(e) => {
          if (isComingSoon) return;
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            toggleExpanded();
          }
        }}
      >
        <div className="size-10 rounded-lg bg-muted flex items-center justify-center text-muted-foreground">
          <Plug size={20} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-base font-semibold">{entry.display_name}</h2>
            {/* Status chips go through the canonical StatusBadge so the
                soft-pill recipe (15% fill + solid text + 30% border) +
                radius track the theme picker.  Tone resolution lives
                in lib/status.ts STATUS_TONE — never pick a tone here. */}
            {isComingSoon && <StatusBadge status="coming_soon" />}
            {isConnected && <StatusBadge status="connected" />}
            {isErrored && <StatusBadge status="error" />}
            {integration?.status === 'disabled' && (
              <StatusBadge status="paused" />
            )}
          </div>
          <p className="text-sm text-muted-foreground mt-0.5">{entry.tagline}</p>
        </div>
        {backfillStatusBadge}
        {!isComingSoon && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={(e) => { e.stopPropagation(); toggleExpanded(); }}
            aria-label={expanded ? 'Collapse integration' : 'Expand integration'}
            aria-expanded={expanded}
            className="shrink-0"
          >
            {expanded ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
          </Button>
        )}
      </div>

      {/* Health-error banner stays visible even when collapsed — it's
          a problem the operator needs to see at a glance. */}
      {isErrored && integration?.last_health_error && (
        <div className={`mt-3 flex items-start gap-2 text-xs border rounded px-2.5 py-2 ${toneClasses('danger')}`}>
          <AlertTriangle size={14} className="flex-shrink-0 mt-0.5" />
          <span className="break-words">{integration.last_health_error}</span>
        </div>
      )}

      {/* When collapsed on a connected integration, render a one-line
          summary so the operator still gets a quick read of what's
          configured without having to expand every card. */}
      {!isComingSoon && integration && !expanded && (
        <p className="mt-3 text-xs text-muted-foreground">
          {entry.capabilities.length} capabilities · click to manage keys + cadences
        </p>
      )}

      {/* Everything below this point is in the collapsible region.
          Coming-soon cards always show their description (they're
          informational previews); connected cards hide it when
          collapsed since the operator is here to manage, not to
          read marketing. */}
      {(isComingSoon || expanded) && (
        <>
      <p className="mt-3 text-sm text-muted-foreground">{entry.description}</p>

      {entry.docs_url && (
        <a
          href={entry.docs_url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-2 inline-block text-xs text-primary hover:underline"
        >
          Provider docs ↗
        </a>
      )}

      {/* Connected — show toggles + actions */}
      {!isComingSoon && integration && (
        <div className="mt-4 border-t border-border pt-3">
          {/* Provenance line: when the last full backfill landed,
              relative + absolute UTC.  Lets operators distinguish
              "never backfilled" (data freshness unknown) from "fresh
              today".  Gated on history_backfill — a TMS provider
              (Datatruck) has no historical telemetry to replay, so the
              line would be a meaningless "never" there.  Mirror of the
              Run-all button's gate below so the two appear together. */}
          {entry.capabilities.includes('history_backfill') && (
            <p className="mb-3 text-2xs text-muted-foreground">
              Last history backfill:{' '}
              {integration.last_backfill_at
                ? formatBackfillTimestamp(integration.last_backfill_at)
                : (
                  <span className="italic">
                    {backfillRunning
                      ? 'in progress — first run, please wait'
                      : 'never — calendar projection may be empty'}
                  </span>
                )}
            </p>
          )}

          {/* Shared "history window" — one control per card, in the SAME
              place on every integration, that every sync on this card
              reads.  Samsara: how many days the backfill replays.
              Datatruck: the window for its date-ranged resources (orders
              / work orders); roster syncs ignore it.  Shown only for
              cards that have a history concept. */}
          {(entry.capabilities.includes('history_backfill') || entry.kind === 'tms') && (
            <div className="mb-3 flex items-center gap-2">
              <span className="text-2xs text-muted-foreground">History window</span>
              <Select
                value={String(historyWindow)}
                onValueChange={(v) => setHistoryWindow(Number(v))}
                disabled={triggering || backfillRunning}
                items={HISTORY_WINDOW_ITEMS}
              >
                <SelectTrigger
                  aria-label="History window"
                  title="How many days of history each sync on this card pulls."
                ><SelectValue /></SelectTrigger>
                <SelectContent>
                  {HISTORY_WINDOW_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          )}

          {/* Per-company key matrix — canonical place to manage
              Samsara API keys.  Replaces the API Key column that
              used to live on the Companies page so operators have
              ONE place to see "is Samsara wired up correctly".
              Only rendered for providers whose credentials are
              per-company (auth_kind="api_token") — TMS providers
              like Datatruck use one token per account, so the
              "Connected companies" matrix has nothing to show. */}
          {entry.auth_kind === 'api_token' && (
            <ConnectedCompanies providerId={entry.provider_id} />
          )}

          {/* Single-token providers (Datatruck) get the same shell with
              a one-credential summary + Update action — so every
              connected card shows its credentials consistently. */}
          {entry.auth_kind !== 'api_token' && (
            <SingleCredentialPanel
              authKind={entry.auth_kind}
              hasCredentials={integration.has_credentials}
              onUpdate={() => setShowConnect(true)}
            />
          )}

          {/* TMS providers get the "Synced data" panel instead — one
              row per resource with stored counts, live run progress,
              and a Sync-now button.  Keyed on kind (not provider_id)
              so a future second TMS provider inherits the surface.
              The window comes from the shared picker above. */}
          {entry.kind === 'tms' && (
            <DatatruckSyncPanel windowDays={historyWindow} onToggle={toggleCapability} />
          )}

          {/* Telematics providers get the same "Synced data" freshness
              table (count + last-synced per feed) so the Samsara and
              Datatruck cards read as one consistent structure. */}
          {entry.kind !== 'tms' && (
            <SyncedDataTable
              providerId={entry.provider_id}
              toggles={integration.feature_toggles}
              defaults={entry.feature_defaults}
              onToggle={toggleCapability}
            />
          )}

          {/* Everything that stores data is now a feature-grouped feed in
              the Synced-data table above.  What's left here are the
              non-data "housekeeping" capabilities — the one-time history
              backfill + the retention prune (actions/policies, not feeds).
              Hidden entirely when every capability is a feed (Datatruck). */}
          {checklistCaps.length > 0 && (
          <div className="mt-4">
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Housekeeping
            </p>
          <FeatureToggleList
            capabilities={checklistCaps}
            toggles={integration.feature_toggles}
            defaults={entry.feature_defaults}
            onChange={onToggle}
            onTriggerBackfill={handleTrigger}
            // Disable the Run-now button if the local trigger is in
            // flight OR if the server already has a backfill running
            // (read from the shared Redis status query above).  This
            // closes the gap where a user could spam the button while
            // a 45-min backfill was in progress and see confusing
            // toast cycles.
            backfillPending={triggering || backfillRunning}
            backfillRunningLabel={
              backfillRunning && backfillStatus
                ? `${backfillStatus.days_done ?? 0}/${backfillStatus.days_total ?? backfillStatus.days_requested ?? 30}`
                : undefined
            }
            // When a previous backfill completed, the button label
            // changes from "Run now (30 days)" to "Refresh history ·
            // N ago".  Tells the operator at-a-glance that the data
            // is already in place — clicking would just refresh, not
            // start from zero.  ``last_backfill_at`` is set by M5's
            // completion path (record_integration_backfill_completion).
            backfillLastDoneLabel={
              integration.last_backfill_at
                ? formatBackfillTimestamp(integration.last_backfill_at)
                : undefined
            }
          />
          </div>
          )}
          {triggerResult && (
            <FeedbackBanner
              kind={triggerResult.kind}
              message={triggerResult.message}
              onDismiss={() => setTriggerResult(null)}
            />
          )}
          {testResult && (
            <FeedbackBanner
              kind={testResult.kind}
              message={testResult.message}
              onDismiss={() => setTestResult(null)}
            />
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleTest}
              disabled={testing}
              title={
                entry.kind === 'tms'
                  ? `Test the ${entry.display_name} connection`
                  : "Probe every company's connection"
              }
            >
              {testing ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <RefreshCw size={14} />
              )}
              {testing ? 'Testing…' : 'Test all'}
            </Button>
            {/* Account-wide history backfill — same surface as the per-
                company Refresh button but covers every company.  Smart
                label: "Run all (30 days)" first time, "Refresh history
                · N ago" when last_backfill_at is set, "Running · X/Y
                days" while in flight.  Only rendered for providers
                that declare history_backfill in the catalog — TMS
                providers (Datatruck) have no historical telemetry to
                replay, so the button would be a misleading no-op. */}
            {/* Account-wide history backfill.  The day window comes from
                the shared card-level "History window" picker (rendered up
                top, same place on every integration), not an inline
                control here. */}
            {entry.capabilities.includes('history_backfill') && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => handleTrigger(historyWindow)}
                disabled={triggering || backfillRunning}
                title={
                  backfillRunning && backfillStatus
                    ? `Backfill in progress · ${backfillStatus.days_done ?? 0}/${backfillStatus.days_total ?? backfillStatus.days_requested ?? historyWindow} days`
                    : integration.last_backfill_at
                      ? `Last completed ${formatBackfillTimestamp(integration.last_backfill_at)}. Click to re-run the last ${historyWindow} days for every company.`
                      : `Run a ${historyWindow}-day history backfill across every company`
                }
              >
                {(triggering || backfillRunning) ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : integration.last_backfill_at ? (
                  <Check size={14} />
                ) : (
                  <RefreshCw size={14} />
                )}
                {backfillRunning && backfillStatus
                  ? `Running · ${backfillStatus.days_done ?? 0}/${backfillStatus.days_total ?? backfillStatus.days_requested ?? historyWindow}`
                  : triggering ? 'Queuing…'
                  : integration.last_backfill_at ? 'Refresh history'
                  : `Run all (${historyWindow} days)`}
              </Button>
            )}
            <Button
              type="button"
              variant="destructive"
              size="sm"
              onClick={() => setShowDisconnect(true)}
              title="Disconnect the integration — your synced data is kept"
            >
              <X size={14} />
              Disconnect
            </Button>
          </div>
        </div>
      )}

      {/* Not connected, but available — show Connect button */}
      {!isComingSoon && !integration && !showConnect && (
        <div className="mt-4">
          <Button type="button" onClick={() => setShowConnect(true)}>
            Connect {entry.display_name}
          </Button>
        </div>
      )}

      {/* Shown when connecting (no integration) AND when updating an
          existing single-token integration's credentials (re-connect
          upserts).  Per-company-key providers manage keys inline in the
          companies matrix, so they never reopen this for an update. */}
      {showConnect && (
        <div className="mt-4 border-t border-border pt-3">
          <ConnectForm
            authKind={entry.auth_kind}
            onCancel={() => setShowConnect(false)}
            onSubmit={async (creds) => {
              await onConnect(creds);
              setShowConnect(false);
            }}
          />
        </div>
      )}

      {showDisconnect && (
        <ConfirmDisconnect
          providerName={entry.display_name}
          onCancel={() => setShowDisconnect(false)}
          onConfirm={async () => {
            await onDisconnect();
            setShowDisconnect(false);
          }}
        />
      )}
        </>
      )}
    </div>
  );
}

// ── Connected companies (per-company key management) ─────────────

/** Render the per-company key matrix.  Each row shows the company
 *  code + name + key status, plus inline "Set / Edit / Remove"
 *  affordances that POST to the new /integrations/{provider}/
 *  companies/{code}/credentials endpoints.
 *
 *  The list comes from the backend (which already pulls from the
 *  encrypted integration credentials map) — raw tokens are never
 *  sent to the client.  A successful set/remove triggers a refetch
 *  so the row immediately reflects the new state. */
function ConnectedCompanies({ providerId }: { providerId: string }) {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery<ProviderCompaniesResponse>({
    queryKey: ['integration-companies', providerId],
    queryFn: () => listProviderCompanies(providerId),
    staleTime: 30_000,
  });
  // ALL hooks declared up-front BEFORE any early returns — React
  // depends on the hook count being identical across renders.  The
  // initial isLoading=true render must register the same hooks as
  // the post-load render, or it raises minified error #310.
  const [editing, setEditing] = useState<string | null>(null);
  const [tokenDraft, setTokenDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<ActionFeedback>(null);
  // Per-row "is this company currently being tested" state.
  // Tracked as a Set rather than a single string so the operator
  // can click Test on multiple companies in quick succession
  // without the second click clearing the first row's spinner.
  // Each row's spinner is driven by membership in this set; the
  // feedback line accumulates results in order.
  const [testingCodes, setTestingCodes] = useState<Set<string>>(new Set());
  const [refreshingCodes, setRefreshingCodes] = useState<Set<string>>(new Set());

  const companies = data?.companies ?? [];
  if (isLoading) {
    return (
      <div className="mb-3 text-2xs text-muted-foreground">
        Loading companies…
      </div>
    );
  }
  if (error) {
    return (
      <div className={`${toneClasses('danger')} mb-3 rounded px-2 py-1 text-2xs`}>
        Couldn't load companies: {error instanceof Error ? error.message : 'unknown'}
      </div>
    );
  }
  if (companies.length === 0) {
    return (
      <div className={`${toneClasses('info')} mb-3 rounded px-2 py-1.5 text-2xs`}>
        No companies yet.{' '}
        <Link to="/companies" className="underline font-medium">
          Add one on the Companies page
        </Link>
        , then return here to set its API key.
      </div>
    );
  }

  const handleSave = async (code: string) => {
    setSaving(true); setFeedback(null);
    try {
      await setCompanyCredential(providerId, code, tokenDraft);
      setEditing(null); setTokenDraft('');
      setFeedback({ kind: 'success', message: `Key saved for ${code}` });
      qc.invalidateQueries({ queryKey: ['integration-companies', providerId] });
    } catch (e) {
      setFeedback({
        kind: 'error',
        message: `Failed to save ${code}: ${e instanceof Error ? e.message : 'error'}`,
      });
    } finally {
      setSaving(false);
    }
  };

  const handleRemove = async (code: string) => {
    if (!confirm(`Remove the Samsara API key for ${code}? Live ingest for this company will stop until a new key is added.`)) {
      return;
    }
    setSaving(true); setFeedback(null);
    try {
      await removeCompanyCredential(providerId, code);
      setFeedback({ kind: 'success', message: `Key cleared for ${code}` });
      qc.invalidateQueries({ queryKey: ['integration-companies', providerId] });
    } catch (e) {
      setFeedback({
        kind: 'error',
        message: `Failed to remove ${code}: ${e instanceof Error ? e.message : 'error'}`,
      });
    } finally {
      setSaving(false);
    }
  };

  const missingKeys = companies.filter(c => !c.has_key).length;
  const summary = data?.health_summary;

  const handleRefresh = async (code: string) => {
    // Mirror handleTest's "already in flight" guard so a double-
    // click doesn't fire two queueing requests at the backend.
    if (refreshingCodes.has(code)) return;
    setRefreshingCodes(prev => new Set(prev).add(code));
    try {
      const r = await triggerCompanyBackfill(providerId, code, 30);
      setFeedback(
        r.state === 'queued'
          ? { kind: 'info', message: `${code}: refresh queued · ${r.days} days` }
          : { kind: 'info', message: `${code}: ${r.state}` },
      );
      qc.invalidateQueries({ queryKey: ['integration-companies', providerId] });
      qc.invalidateQueries({ queryKey: ['integrations'] });
    } catch (e) {
      setFeedback({
        kind: 'error',
        message: `${code}: ${e instanceof Error ? e.message : 'refresh failed'}`,
      });
    } finally {
      setRefreshingCodes(prev => {
        const next = new Set(prev);
        next.delete(code);
        return next;
      });
    }
  };

  const handleTest = async (code: string) => {
    // Already in flight for this company → no-op (defensive — the
    // per-row Test button is already disabled while the row is in
    // ``testingCodes``).
    if (testingCodes.has(code)) return;
    setTestingCodes(prev => new Set(prev).add(code));
    try {
      const r = await testCompanyConnection(providerId, code);
      setFeedback(
        r.ok
          ? { kind: 'success', message: `${code}: ${r.message} · ${r.elapsed_ms}ms` }
          : { kind: 'error', message: `${code}: ${r.message}` },
      );
      // Refresh both the company list (picks up new health) AND
      // the integration list (aggregate ai.status may have flipped
      // back to "connected" via _refresh_aggregate_status, which
      // unblocks the Run-now button).
      qc.invalidateQueries({ queryKey: ['integration-companies', providerId] });
      qc.invalidateQueries({ queryKey: ['integrations'] });
    } catch (e) {
      setFeedback({
        kind: 'error',
        message: `${code}: ${e instanceof Error ? e.message : 'test failed'}`,
      });
    } finally {
      setTestingCodes(prev => {
        const next = new Set(prev);
        next.delete(code);
        return next;
      });
    }
  };

  return (
    <CredentialsSection
      title={`Connected companies (${companies.length})`}
      headerRight={
        <span className="flex items-center gap-2">
          {/* Aggregate health summary — derived from per-company
              probe results, NOT the legacy ai.status.  Lets the
              operator see "4 of 5 healthy" instead of a binary
              connected/error chip. */}
          {summary && summary.status !== 'unknown' && (
            <span className={`${toneClasses(
              summary.status === 'healthy'
                ? 'ok'
                : summary.status === 'degraded'
                ? 'warn'
                : 'danger',
            )} text-2xs px-1.5 py-0.5 rounded`}>
              {summary.healthy} of {summary.total} healthy
            </span>
          )}
          {missingKeys > 0 && (
            <span className={`${toneClasses('warn')} text-2xs px-1.5 py-0.5 rounded`}>
              {missingKeys} need a key
            </span>
          )}
          {/* Companies are CREATED on the Companies page (code, name,
              active window); their Samsara keys are managed here.
              The link closes the "how do I add a 6th company?" gap —
              without it the operator has to already know the split. */}
          <Link
            to="/companies"
            className="text-2xs text-muted-foreground hover:text-foreground flex items-center gap-1"
            title="Create the company on the Companies page — it appears here for its API key"
          >
            <Plus size={12} />
            Add company
          </Link>
        </span>
      }
    >
      <ul className="divide-y divide-border">
        {companies.map((co) => (
          <CompanyKeyRow
            key={co.code}
            company={co}
            editing={editing === co.code}
            tokenDraft={tokenDraft}
            saving={saving}
            testing={testingCodes.has(co.code)}
            refreshing={refreshingCodes.has(co.code)}
            onEdit={() => { setEditing(co.code); setTokenDraft(''); }}
            onCancel={() => { setEditing(null); setTokenDraft(''); }}
            onChangeDraft={setTokenDraft}
            onSave={() => handleSave(co.code)}
            onRemove={() => handleRemove(co.code)}
            onTest={() => handleTest(co.code)}
            onRefresh={() => handleRefresh(co.code)}
          />
        ))}
      </ul>
      {feedback && (
        <div className="border-t border-border">
          <FeedbackBanner
            kind={feedback.kind}
            message={feedback.message}
            onDismiss={() => setFeedback(null)}
          />
        </div>
      )}
    </CredentialsSection>
  );
}

function CompanyKeyRow({
  company, editing, tokenDraft, saving, testing, refreshing,
  onEdit, onCancel, onChangeDraft, onSave, onRemove, onTest, onRefresh,
}: {
  company: ProviderCompanyEntry;
  editing: boolean;
  tokenDraft: string;
  saving: boolean;
  testing: boolean;
  refreshing: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onChangeDraft: (v: string) => void;
  onSave: () => void;
  onRemove: () => void;
  onTest: () => void;
  onRefresh: () => void;
}) {
  // Tone resolution for the per-row health badge.  ``null`` health
  // (never tested / TTL expired) is treated as ``neutral`` — we don't
  // know if it's healthy or not, so we shouldn't claim either way.
  const healthTone =
    company.health === null ? 'neutral'
    : company.health.ok ? 'ok'
    : 'danger';
  const healthText =
    company.health === null ? 'untested'
    : company.health.ok
      ? (company.health.elapsed_ms
          ? `reachable · ${company.health.elapsed_ms}ms`
          : 'reachable')
      : company.health.message;

  return (
    <li className="px-3 py-2 text-xs">
      <div className="flex items-center gap-3">
        <span className="font-medium w-12 shrink-0">{company.code}</span>
        <span className="flex-1 truncate text-muted-foreground">
          {company.display_name}
        </span>
        {!editing && (
          <>
            {/* Per-company /me probe result.  Title tooltip carries
                the full message when the badge gets ellipsised. */}
            <span
              className={`${toneClasses(healthTone)} text-2xs px-1.5 py-0.5 rounded max-w-44 truncate`}
              title={
                company.health
                  ? `${company.health.ok ? 'reachable' : 'failed'} — ${company.health.message}`
                  : 'No /me probe has been run for this company yet'
              }
            >
              {healthText}
            </span>
            <Button
              type="button" variant="ghost" size="sm"
              onClick={onTest} disabled={testing}
              title="Probe this company's Samsara /me endpoint"
            >
              {testing
                ? <Loader2 size={12} className="animate-spin" />
                : <RefreshCw size={12} />}
              Test
            </Button>
            {/* Per-company history refresh — same shape as Test but
                queues a 30-day backfill scoped to this company's key.
                Disabled when the company has no key (nothing to fetch
                with) or a refresh is already in flight for this row. */}
            <Button
              type="button" variant="ghost" size="sm"
              onClick={onRefresh}
              disabled={refreshing || !company.has_key}
              title={
                company.has_key
                  ? `Refresh this company's last 30 days of history`
                  : 'Set a key first before refreshing this company'
              }
            >
              {refreshing
                ? <Loader2 size={12} className="animate-spin" />
                : <RefreshCw size={12} />}
              Refresh
            </Button>
            {company.has_key ? (
              <span className="text-ok text-2xs flex items-center gap-1">
                <Check size={12} /> key set
              </span>
            ) : (
              <span className="text-muted-foreground italic text-2xs">no key</span>
            )}
            <Button type="button" variant="ghost" size="sm" onClick={onEdit}>
              <Pencil size={12} />
              {company.has_key ? 'Rotate' : 'Set key'}
            </Button>
            {company.has_key && (
              <Button type="button" variant="ghost" size="sm" onClick={onRemove}>
                <Trash2 size={12} />
              </Button>
            )}
          </>
        )}
      </div>
      {editing && (
        <div className="mt-2 flex items-center gap-2">
          <input
            type="password"
            placeholder="Samsara API token"
            value={tokenDraft}
            onChange={(e) => onChangeDraft(e.target.value)}
            className="flex-1 bg-muted border border-border rounded px-2 py-1 text-xs focus:outline-none focus:border-ring"
            autoFocus
          />
          <Button
            type="button"
            variant="default"
            size="sm"
            onClick={onSave}
            disabled={saving || !tokenDraft.trim()}
          >
            {saving ? <Loader2 size={12} className="animate-spin" /> : 'Save'}
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      )}
    </li>
  );
}

// ── Feature toggle list ────────────────────────────────────────

function FeatureToggleList({
  capabilities,
  toggles,
  defaults,
  onChange,
  onTriggerBackfill,
  backfillPending,
  backfillRunningLabel,
  backfillLastDoneLabel,
}: {
  capabilities: string[];
  toggles: FeatureToggleMap;
  defaults: FeatureToggleMap;
  onChange: (next: FeatureToggleMap) => Promise<void>;
  onTriggerBackfill: (days: number) => Promise<void>;
  backfillPending: boolean;
  /** When defined, shows the progress (e.g. "12/30") on the button
   *  so the operator can see the live backfill state without having
   *  to look at the badge in the card header. */
  backfillRunningLabel?: string;
  /** When defined and no run is in flight, the button label switches
   *  from "Run now (30 days)" to "Refresh history · N ago" so the
   *  operator sees at-a-glance that the work is already done.
   *  Format comes from formatBackfillTimestamp (relative + UTC). */
  backfillLastDoneLabel?: string;
}) {
  return (
    <ul className="space-y-1.5">
      {capabilities.map((cap) => {
        const t = toggles[cap] ?? { enabled: true };
        const def = defaults[cap] ?? {};
        const cadenceLabel = formatCadence(t.cron || t.interval_sec || t.interval_min || t.interval_hour ? t : def);
        const isBackfill = cap === 'history_backfill';
        return (
          <li key={cap} className="flex items-center gap-3 text-sm">
            <label className="flex items-center gap-2 flex-1 cursor-pointer">
              <input
                type="checkbox"
                checked={t.enabled ?? true}
                onChange={(e) =>
                  onChange({
                    ...toggles,
                    [cap]: { ...t, enabled: e.target.checked },
                  })
                }
                className="size-4 accent-primary"
              />
              <span>{capabilityLabel(cap)}</span>
            </label>
            {cadenceLabel && !isBackfill && (
              <span className="text-2xs text-muted-foreground">{cadenceLabel}</span>
            )}
            {/* The history_backfill row used to host an inline "Run
                now" button here.  That button has been promoted to
                the bottom action row alongside "Test all" so backfill,
                test, and disconnect all live together — and per-row
                "Refresh" buttons now expose the same affordance per
                company.  The checkbox here still controls whether the
                capability is enabled at all. */}
            {isBackfill && (backfillPending || backfillRunningLabel) && (
              <span
                className="text-2xs text-muted-foreground italic"
                title="A history backfill is in flight — see the badge in the card header for details"
              >
                {backfillRunningLabel
                  ? `running · ${backfillRunningLabel}`
                  : 'queuing…'}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

// ── Inline action feedback banner ────────────────────────────

function FeedbackBanner({
  kind,
  message,
  onDismiss,
}: {
  kind: 'success' | 'error' | 'info';
  message: string;
  onDismiss: () => void;
}) {
  // Tone resolution funnels through toneClasses() — the canonical
  // soft-pill recipe — so the banner inherits the theme's radius
  // and the same 15% / 30% alpha mix every status pill uses.
  const tone =
    kind === 'success' ? toneClasses('ok')
    : kind === 'error' ? toneClasses('danger')
    : toneClasses('neutral');
  const Icon = kind === 'success' ? Check : kind === 'error' ? AlertTriangle : RefreshCw;
  return (
    <div className={`mt-3 flex items-start gap-2 text-xs border rounded px-2.5 py-2 ${tone}`}>
      <Icon size={14} className="flex-shrink-0 mt-0.5" />
      <span className="flex-1 break-words">{message}</span>
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        onClick={onDismiss}
        className="flex-shrink-0 opacity-60 hover:opacity-100"
        aria-label="Dismiss"
      >
        <X size={12} />
      </Button>
    </div>
  );
}

// ── Connect form ──────────────────────────────────────────────

function ConnectForm({
  authKind,
  onSubmit,
  onCancel,
}: {
  authKind: string;
  onSubmit: (creds: Record<string, string>) => Promise<void>;
  onCancel: () => void;
}) {
  const [token, setToken] = useState('');
  const [subdomain, setSubdomain] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const wantsSubdomain = authKind === 'api_token_with_subdomain';
  const canSubmit = token.trim().length > 0 && (
    !wantsSubdomain || subdomain.trim().length > 0
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting || !canSubmit) return;
    setError('');
    setSubmitting(true);
    try {
      const creds: Record<string, string> = { api_token: token.trim() };
      if (wantsSubdomain) {
        creds.company_subdomain = subdomain.trim().toLowerCase();
      }
      await onSubmit(creds);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  if (authKind !== 'api_token' && authKind !== 'api_token_with_subdomain') {
    return (
      <div className="text-sm text-muted-foreground">
        {authKind} auth flow not yet supported in the dashboard.
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      {wantsSubdomain && (
        <div className="space-y-1.5">
          <label className="block text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Your Datatruck address
          </label>
          <input
            type="text"
            value={subdomain}
            onChange={(e) => setSubdomain(e.target.value)}
            className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            placeholder="acme.datatruck.io  or just  acme"
            autoFocus
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
          />
          <details className="text-2xs text-muted-foreground">
            <summary className="cursor-pointer select-none hover:text-foreground">
              Where do I find this?
            </summary>
            <div className="mt-1.5 pl-3 space-y-1.5 border-l-2 border-border">
              <p>
                Datatruck gives every company its own URL prefix (e.g.{' '}
                <span className="font-mono">acme</span> for Acme
                Trucking Inc). Three places you might find yours:
              </p>
              <ol className="list-decimal list-inside space-y-0.5 ml-1">
                <li>
                  In the welcome email Datatruck sent you when your
                  account was first set up.
                </li>
                <li>
                  By emailing{' '}
                  <span className="font-mono">support@datatruck.io</span>{' '}
                  — they answer this quickly.
                </li>
              </ol>
              <p className="italic">
                Heads up:{' '}
                <span className="font-mono">app.datatruck.io</span> is the
                shared login page — not your company URL. We need the part
                that's unique to your company.
              </p>
            </div>
          </details>
        </div>
      )}
      <div className="space-y-1.5">
        <label className="block text-xs font-medium uppercase tracking-wide text-muted-foreground">
          API token
        </label>
        <input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
          placeholder="paste your provider API token"
          autoFocus={!wantsSubdomain}
        />
        {wantsSubdomain && (
          <p className="text-2xs text-muted-foreground">
            Minted from Datatruck Settings → API tokens. Driver + Orders +
            Work Order Read scopes are enough for the MVP sync.
          </p>
        )}
      </div>
      {error && <p className="text-xs text-danger">{error}</p>}
      <div className="flex gap-2">
        <Button type="submit" disabled={submitting || !canSubmit}>
          <Check size={14} />
          {submitting ? 'Connecting…' : 'Connect'}
        </Button>
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

// ── Disconnect confirmation ────────────────────────────────────

function ConfirmDisconnect({
  providerName,
  onCancel,
  onConfirm,
}: {
  providerName: string;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [submitting, setSubmitting] = useState(false);
  return (
    <div className="mt-3 border-t border-border pt-3 bg-danger/5 rounded p-3 -mx-2">
      <p className="text-sm">
        Disconnect <span className="font-semibold">{providerName}</span>?
        Live ingest stops immediately.  Reconnecting later restores it
        without re-entering credentials.
      </p>
      <div className="mt-2 flex gap-2">
        <Button
          type="button"
          variant="destructive"
          onClick={async () => {
            if (submitting) return;
            setSubmitting(true);
            try {
              await onConfirm();
            } finally {
              setSubmitting(false);
            }
          }}
          disabled={submitting}
        >
          {submitting ? 'Disconnecting…' : 'Yes, disconnect'}
        </Button>
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
