/**
 * Page header section — title + LastUpdated chip.
 *
 * The bulk-acknowledge action moved to DataGrid's bulk-action bar
 * (AlertsResults declares it as a `bulkActions` entry) when selection
 * became a first-class DataGrid feature — so this header no longer owns
 * the ack button or the network call; it's just the title + refresh
 * chip now.
 *
 * Persona-agnostic — no useShellConfig read.  The same header renders
 * for every persona; persona-specific copy / actions live in their own
 * sections.
 */
import { Bell, SlidersHorizontal } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { PageHeader, LastUpdated } from '../../../components/shell';
import { useAlertsQuery } from '../_shared/useAlertsQuery';

export default function AlertsHeader() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { dataUpdatedAt, isFetching, refetch } = useAlertsQuery();

  return (
    <PageHeader
      icon={Bell}
      title={t('alerts.page_title')}
      description={t('alerts.page_description_pending')}
      actions={
        <div className="flex items-center gap-2">
          {/* Notification preferences (personal DM settings) live behind
              this gear — the single Alerts gate reaches the board here
              and the preferences here, no separate nav entry. */}
          <button
            onClick={() => navigate('/notifications')}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 h-8 text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          >
            <SlidersHorizontal size={14} aria-hidden />
            <span className="hidden sm:inline">{t('alerts.notification_prefs')}</span>
          </button>
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
