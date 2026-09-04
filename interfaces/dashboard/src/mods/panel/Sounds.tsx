/**
 * The Sounds category — one volume, and the named things it applies to.
 *
 * The group holds its own audio locals: the preview a pack chip plays,
 * and the level a mute must come back to. Both were fields of a
 * 600-line `ModControls`; neither is anyone else's business.
 */
import { useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { RotateCcw, Volume2, VolumeX } from 'lucide-react';
import { Slider } from '../../components/ui/slider';
import { Switch } from '../../components/ui/switch';
import { Tip } from '../../components/tooltip';
import { usePreference } from '../../preferences';
import { SOUND_PACKS, armAudio, playCue, type SoundPack } from '../sound/engine';
import { KEY_PACKS, KEY_LIMITS, keyPackById } from '../sound/keys';
import { Chip } from './Chip';
import type { LabelClass } from './Interface';

export function SoundsGroup({ label: groupLabel }: { label: LabelClass }) {
  const { t } = useTranslation();
  const { value: soundPack, setValue: setSoundPack } = usePreference('mods.sound.pack');
  const { value: volume, setValue: setVolume } = usePreference('mods.sound.volume');
  const { value: alertSoundOn, setValue: setAlertSoundOn } = usePreference('dispatch.soundOn');
  const { value: uiSound, setValue: setUiSound } = usePreference('mods.sound.ui');
  const { value: keySound, setValue: setKeySound } = usePreference('mods.sound.keyboard');
  const { value: keyPack, setValue: setKeyPack } = usePreference('mods.sound.keyboard.pack');

  const preview = (pack: SoundPack, at: number) => {
    armAudio();
    playCue(pack.cues.alert, at);
  };

  /** The level to come back to. Silencing and restoring must not cost
   *  somebody the level they set — a mute that resets to 100% is a mute
   *  people stop using. */
  const beforeMute = useRef(1);

  return (
      <div>
        <div className="flex items-center justify-between gap-2 mb-1.5">
          <p className={groupLabel}>
            {t('mods.group_sound', 'Sound')}
          </p>
          <div className="flex items-center gap-1.5">
            <span className="text-2xs tabular-nums text-muted-foreground">
              {Math.round(volume * 100)}%
            </span>
            {/* Silence and restore, in one control. Zero is a real
                setting here rather than a disabled state — it is how
                a person quiets one screen without turning off each
                feature's own toggle. */}
            {/* Mute first, reset LAST — because the trailing control
                of a slider section's header means "return this
                section to its default" in every section, and SIZE
                already established that. A person who learns one
                header should not have to relearn the next. */}
            <Tip label={volume > 0 ? t('mods.sound_mute', 'Silence') : t('mods.sound_unmute', 'Unmute')}>
              <button
                type="button"
                onClick={() => {
                  if (volume > 0) { beforeMute.current = volume; setVolume(0); }
                  else setVolume(beforeMute.current || 1);
                }}
                aria-label={volume > 0 ? t('mods.sound_mute', 'Silence') : t('mods.sound_unmute', 'Unmute')}
                className="inline-flex size-5 min-h-tap min-w-tap items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-muted/60"
              >
                {volume > 0 ? <VolumeX className="size-3" /> : <Volume2 className="size-3" />}
              </button>
            </Tip>
            <Tip label={t('mods.sound_reset', 'Reset')}>
              <button
                type="button"
                onClick={() => setVolume(1)}
                disabled={volume === 1}
                aria-label={t('mods.sound_reset', 'Reset')}
                className="inline-flex size-5 min-h-tap min-w-tap items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted-foreground"
              >
                <RotateCcw className="size-3" />
              </button>
            </Tip>
          </div>
        </div>
        <Slider
          value={volume}
          min={0}
          max={1}
          step={0.05}
          aria-label={t('mods.sound_label', 'Sound volume')}
          formatValue={(v) => `${Math.round(v * 100)}%`}
          onValueCommitted={(v) => {
            setVolume(v);
            const pack = SOUND_PACKS.find((p) => p.id === soundPack);
            if (pack && v > 0) preview(pack, v);
          }}
        />
        <div className="flex flex-wrap gap-1 mt-1.5">
          {SOUND_PACKS.map((p) => (
            <Chip key={p.id} value={p.id} current={soundPack} label={p.label}
              onClick={(v) => {
                setSoundPack(v);
                if (volume > 0) preview(p, volume);
              }} />
          ))}
        </div>
        {/* Sounds is a GROUP of named things, not one switch.
            That is GX's shape — its Sounds category holds Background
            Music, Browser sound and Keyboard sound as separate items —
            and the owner's call. Ours holds three: what the interface
            does, what the keyboard does, and what alerts do. One volume
            above them all, because `engine.test.ts` holds the line that
            there is exactly ONE intensity: two numbers multiplying into
            one gain is how a person reaches 40% of 40%, hears almost
            nothing, and decides the feature is broken. */}
        <div className="border-t border-border mt-2.5 pt-2.5" />

        {/* The gate this section can actually operate.
            Two gates and one dial, which is the honest shape: the
            volume is a LEVEL, and each thing that makes a sound has
            its own switch. Alert sound's switch lives in the alerts
            panel and only three of the nine roles ever see it — this
            one is here, so every role has something the dial applies
            to. Off by default, and it has to stay that way: the level
            defaults to 1, so this switch is the whole distance between
            a fresh account and noise on a shared floor. */}
        <div className="flex items-center justify-between gap-2 mt-2">
          <span className="text-xs text-foreground">
            {t('mods.sound_ui_label', 'Interface sounds')}
          </span>
          <Switch
            size="sm"
            checked={uiSound}
            onCheckedChange={(next) => {
              setUiSound(next);
              // Armed from inside the click that turned it on. A
              // listener added mid-dispatch still receives the event on
              // nodes it has not reached yet, and window is above the
              // React root — so this same click unlocks audio and the
              // first cue after it can be heard. The provider's effect
              // covers the reload case; this covers the first try,
              // which is the one that decides whether a person believes
              // the feature works.
              if (next) armAudio();
            }}
            aria-label={t('mods.sound_ui_label', 'Interface sounds')}
          />
        </div>
        <p className="text-2xs text-muted-foreground mt-1">
          {t('mods.sound_ui_hint', 'A short cue when something can be undone, and when it lands.')}
        </p>

        <div className="flex items-center justify-between gap-2 mt-2">
          <span className="text-xs text-foreground">
            {t('mods.sound_keys_label', 'Keyboard')}
          </span>
          <Switch
            size="sm"
            checked={keySound}
            onCheckedChange={(next) => {
              setKeySound(next);
              // Same click, same reason as the switch above: a listener
              // added mid-dispatch still reaches window, so the gesture
              // that turns this on is the gesture that unlocks audio.
              if (next) armAudio();
            }}
            aria-label={t('mods.sound_keys_label', 'Keyboard')}
          />
        </div>
        <p className="text-2xs text-muted-foreground mt-1">
          {t('mods.sound_keys_hint', 'Typing clicks. Never in a password or payment field.')}
        </p>
        {keySound && (
          <div className="flex flex-wrap gap-1 mt-1.5">
            {KEY_PACKS.map((p) => (
              <Chip key={p.id} value={p.id} current={keyPack} label={p.label}
                onClick={(v) => {
                  setKeyPack(v);
                  // Preview the letter, the one a person hears most.
                  if (volume > 0) playCue(keyPackById(v)!.cues.letter, volume, KEY_LIMITS);
                }} />
            ))}
          </div>
        )}

        {/* Alert sound is a SWITCH here, not a status line pointing
            somewhere else. It used to read "Live alerts · off — turn on
            in the alerts panel", which was honest about the state and
            useless about the fix: that panel renders for dispatcher,
            fleet and safety only, so six of the nine roles were told
            where to go and could not go there. Same preference, reachable
            from the one place every role can see. */}
        <div className="flex items-center justify-between gap-2 mt-2">
          <span className="text-xs text-foreground">
            {t('mods.sound_gate_label', 'Live alerts')}
          </span>
          <Switch
            size="sm"
            checked={alertSoundOn}
            onCheckedChange={(next) => {
              setAlertSoundOn(next);
              if (next) armAudio();
            }}
            aria-label={t('mods.sound_gate_label', 'Live alerts')}
          />
        </div>
        <p className="text-2xs text-muted-foreground mt-1">
          {t('mods.sound_gate_hint', 'A cue when an alert arrives — louder for critical ones.')}
        </p>
      </div>
  );
}
