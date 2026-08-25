import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ClipboardCheck, ChevronRight } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { type Tone } from '../../lib/status';
import type { PTIInspectionRow, PTIInspectionsResponse } from '../../types';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

/**
 * "Last 5 inspections" mini-card embedded on the Vehicle Detail page.
 *
 * Hits ``GET /api/inspections?vehicle_name=X&page_size=5`` — same
 * endpoint as the Inspections list, just scoped to one vehicle.
 * Permission is the caller's responsibility: the parent only mounts
 * this when ``can_inspections_all`` is granted.
 */

const STATUS_LABEL: Record<PTIInspectionRow['status'], string> = {
  scheduled:           'Scheduled',
  in_progress:         'In progress',
  submitted:           'Pending review',
  reviewed:            'Reviewed',
  revision_required:   'Revision required',
};

// PTI workflow status → tone.  This vocabulary is more specific than
// the generic ``statusTone`` map (here ``submitted`` = pending review →
// info, ``in_progress`` = mid-walkaround → warn), so it keeps its own
// mapping and funnels through ``toneClasses``.
const STATUS_TONE: Record<PTIInspectionRow['status'], Tone> = {
  scheduled:         'neutral',
  in_progress:       'warn',
  submitted:         'info',
  reviewed:          'ok',
  revision_required: 'warn',
};

function _formatDate(iso: string | null, tz?: string): string {
  return formatDay(iso, { timeZone: tz });
}

interface Props {
  vehicleName: string;
}

export function VehicleInspectionsCard({ vehicleName }: Props) {
  const { t } = useTranslation();
  const tz = useTimezone();

  const { data, isLoading, error } = useQuery({
    queryKey: ['vehicle-inspections', vehicleName],
    queryFn: () => apiJSON<PTIInspectionsResponse>(
      `/inspections?vehicle_name=${encodeURIComponent(vehicleName)}&page_size=5`,
    ),
    staleTime: 60_000,
  });

  const rows = data?.items ?? [];

  return (
    <Card>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold inline-flex items-center gap-2">
          <ClipboardCheck className="text-primary size-4.5" />
          {t('inspections.vehicle_section.title')}
        </h2>
        <Link
          to={`/inspections?vehicle=${encodeURIComponent(vehicleName)}`}
          className="text-xs text-primary hover:underline inline-flex items-center gap-0.5 min-h-tap"
        >
          {t('inspections.vehicle_section.view_all')}
          <ChevronRight className="size-3" />
        </Link>
      </div>

      {isLoading && (
        <p className="text-sm text-muted-foreground">Loading…</p>
      )}

      {error && (
        <p className="text-sm text-destructive">
          {error instanceof Error ? error.message : 'Load failed'}
        </p>
      )}

      {!isLoading && !error && rows.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {t('inspections.vehicle_section.empty')}
        </p>
      )}

      {rows.length > 0 && (
        <ul className="space-y-1.5">
          {rows.map(r => (
            <li key={r.id}>
              <Link
                to={`/inspections?id=${r.id}`}
                className="flex items-center justify-between gap-2 p-2 rounded-md hover:bg-muted/50 transition-colors"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium capitalize truncate">
                    {r.inspection_type.replace(/_/g, ' ')}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {_formatDate(r.submitted_at ?? r.created_at, tz)}
                    {r.defects_count > 0 ? ` · ${r.defects_count} defect${r.defects_count !== 1 ? 's' : ''}` : ''}
                    {r.has_oos_defect ? ' · OOS' : ''}
                  </p>
                </div>
                <Badge tone={STATUS_TONE[r.status]}>
                  {STATUS_LABEL[r.status]}
                </Badge>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
