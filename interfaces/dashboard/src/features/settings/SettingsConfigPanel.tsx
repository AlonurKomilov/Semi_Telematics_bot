/**
 * The account_settings values, as a panel inside the config gear.
 *
 * This was a card headed "Configuration" in the Settings page's content
 * flow, sitting between the bot-config and AI-usage cards. On any other
 * page that would merely be untidy; here it was actively confusing,
 * because every card on this page is called a setting and only this one
 * held rows the config family owns (`can_manage_config_all`). The rest —
 * bot config, forum routing, modules, public identity, danger zone — are
 * the page's OPERATIONS and stay on `can_manage_account`.
 *
 * State lives in the parent because the values arrive with the page's
 * `/settings/config` payload, which the page already fetches for its
 * other cards. Lifting the fetch in here would mean a second request for
 * data already in memory.
 */
import type { Dispatch, SetStateAction } from 'react';

interface SettingsConfigPanelProps {
  edits: Record<string, string>;
  setEdits: Dispatch<SetStateAction<Record<string, string>>>;
  saving: boolean;
  onSave: (key: string) => void;
}

export default function SettingsConfigPanel({
  edits, setEdits, saving, onSave,
}: SettingsConfigPanelProps) {
  const keys = Object.keys(edits);

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Account-wide values. Changing one changes it for everyone in the
        account.
      </p>
      {keys.length === 0 ? (
        // Reachable and honest: the server returns only keys that have a
        // stored value, so a fresh account genuinely has none yet. It no
        // longer doubles as "you cannot read these" — the gear does not
        // render at all without can_manage_config_all.
        <p className="text-muted-foreground text-sm">
          No settings configured yet.
        </p>
      ) : (
        <div className="space-y-3">
          {keys.map((key) => (
            <div key={key} className="flex items-center gap-3">
              <span className="text-sm text-muted-foreground w-48 shrink-0 truncate">
                {key}
              </span>
              <input
                value={edits[key]}
                onChange={(e) =>
                  setEdits((prev) => ({ ...prev, [key]: e.target.value }))
                }
                className="flex-1 bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
              />
              <button
                type="button"
                onClick={() => onSave(key)}
                disabled={saving}
                className="px-3 py-1.5 bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 rounded text-xs font-medium transition"
              >
                Save
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
