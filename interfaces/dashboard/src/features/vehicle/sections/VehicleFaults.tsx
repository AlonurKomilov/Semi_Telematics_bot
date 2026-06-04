/**
 * Active Fault Codes section + inline AI Diagnose flow.
 *
 * The diagnose button cross-references the current fault list with
 * the health snapshot, so this section reads from BOTH the faults
 * query AND the health query.  Health is fetched here too rather
 * than relying on VehicleHealth being in the layout — Dispatch may
 * include Faults without Health, in which case we still want the
 * AI diagnosis to have lights context.  TanStack Query dedupes when
 * both sections are present.
 *
 * Permission gate: ``can_faults`` controls the fault feed; the AI
 * Diagnose button additionally checks ``can_faults`` (same flag
 * for now — change here if the AI flow gets its own permission).
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { apiJSON } from '../../../api/client';
import { CardSkeleton } from '../../../components/shell';
import { usePermissions } from '../../../hooks/usePermissions';
import { formatAIResponse } from '../../../utils/formatAI';
import type {
  AIDiagnoseResponse,
  Fault,
  FaultsResponse,
  HealthResponse,
} from '../../../types';
import type { VehicleSectionProps } from './_shared/types';

export default function VehicleFaults({ vehicleName }: VehicleSectionProps) {
  const { has } = usePermissions();
  const navigate = useNavigate();
  const hasFaultsPerm = has('can_faults');

  const [diagnosing, setDiagnosing] = useState(false);
  const [diagnosis, setDiagnosis] = useState('');
  const [diagnosisError, setDiagnosisError] = useState('');

  const { data: faults, isLoading: faultsLoading } = useQuery<FaultsResponse | null>({
    queryKey: ['vehicle-faults', vehicleName],
    queryFn: () =>
      apiJSON<FaultsResponse>(
        `/vehicles/${encodeURIComponent(vehicleName)}/faults`,
      ),
    enabled: hasFaultsPerm,
    staleTime: 30_000,
  });

  // Pulled here independently so the AI diagnosis can pass health
  // lights even when the Health section isn't in this persona's
  // layout.  Deduped against VehicleHealth via the query key.
  const { data: health } = useQuery<HealthResponse | null>({
    queryKey: ['vehicle-health', vehicleName],
    queryFn: () =>
      apiJSON<HealthResponse>(
        `/vehicles/${encodeURIComponent(vehicleName)}/health`,
      ),
    enabled: hasFaultsPerm,
    staleTime: 60_000,
  });

  if (!hasFaultsPerm) return null;
  if (faultsLoading) return <CardSkeleton height="h-48" />;

  const faultList: Fault[] = faults?.faults || [];

  async function diagnoseFaults() {
    if (diagnosing || !faultList.length) return;
    setDiagnosing(true);
    setDiagnosis('');
    setDiagnosisError('');
    try {
      const data = await apiJSON<AIDiagnoseResponse>('/ai/diagnose', {
        method: 'POST',
        body: {
          vehicle_name: vehicleName,
          dtcs: faultList,
          lights: health?.health ?? {},
        },
      });
      setDiagnosis(data.diagnosis);
    } catch (e) {
      setDiagnosisError(e instanceof Error ? e.message : 'Diagnosis failed');
    } finally {
      setDiagnosing(false);
    }
  }

  if (faultList.length === 0) {
    if (!faults) return null;
    return (
      <div className="bg-card border border-border rounded-xl p-5">
        <h2 className="text-lg font-semibold mb-3">Fault Codes</h2>
        <p className="text-green-600 dark:text-green-400 text-sm">
          No active fault codes
        </p>
      </div>
    );
  }

  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">
          Active Fault Codes ({faultList.length})
        </h2>
        <button
          onClick={diagnoseFaults}
          disabled={diagnosing}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-primary/15 hover:bg-primary/25 text-primary font-medium transition-colors disabled:opacity-60"
        >
          {diagnosing ? (
            <>
              <span className="inline-flex gap-0.5">
                <span className="animate-bounce" style={{ animationDelay: '0ms' }}>•</span>
                <span className="animate-bounce" style={{ animationDelay: '150ms' }}>•</span>
                <span className="animate-bounce" style={{ animationDelay: '300ms' }}>•</span>
              </span>
              {' '}Analyzing...
            </>
          ) : diagnosis ? (
            'Re-diagnose'
          ) : (
            '✨ Diagnose with AI'
          )}
        </button>
      </div>
      <div className="space-y-2">
        {(faultList as unknown as Record<string, unknown>[]).map((f, i) => {
          const j = (f.j1939 as Record<string, unknown> | undefined) ?? {};
          const spn = (j.spnDescription ?? f.spnDescription ?? f.code ?? 'DTC') as string;
          const fmi = (j.fmiDescription ?? f.fmiDescription) as string | undefined;
          const src = (j.sourceAddressName ?? f.sourceAddressName) as string | undefined;
          const count = (f.occurrences ?? f.occurrenceCount) as number | undefined;
          const desc = f.description as string | undefined;
          return (
            <div key={i} className="bg-muted rounded-lg p-3 text-sm">
              <div className="flex items-center gap-2">
                <span className="font-mono text-orange-600 dark:text-orange-400">
                  {spn}
                </span>
                {fmi && (
                  <span className="text-xs text-muted-foreground">FMI: {fmi}</span>
                )}
              </div>
              {desc && <p className="text-muted-foreground mt-1">{desc}</p>}
              {(count != null || src) && (
                <div className="flex gap-3 mt-1 text-xs text-muted-foreground">
                  {count != null && (
                    <span>× {count} occurrence{count !== 1 ? 's' : ''}</span>
                  )}
                  {src && <span>Source: {src}</span>}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Inline AI Diagnosis result */}
      {diagnosisError && (
        <div className="mt-4 text-destructive text-sm bg-destructive/10 rounded-lg px-3 py-2">
          {diagnosisError}
        </div>
      )}
      {diagnosis && (
        <div className="mt-4 bg-primary/5 border border-primary/20 rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-semibold text-primary">✨ AI Diagnosis</span>
            <button
              onClick={() =>
                navigate('/ai/chat', {
                  state: {
                    initialMessage: `Tell me more about the fault codes on Truck ${vehicleName}`,
                  },
                })
              }
              className="text-xs text-muted-foreground hover:text-foreground underline underline-offset-2 transition-colors"
            >
              Open in AI Chat
            </button>
          </div>
          <div
            className="text-sm text-foreground/90 leading-relaxed ai-response"
            dangerouslySetInnerHTML={{ __html: formatAIResponse(diagnosis) }}
          />
        </div>
      )}
    </div>
  );
}
