import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ClipboardCheck, ChevronRight } from 'lucide-react';
import { apiJSON } from '../../api/client';
import type { PTIInspectionRow, PTIInspectionsResponse } from '../../types';

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

const STATUS_TONE: Record<PTIInspectionRow['status'], string> = {
  scheduled:         'bg-muted text-muted-foreground',
  in_progress:       'bg-amber-500/15 text-amber-700 dark:text-amber-400',
  submitted:         'bg-blue-500/15 text-blue-700 dark:text-blue-400',
  reviewed:          'bg-green-500/15 text-green-700 dark:text-green-400',
  revision_required: 'bg-orange-500/15 text-orange-700 dark:text-orange-400',
};

function _formatDate(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString();
}

interface Props {
  vehicleName: string;
}

export function VehicleInspectionsCard({ vehicleName }: Props) {
  const { t } = useTranslation();

  const { data, isLoading, error } = useQuery({
    queryKey: ['vehicle-inspections', vehicleName],
    queryFn: () => apiJSON<PTIInspectionsResponse>(
      `/inspections?vehicle_name=${encodeURIComponent(vehicleName)}&page_size=5`,
    ),
    staleTime: 60_000,
  });

  const rows = data?.items ?? [];

  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold inline-flex items-center gap-2">
          <ClipboardCheck size={18} className="text-primary" />
          {t('inspections.vehicle_section.title')}
        </h2>
        <Link
          to={`/inspections?vehicle=${encodeURIComponent(vehicleName)}`}
          className="text-xs text-primary hover:underline inline-flex items-center gap-0.5"
        >
          {t('inspections.vehicle_section.view_all')}
          <ChevronRight size={12} />
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
                    {_formatDate(r.submitted_at ?? r.created_at)}
                    {r.defects_count > 0 ? ` · ${r.defects_count} defect${r.defects_count !== 1 ? 's' : ''}` : ''}
                    {r.has_oos_defect ? ' · OOS' : ''}
                  </p>
                </div>
                <span className={`text-[11px] font-medium px-2 py-0.5 rounded ${STATUS_TONE[r.status]} whitespace-nowrap`}>
                  {STATUS_LABEL[r.status]}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
