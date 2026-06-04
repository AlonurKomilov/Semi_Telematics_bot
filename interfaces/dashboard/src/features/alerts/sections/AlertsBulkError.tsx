/**
 * Bulk-action error banner.
 *
 * Renders only when AlertsHeader's bulk-ack network call failed and
 * set ``bulkError`` in AlertsSelectionContext.  Silent no-op
 * otherwise — the section sits in every layout but is invisible
 * unless there's something to surface.
 */
import { ErrorState } from '../../../components/shell';
import { useAlertsSelection } from '../_shared/AlertsSelectionContext';

export default function AlertsBulkError() {
  const { bulkError } = useAlertsSelection();
  if (!bulkError) return null;
  return (
    <div className="mb-3">
      <ErrorState message={bulkError} />
    </div>
  );
}
