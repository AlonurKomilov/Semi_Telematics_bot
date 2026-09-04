/**
 * The Effects category — how the app moves, and what it does when
 * nobody is watching it move.
 *
 * One group rather than two exported pieces: Motion and Ambient always
 * render together, on the page only, and splitting them would be a file
 * boundary that no surface asks for.
 */
import { useTranslation } from 'react-i18next';
import { Switch } from '../../components/ui/switch';
import { usePreference } from '../../preferences';
import { useMods, type Motion } from '../context';
import { MOD_MOTIONS, motionPercent } from '../catalogue';
import { Chip } from './Chip';
import type { LabelClass } from './Interface';

const MOTION_OPTIONS: { value: Motion; key: string; label: string }[] =
  MOD_MOTIONS.map((m) => ({
    value: m,
    key: `mods.motion_${m}`,
    label: m === 'default' ? 'Normal' : m === 'calm' ? 'Calm' : 'Snappy',
  }));

export function EffectsGroup({ label }: { label: LabelClass }) {
  const { t } = useTranslation();
  const { theme, setTheme } = useMods();
  const { value: ambient, setValue: setAmbient } = usePreference('mods.ambient');
  return (
    <div>
      {/* The header carries the intensity, exactly as Sound's does.
          GX gives every mods category a percentage; ours had one for
          Sound and one for Size and nothing here, even though motion
          has been a multiplier all along.

          It is INVERTED on the way out — see `motionPercent`. The
          stored scale multiplies duration, so calm is 1.6; every other
          percentage on this card means more of the thing named, and a
          "Motion 160%" that moves least would be the only one lying. */}
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <p className={label}>
          {t('mods.group_motion', 'Motion')}
        </p>
        <span className="text-2xs tabular-nums text-muted-foreground">
          {motionPercent(theme.motion)}%
        </span>
      </div>
      {/* A multiplier on every transition. Spinners and pulses are
          deliberately not on it — see index.css. */}
      <div className="flex flex-wrap gap-1">
        {MOTION_OPTIONS.map((o) => (
          <Chip key={o.value} value={o.value} current={theme.motion} label={t(o.key, o.label)}
            onClick={(v) => setTheme({ motion: v })} />
        ))}
      </div>

      {/* Ambient is an effect in the literal sense — it is a thing the
          app does on its own, over time, without being asked. It sits
          beside Motion rather than in Interface for that reason: a
          person looking for "what does this screen do while I am not
          here" is not looking under colours and corners. */}
      <div className="flex items-center justify-between gap-2 mt-2.5">
        <span className="text-xs text-foreground">
          {t('mods.ambient_label', 'Ambient mode')}
        </span>
        <Switch
          size="sm"
          checked={ambient}
          onCheckedChange={setAmbient}
          aria-label={t('mods.ambient_label', 'Ambient mode')}
        />
      </div>
      <p className="text-2xs text-muted-foreground mt-1">
        {t(
          'mods.ambient_hint',
          'After a few untouched minutes the page grows and the menus fade, so it reads from across the room. Alerts stay their own size.',
        )}
      </p>
    </div>
  );
}
