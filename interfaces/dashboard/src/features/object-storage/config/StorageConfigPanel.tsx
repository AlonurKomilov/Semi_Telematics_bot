/**
 * Where this account's files are stored — the Storage feature's config.
 *
 * Split out of ObjectStorageBackendCard, which kept two different things
 * in one card: CONNECTING Drive (operational — `can_manage_storage`) and
 * CHOOSING the backend (config — `can_manage_config_all`). The card still
 * owns the connection; this owns the choice, and lives behind the gear
 * with every other feature's config.
 *
 * The split is not cosmetic. The backend value decides where every driver
 * document, invoice and DQF the account ever writes will land, so it is
 * account-wide config by the blast-radius rule. A holder of Storage alone
 * can still run the storage — connect Drive, retry syncs, clear orphans —
 * and can no longer silently redirect the whole document estate.
 *
 * It re-reads `/object-storage/status` under the SAME query key the card
 * uses, so react-query serves both from one request rather than issuing a
 * second on open.
 */
import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { toast } from '../../../lib/toast';
import { HardDrive, Cloud, RefreshCcw, Loader2 } from 'lucide-react';
import { apiJSON } from '../../../api/client';
import {
  BackendOption, type Backend, type StorageConfig,
} from '../ObjectStorageBackendCard';

export default function StorageConfigPanel() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [switching, setSwitching] = useState<Backend | null>(null);

  const { data, isLoading } = useQuery<StorageConfig>({
    queryKey: ['storage-config'],
    queryFn: () => apiJSON<StorageConfig>('/object-storage/status'),
  });

  const handleSwitch = async (backend: Backend) => {
    setSwitching(backend);
    try {
      // PUT /object-storage/config — under the config convention this
      // replaces a config VALUE rather than performing an action. The old
      // POST /object-storage/backend stays mounted as a deprecated alias.
      await apiJSON('/object-storage/config', {
        method: 'PUT',
        body: { backend },
      });
      toast.success(t('storage.settings.switch_done', { backend }));
      qc.invalidateQueries({ queryKey: ['storage-config'] });
      qc.invalidateQueries({ queryKey: ['storage-health'] });
    } catch (e) {
      toast.error(
        e instanceof Error ? e.message : t('storage.settings.switch_failed'),
      );
    } finally {
      setSwitching(null);
    }
  };

  if (isLoading || !data) {
    return (
      <div className="flex justify-center py-6">
        <Loader2 className="animate-spin text-muted-foreground size-4.5" />
      </div>
    );
  }

  const activeBackend = (data.backend as Backend) || 'disk';
  const driveConnected = Boolean(data.gdrive?.connected);

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        {t(
          'storage.settings.config_intro',
          'Where new files are written. Existing files stay where they are.',
        )}
      </p>
      <div className="grid grid-cols-1 gap-2.5">
        <BackendOption
          icon={<HardDrive className="size-4" />}
          title={t('storage.settings.backend_disk_title')}
          description={t('storage.settings.backend_disk_desc')}
          active={activeBackend === 'disk'}
          needsDrive={false}
          driveConnected={driveConnected}
          switching={switching === 'disk'}
          onSwitch={() => handleSwitch('disk')}
        />
        <BackendOption
          icon={<Cloud className="size-4" />}
          title={t('storage.settings.backend_gdrive_title')}
          description={t('storage.settings.backend_gdrive_desc')}
          active={activeBackend === 'gdrive'}
          needsDrive
          driveConnected={driveConnected}
          switching={switching === 'gdrive'}
          onSwitch={() => handleSwitch('gdrive')}
        />
        <BackendOption
          icon={<RefreshCcw className="size-4" />}
          title={t('storage.settings.backend_hybrid_title')}
          description={t('storage.settings.backend_hybrid_desc')}
          active={activeBackend === 'hybrid'}
          needsDrive
          driveConnected={driveConnected}
          switching={switching === 'hybrid'}
          onSwitch={() => handleSwitch('hybrid')}
          recommended
        />
      </div>
      {!driveConnected && (
        // Google Drive and Hybrid need a connection this panel cannot
        // make — connecting is Manage, and it lives on the page behind.
        <p className="text-xs text-muted-foreground">
          {t(
            'storage.settings.connect_first',
            'Google Drive and Hybrid need a connected Drive account. Connect one on the Storage page, then choose it here.',
          )}
        </p>
      )}
    </div>
  );
}
