import { Cloud } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { PageHeader } from '../../components/shell';
import StorageBackendCard from './StorageBackendCard';
import StorageHealthCard from './StorageHealthCard';
import StorageUsageCard from './StorageUsageCard';
import StorageFileTable from './StorageFileTable';

/**
 * Admin page for storage configuration + health.
 *
 * Top-down reading order matches the operator's mental flow:
 *   1. Status      — what backend is active, are we healthy?
 *   2. Stored      — what am I actually keeping, and how much?
 *   3. In transit  — what needs my attention right now?
 *   4. Settings    — connect/disconnect Drive, switch backend.
 *
 * Step 2 was missing entirely, and its absence made step 3 misleading:
 * the file table lists sync-QUEUE rows, so on a drained queue the page
 * showed an empty table and a ~0 byte figure to an account holding 793
 * files.  "Everything is synced" and "storage is broken" looked identical.
 *
 * Permission-guarded by the router (``can_manage_storage``).
 */
export default function Storage() {
  const { t } = useTranslation();
  return (
    <div>
      <PageHeader
        icon={Cloud}
        title={t('storage.page_title')}
        description={t('storage.page_desc')}
      />
      <div className="space-y-4">
        <StorageHealthCard />
        <StorageUsageCard />
        <StorageFileTable />
        <StorageBackendCard />
      </div>
    </div>
  );
}
