/**
 * Page header section — title + bulk-ack action + LastUpdated chip.
 *
 * Reads:
 *   • selection size from AlertsSelectionContext (drives the bulk-ack button)
 *   • acking + setAcking + setBulkError + clearSelection (bulk-ack flow)
 *   • dataUpdatedAt + isFetching + refetch from useAlertsQuery (LastUpdated)
 *
 * Owns the bulk-ack network call.  After a successful bulk-ack:
 * selection is cleared and all alerts queries are invalidated so
 * AlertsResults refetches without the user clicking refresh.
 *
 * Persona-agnostic — no useShellConfig read.  The same header
 * renders for every persona; persona-specific copy / actions live
 * in their own sections (e.g. a future Dispatcher LiveAckPanel for
 * the sound toggle).
 */
import { Bell, CheckCircle2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { apiJSON } from '../../../api/client';
import { PageHeader, LastUpdated } from '../../../components/shell';
import type { BulkAckResponse } from '../../../types';
import { useAlertsSelection } from '../_shared/AlertsSelectionContext';
import { useAlertsQuery } from '../_shared/useAlertsQuery';

export default function AlertsHeader() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const {
    selected,
    clearSelection,
    acking,
    setAcking,
    setBulkError,
  } = useAlertsSelection();
  const { dataUpdatedAt, isFetching, refetch } = useAlertsQuery();

  async function ackSelected() {
    if (selected.size === 0 || acking) return;
    setAcking(true);
    setBulkError('');
    try {
      // Backend BulkAckRequest expects { ids: list[int] } — see
      // interfaces/api/routes/alerts.py.  The selection set holds
      // string-or-number ids (Alert.id is widened to support both),
      // so coerce to Number before sending or the int validator
      // 422s every bulk-ack click.
      const ids = Array.from(selected).map(Number);
      await apiJSON<BulkAckResponse>('/alerts/bulk-ack', {
        method: 'POST',
        body: { ids },
      });
      clearSelection();
      await qc.invalidateQueries({ queryKey: ['alerts'] });
    } catch (e) {
      setBulkError(e instanceof Error ? e.message : 'Bulk acknowledge failed');
    } finally {
      setAcking(false);
    }
  }

  return (
    <PageHeader
      icon={Bell}
      title={t('alerts.page_title')}
      description={t('alerts.page_description_pending')}
      actions={
        <div className="flex items-center gap-3">
          {selected.size > 0 && (
            <button
              onClick={ackSelected}
              disabled={acking}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded-md text-xs font-medium text-primary-foreground transition"
            >
              <CheckCircle2 size={13} />
              {acking
                ? t('alerts.acknowledging')
                : t('alerts.acknowledge_n', { n: selected.size })}
            </button>
          )}
          <LastUpdated
            fetchedAt={dataUpdatedAt}
            isFetching={isFetching}
            onRefresh={refetch}
          />
        </div>
      }
    />
  );
}
