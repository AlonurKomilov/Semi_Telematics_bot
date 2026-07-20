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
import { Bell } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { PageHeader, LastUpdated } from '../../../components/shell';
import { useAlertsQuery } from '../_shared/useAlertsQuery';

export default function AlertsHeader() {
  const { t } = useTranslation();
  const { dataUpdatedAt, isFetching, refetch } = useAlertsQuery();

  return (
    <PageHeader
      icon={Bell}
      title={t('alerts.page_title')}
      description={t('alerts.page_description_pending')}
      actions={
        <LastUpdated
          fetchedAt={dataUpdatedAt}
          isFetching={isFetching}
          onRefresh={refetch}
        />
      }
    />
  );
}
