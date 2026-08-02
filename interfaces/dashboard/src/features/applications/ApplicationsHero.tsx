/**
 * ApplicationsHero — feature-scoped topbar chips shown while the
 * recruiter is ON /workforce/applications (see shells/heroes/
 * ShellHero).  Shows the OPEN pipeline stages — the daily workload
 * read — in stage order:
 *
 *   Submitted · Screening · Interview · Approved
 *
 * Terminal states (hired / rejected / withdrawn) are deliberately
 * not chips: they're history, visible in the grid's Hired / Closed
 * segment tabs.  Reads the same react-query cache as the page, so
 * a board drag or bulk move updates these numbers in the same tick.
 */
import HeroChip from '../../shells/heroes/HeroChip';
import { useApplicationsQuery, countAppsByStatus } from './useApplications';

export default function ApplicationsHero() {
  const { data, isLoading, isError } = useApplicationsQuery();
  if (isError) return <div className="flex-1 min-w-0" />;
  if (isLoading || !data) {
    return (
      <div className="flex-1 min-w-0 flex items-center px-2 gap-1.5">
        <span className="text-2xs text-muted-foreground/60">Loading applications…</span>
      </div>
    );
  }
  const counts = countAppsByStatus(data.items);
  // The query loads the newest APP_PAGE_LIMIT rows, so past that these are
  // counts of the LOADED SLICE, not of the pipeline. Say so in the chip
  // titles rather than let a partial number read as the whole truth.
  const partial = (data.total ?? 0) > data.items.length;
  const note = partial
    ? ` (newest ${data.items.length} of ${data.total} applications)`
    : '';
  return (
    <div className="flex-1 min-w-0 flex items-center px-2 gap-1.5 overflow-x-auto overflow-y-hidden scrollbar-thin">
      <HeroChip label="Submitted" value={counts.submitted ?? 0} tone="info" title={`New applications awaiting first review${note}`} />
      <HeroChip label="Screening" value={counts.screening ?? 0} tone="neutral" title={note ? `Screening${note}` : undefined} />
      <HeroChip label="Interview" value={counts.interview ?? 0} tone="warning" title={`Scheduled or in interview stage${note}`} />
      <HeroChip label="Approved" value={counts.approved ?? 0} tone="positive" title={`Cleared vetting — ready to hire${note}`} />
    </div>
  );
}
