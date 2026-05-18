import { Cloud } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { PageHeader } from '../../components/shell';
import StorageBackendCard from './StorageBackendCard';

/**
 * Dedicated admin page for attachment storage configuration.
 *
 * Was previously embedded as a single card inside the personal Settings
 * page; moved here so account-level storage configuration (which
 * affects where every user's bytes land) gets its own sidebar entry
 * and isn't hidden under "your profile" menu.
 *
 * The actual functionality lives in ``StorageBackendCard`` — this page
 * is just the chrome (header + permission guard inherited from the
 * router) around it.  If/when a third storage-related panel ships
 * (e.g. retention rules, archive policy), they slot in below this card.
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
      <StorageBackendCard />
    </div>
  );
}
