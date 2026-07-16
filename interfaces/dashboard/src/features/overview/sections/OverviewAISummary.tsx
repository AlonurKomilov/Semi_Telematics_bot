/**
 * AI narrative summary card — Tier 2 of the Overview.
 *
 * Only rendered when the account has at least one vehicle (fresh
 * tenants see nothing here until ingestion lands the first truck —
 * the AI has nothing to summarise about an empty fleet).  The card
 * itself is the existing shared ``AISummaryCard`` component, which
 * uses ``useShellConfig`` internally for persona-tuned copy.
 */
import { AISummaryCard } from '../../../components/shell';
import type { OverviewSectionProps } from './_shared/types';

export default function OverviewAISummary({ stats }: OverviewSectionProps) {
  const total = (stats.vehicles ?? stats.fleet)?.total ?? 0;
  if (total <= 0) return null;
  return <AISummaryCard stats={stats} />;
}
