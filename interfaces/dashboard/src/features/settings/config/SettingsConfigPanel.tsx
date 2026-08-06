/**
 * The account-wide values, as a panel inside the config gear.
 *
 * This was a card headed "Configuration" in the Settings page's content
 * flow. On any other page that would merely be untidy; here it was
 * actively confusing, because every card on this page is called a setting
 * and only this one held rows the config family owns
 * (`can_manage_config_all`). The rest — bot config, forum routing,
 * modules, public identity, danger zone — are the page's OPERATIONS and
 * stay on `can_manage_account`.
 *
 * TWO THINGS A UX PASS CAUGHT, both fixed here.
 *
 * 1. It rendered RAW WIRE KEYS as labels — `digest_hour`,
 *    `scorecard_default_subject` — to paying customers. Those are
 *    identifiers, not English. FIELDS below maps each to a human label
 *    and a one-line explanation.
 *
 * 2. It rendered only keys that ALREADY had a stored value, so a fresh
 *    account opened the gear onto "No settings configured yet" with no
 *    way to configure anything. The set is fixed and known (it is the
 *    same tuple `GET /settings/config` reads), so every row is always
 *    shown and an unset one is simply empty.
 *
 * WHY `timezone` IS NOT HERE, though the endpoint returns it. The account
 * timezone has its own card on this page, and that card writes
 * `accounts.timezone` — a COLUMN — through `PUT /admin/timezone`. The
 * `timezone` account_settings KEY is a separate, legacy store that
 * nothing reads. Rendering it would put a second timezone editor next to
 * the real one, silently writing somewhere with no effect. Left out
 * deliberately; the legacy key is tracked separately for removal.
 */
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import type { Dispatch, SetStateAction } from 'react';

interface SettingsConfigPanelProps {
  edits: Record<string, string>;
  setEdits: Dispatch<SetStateAction<Record<string, string>>>;
  saving: boolean;
  onSave: (key: string) => void;
}

/** The account_settings rows this panel owns, in the order they read. */
const FIELDS: { key: string; label: string; hint: string }[] = [
  { key: 'account_name', label: 'Account name', hint: 'Shown in the dashboard and on internal reports.' },
  { key: 'alert_defaults', label: 'Alert defaults', hint: 'Alert types new members start subscribed to.' },
  { key: 'language', label: 'Language', hint: 'Default language for members who have not chosen one.' },
  { key: 'digest_hour', label: 'Daily digest hour', hint: 'Local hour (0–23) the daily summary is sent.' },
  { key: 'scorecard_default_subject', label: 'Scorecard email subject', hint: 'Default subject line for scorecard emails.' },
];

export default function SettingsConfigPanel({
  edits, setEdits, saving, onSave,
}: SettingsConfigPanelProps) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Account-wide values.{' '}
        <span className="text-foreground">Changing one changes it for
        everyone</span> in the account. An empty field means no value is
        set and the built-in behaviour applies.
      </p>

      <div className="space-y-4">
        {FIELDS.map((f) => {
          const value = edits[f.key] ?? '';
          return (
            <div key={f.key} className="space-y-1">
              <label
                htmlFor={`setting-${f.key}`}
                className="text-sm font-medium text-foreground"
              >
                {f.label}
              </label>
              <p className="text-xs text-muted-foreground">{f.hint}</p>
              <div className="flex items-center gap-2">
                <Input
                  id={`setting-${f.key}`}
                  value={value}
                  onChange={(e) =>
                    setEdits((prev) => ({ ...prev, [f.key]: e.target.value }))
                  }
                  className="flex-1"
                />
                {/* Disabled while blank: an empty value is what "unset"
                    already means, so saving one is a no-op that looks
                    like an action. It also keeps the handler from
                    posting `undefined` for a row never touched. */}
                <Button
                  size="sm"
                  onClick={() => onSave(f.key)}
                  disabled={saving || value.trim() === ''}
                >
                  Save
                </Button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
