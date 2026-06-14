import { Cloud } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { PageHeader } from '../../components/shell';
import StorageBackendCard from './StorageBackendCard';
import StorageHealthCard from './StorageHealthCard';
import StorageFileTable from './StorageFileTable';

/**
 * Admin page for storage configuration + health.
 *
 * Top-down reading order matches the operator's mental flow:
 *   1. Status      — what backend is active, are we healthy?
 *   2. Files       — what needs my attention right now?
 *   3. Settings    — connect/disconnect Drive, switch backend.
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
        <StorageFileTable />
        <StorageBackendCard />
      </div>
    </div>
  );
}
