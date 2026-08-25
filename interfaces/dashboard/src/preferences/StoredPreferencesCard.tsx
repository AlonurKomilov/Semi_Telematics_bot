import { useCallback, useEffect, useState } from 'react';
import { Cloud, HardDrive, RotateCcw } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '../components/ui/button';
import { Switch } from '../components/ui/switch';
import { InfoTip } from '../components/tooltip';
import { usePreference } from './usePreference';
import { resetAll } from './store';
import { resetAppearanceDefault } from './appearance';
import { measureUsage, formatBytes } from './usage';
import { Card } from '@/components/ui/card';

/**
 * "Your saved preferences" — the ONE place an operator can see what the
 * dashboard remembers for them, how much it is, and whether it follows
 * them to another browser.
 *
 * Deliberately NOT a per-key list: each preference is already edited
 * where it lives (theme picker, banner settings, the grid's own menus),
 * so a second set of controls here would be two sources of truth for the
 * same value.  This card answers the three questions a per-key list
 * can't: how much, what's included, and where does it live.
 */
export default function StoredPreferencesCard() {
  const { value: syncEnabled, setValue: setSyncEnabled } = usePreference('prefs.syncEnabled');
  const [usage, setUsage] = useState(() => measureUsage());

  // Re-measure on mount and whenever the toggle flips (syncing writes
  // entries, so the number moves).
  const refresh = useCallback(() => setUsage(measureUsage()), []);
  useEffect(() => { refresh(); }, [refresh, syncEnabled]);

  const handleReset = () => {
    resetAll();
    // resetAll() sweeps this device. The personal appearance default is
    // a SYNCED key holding what an unused browser should start from — if
    // it survived, signing in somewhere new would restore exactly the
    // settings just discarded.
    resetAppearanceDefault();
    refresh();
    toast.success('Preferences reset to defaults');
  };

  return (
    <Card render={<section />}>
      <div className="flex items-start justify-between gap-4 mb-1">
        <h2 className="text-lg font-semibold inline-flex items-center gap-1.5">
          Saved preferences
          <InfoTip
            size={14}
            label="Settings the dashboard remembers for you — never shared with anyone else on the account."
          />
        </h2>
        <span className="text-sm tabular-nums text-muted-foreground shrink-0">
          {formatBytes(usage.bytes)}
        </span>
      </div>
      <p className="text-xs text-muted-foreground mb-4">
        Includes your theme, sidebar, live-alert banners, AI assistant panel,
        and every table's columns, sorting and saved tabs.
        {usage.gridBytes > 0 && (
          <> Tables account for {formatBytes(usage.gridBytes)} of it.</>
        )}
      </p>

      {/* Where it lives — the master switch. */}
      <div className="flex items-start justify-between gap-4 rounded-lg border border-border p-3">
        <div className="min-w-0">
          <div className="text-sm font-medium inline-flex items-center gap-2">
            {syncEnabled
              ? <Cloud className="text-primary shrink-0 size-4" />
              : <HardDrive className="text-muted-foreground shrink-0 size-4" />}
            {syncEnabled ? 'Synced to your account' : 'This browser only'}
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">
            {syncEnabled
              ? 'Your preferences follow you to another browser or device when you sign in.'
              : 'Preferences stay on this device. Signing in elsewhere starts from defaults.'}
          </p>
        </div>
        <Switch
          checked={syncEnabled}
          onCheckedChange={(next) => {
            setSyncEnabled(next);
            toast.success(next
              ? 'Preferences will sync to your account'
              : 'Preferences will stay on this browser');
          }}
          aria-label="Sync preferences to my account"
        />
      </div>

      <div className="flex items-center justify-between gap-4 mt-3">
        <p className="text-2xs text-muted-foreground">
          Resetting clears every saved preference — layouts, tabs and
          appearance go back to their defaults.
        </p>
        <Button variant="outline" size="sm" onClick={handleReset} className="shrink-0">
          <RotateCcw /> Reset every preference
        </Button>
      </div>
    </Card>
  );
}
