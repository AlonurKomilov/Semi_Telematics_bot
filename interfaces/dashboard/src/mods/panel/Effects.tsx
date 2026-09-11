/**
 * The Effects category — the light the app sits in, how it moves, and
 * what it does when nobody is watching it move.
 *
 * Three items and one composition, for the same reason Sounds has
 * them: a tile on the /mods page opens a page about ONE thing, while
 * the panel and the profile card show the category whole. The
 * composition is the DOM those two surfaces always rendered.
 */
import { useTranslation } from 'react-i18next';
import { Switch } from '../../components/ui/switch';
import { usePreference } from '../../preferences';
import { useMods, type Motion } from '../context';
import { MOD_MOTIONS, motionPercent } from '../catalogue';
import { Chip } from './Chip';
import { SHADER_PACKS, shaderPackById } from '../store/packs/shader';
import type { LabelClass } from './Interface';

const MOTION_OPTIONS: { value: Motion; key: string; label: string }[] =
  MOD_MOTIONS.map((m) => ({
    value: m,
    key: `mods.motion_${m}`,
    label: m === 'default' ? 'Normal' : m === 'calm' ? 'Calm' : 'Snappy',
  }));

/**
 * The light. First in the composition because it is the one effect
 * that is on all the time: Motion happens when something moves and
 * Ambient when nobody touches anything; the light is simply how the
 * room is lit while the work is being done.
 */
export function ShadersItem({ label }: { label: LabelClass }) {
  const { t } = useTranslation();
  const { theme, setTheme } = useMods();
  return (
    <div>
      <p className={`${label} mb-1.5`}>
        {t('mods.group_shader', 'Shaders')}
      </p>
      <div className="flex flex-wrap gap-1">
        {SHADER_PACKS.map((sp) => (
          <Chip key={sp.id} value={sp.id} current={theme.shader ?? 'flat'}
            label={t(`mods.shader_${sp.id}`, sp.label)}
            onClick={(v) => setTheme({ shader: v })} />
        ))}
      </div>
      {/* A SPECIMEN, because the light cannot be seen from here without
          one. `Card` is a bordered surface and draws no shadow at all —
          the scale lives on popovers, menus and map controls — so a
          person picking a preset on this page would change the light
          across the app and watch nothing move in front of them.
          `shadow-lg` is the step 37 of those overlays use, so this tile
          is a real sample rather than a decoration of one. */}
      <div className="flex items-center gap-2 mt-2">
        <span
          aria-hidden
          data-shader-specimen
          className="shrink-0 w-10 h-6 rounded-md bg-card border border-border shadow-lg"
        />
        <p className="text-2xs text-muted-foreground">
          {shaderPackById(theme.shader ?? 'flat')?.description}
        </p>
      </div>
    </div>
  );
}

/**
 * The header carries the intensity, exactly as Sound's does. GX gives
 * every mods category a percentage; ours had one for Sound and one for
 * Size and nothing here, even though motion has been a multiplier all
 * along.
 *
 * It is INVERTED on the way out — see `motionPercent`. The stored
 * scale multiplies duration, so calm is 1.6; every other percentage on
 * this card means more of the thing named, and a "Motion 160%" that
 * moves least would be the only one lying.
 */
export function MotionItem({ label }: { label: LabelClass }) {
  const { t } = useTranslation();
  const { theme, setTheme } = useMods();
  return (
    <div>
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
    </div>
  );
}

/**
 * Ambient is an effect in the literal sense — it is a thing the app
 * does on its own, over time, without being asked. It sits beside
 * Motion rather than in Interface for that reason: a person looking for
 * "what does this screen do while I am not here" is not looking under
 * colours and corners.
 */
export function AmbientItem() {
  const { t } = useTranslation();
  const { value: ambient, setValue: setAmbient } = usePreference('mods.ambient');
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
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

/** The whole category in one block — the panel's and the card's DOM. */
export function EffectsGroup({ label }: { label: LabelClass }) {
  return (
    <div>
      <ShadersItem label={label} />
      <div className="mt-2.5"><MotionItem label={label} /></div>
      <div className="mt-2.5"><AmbientItem /></div>
    </div>
  );
}
