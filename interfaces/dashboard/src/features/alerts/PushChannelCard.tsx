/**
 * Push notification channel — per-DEVICE enable/disable.
 *
 * Push consent lives in each browser, so this card manages a device
 * LIST, not one address: "Enable on this device" runs the browser
 * permission + subscribe flow for the browser you're in right now; other
 * rows are your other devices (removable from here).  Which alert types
 * go to push is chosen in the "Notify me when" matrix below — this card
 * only manages the devices.
 */
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { MonitorSmartphone, Trash2 } from 'lucide-react';
import { apiJSON } from '@/api/client';
import { Button } from '@/components/ui/button';
import { Tip } from '@/components/tooltip';
import { formatAgoShort } from '@/utils/datetime';
import { disablePush, enablePush, isPushSupported, thisDeviceEndpoint } from './push';
import { Card } from '@/components/ui/card';
import { SectionHeader } from '@/components/shell';

interface Device {
  id: number;
  endpoint: string;
  device_label: string;
  created_at: string;
  last_ok_at: string;
}

export default function PushChannelCard({ onChanged }: { onChanged: () => void }) {
  const [devices, setDevices] = useState<Device[]>([]);
  const [here, setHere] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const supported = isPushSupported();

  const load = useCallback(async () => {
    try {
      const r = await apiJSON<{ web_push: { devices?: Device[] } }>(
        '/notifications/prefs/web_push');
      setDevices(r.web_push.devices ?? []);
      setHere(await thisDeviceEndpoint());
    } catch {
      /* card renders empty; enable still works */
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const enable = async () => {
    setBusy(true);
    try {
      await enablePush();
      toast.success('Push enabled on this device');
      await load();
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not enable push');
    } finally {
      setBusy(false);
    }
  };

  const disableHere = async () => {
    setBusy(true);
    try {
      await disablePush();
      toast.success('Push disabled on this device');
      await load();
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not disable push');
    } finally {
      setBusy(false);
    }
  };

  const removeDevice = async (endpoint: string) => {
    setBusy(true);
    try {
      await apiJSON('/notifications/push/unsubscribe', {
        method: 'POST', body: { endpoint },
      });
      await load();
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not remove device');
    } finally {
      setBusy(false);
    }
  };

  const enabledHere = here !== null && devices.some(d => d.endpoint === here);

  return (
    <Card render={<section />}>
      <div className="flex items-center justify-between gap-2 mb-1">
        <SectionHeader size="card" icon={<MonitorSmartphone className="size-4" />}>
            Push
          </SectionHeader>
        {devices.length > 0 && (
          <span className="text-xs font-medium text-ok">
            {devices.length} device{devices.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>
      <p className="text-xs text-muted-foreground mb-3">
        System notifications on your computer or phone — they arrive even when
        the dashboard is closed. Enabled per device.
      </p>

      {!supported ? (
        <p className="text-xs text-muted-foreground">
          This browser doesn&apos;t support push notifications.
        </p>
      ) : enabledHere ? (
        <Button variant="outline" size="sm" onClick={() => void disableHere()} disabled={busy}>
          Disable on this device
        </Button>
      ) : (
        <Button size="sm" onClick={() => void enable()} disabled={busy}>
          {busy ? 'Enabling…' : 'Enable on this device'}
        </Button>
      )}

      {devices.length > 0 && (
        <ul className="mt-3 divide-y divide-border/60">
          {devices.map(d => (
            <li key={d.id} className="flex items-center justify-between gap-2 py-2">
              <div className="min-w-0">
                <p className="text-sm truncate">
                  {d.device_label || 'Device'}
                  {d.endpoint === here && (
                    <span className="ml-1.5 text-2xs font-medium text-primary">this device</span>
                  )}
                </p>
                <p className="text-2xs text-muted-foreground">
                  {d.last_ok_at
                    ? `Last delivery ${formatAgoShort(d.last_ok_at)}`
                    : `Added ${formatAgoShort(d.created_at)}`}
                </p>
              </div>
              <Tip label="Remove device">
                <button
                  onClick={() => void removeDevice(d.endpoint)}
                  disabled={busy}
                  aria-label={`Remove ${d.device_label || 'device'}`}
                  className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-danger-bg hover:text-danger transition-colors disabled:opacity-40 min-h-tap min-w-tap"
                >
                  <Trash2 className="size-3.5" aria-hidden />
                </button>
              </Tip>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
