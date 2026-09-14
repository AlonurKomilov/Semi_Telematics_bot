/**
 * The Sounds category — one volume, and the named things it applies to.
 *
 * FOUR PIECES, because the /mods page renders them at three depths.
 * `SoundVolume` is the category's own control — one level, one pack,
 * shared by every lane that makes a noise — and sits on the category
 * page above its tiles. The three items are what a tile opens: a page
 * that is about ONE thing. `SoundsGroup` is the composition the panel
 * and the profile card render, and it is the same DOM those surfaces
 * always had.
 *
 * The URL used to promise that depth and the page did not keep it:
 * `/mods/sounds/keyboard` rendered the whole category, and so did
 * `/mods/sounds/interface`, so the last segment changed the heading
 * and nothing under it.
 *
 * Each piece reads its own preferences. `usePreference` is a
 * subscription, not a lookup, and the alternative — one parent reading
 * six and threading them down — is the 600-line `ModControls` this
 * folder was cut out of.
 */
import { useOffered } from '../store/useOffered';
import { useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Play, RotateCcw, Volume2, VolumeX } from '../../lib/icons';
import { Slider } from '../../components/ui/slider';
import { Switch } from '../../components/ui/switch';
import { Tip } from '../../components/tooltip';
import { usePreference } from '../../preferences';
import { armAudio, playCue, type SoundPack } from '../sound/engine';
import { KEY_LIMITS } from '../sound/keys';
import { SOUND_PACKS, soundPackById } from '../store/items/sound';
import { ACT_PACKS, actPackById } from '../store/items/acts';
import { ACT_LIMITS } from '../sound/acts';
import { KEY_PACKS, keyPackById } from '../store/items/keys';
import { AMBIENCE_PACKS, ambienceById } from '../store/items/ambience';
import { Chip } from './Chip';
import type { LabelClass } from './Interface';

/**
 * The category's own control: the level, and the cue set every lane
 * draws from.
 *
 * The pack lives HERE and not under "Interface sounds" because it is
 * not that item's: `playBannerCue` reads the same key as `playUiCue`,
 * so a person choosing Blip is choosing what an alert sounds like too.
 * The taxonomy says the same thing now.
 */
export function SoundVolume({ label: groupLabel }: { label: LabelClass }) {
  const offered = useOffered();
  const { t } = useTranslation();
  const { value: soundPack, setValue: setSoundPack } = usePreference('mods.sound.pack');
  const { value: volume, setValue: setVolume } = usePreference('mods.sound.volume');

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
        {offered('sound', SOUND_PACKS, (p) => p.id).map((p) => (
          <Chip key={p.id} value={p.id} current={soundPack} label={p.label}
            onClick={(v) => {
              setSoundPack(v);
              if (volume > 0) preview(p, volume);
            }} />
        ))}
      </div>
      <p className="text-2xs text-muted-foreground mt-1.5">
        {SOUND_PACKS.find((p) => p.id === soundPack)?.description ?? ''}
      </p>
    </div>
  );
}

/**
 * The gate this section can actually operate.
 *
 * Two gates and one dial, which is the honest shape: the volume is a
 * LEVEL, and each thing that makes a sound has its own switch. Alert
 * sound's switch lives in the alerts panel and only three of the nine
 * roles ever see it — this one is here, so every role has something
 * the dial applies to. Off by default, and it has to stay that way:
 * the level defaults to 1, so this switch is the whole distance
 * between a fresh account and noise on a shared floor.
 */
export function InterfaceSoundItem() {
  const { t } = useTranslation();
  const { value: uiSound, setValue: setUiSound } = usePreference('mods.sound.ui');
  const { value: volume } = usePreference('mods.sound.volume');
  const { value: soundPack } = usePreference('mods.sound.pack');
  const pack = soundPackById(soundPack);
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
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
        {t('mods.sound_ui_hint',
          'A short cue when the app answers — something saved, something refused, or a few seconds to undo.')}
      </p>
      {/*
        The two things this item could not answer.

        Keyboard and Background each show their pack right here, because
        each OWNS one. This item does not: the cue set is the category's,
        since `playBannerCue` reads the same key as `playUiCue` — picking
        Blip picks what an alert sounds like too. Correct, and it left
        this page saying nothing about what the switch turns on: no name
        for the cue set, no way to hear it, and no sign the choice lives
        one level up. A person turned it on, did nothing that answers,
        heard nothing, and concluded the feature was broken.

        So: the pack by NAME, playable, and where it is chosen. Not a
        second picker — one would be a second place to change a shared
        setting, which is how two controls start disagreeing.
      */}
      {uiSound && pack && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 mt-1.5">
          <button
            type="button"
            disabled={volume <= 0}
            onClick={() => {
              // Same bargain as the switch above: armed from inside the
              // click, so the first press is the one that is heard.
              armAudio();
              playCue(pack.cues.success, volume);
            }}
            aria-label={t('mods.sound_ui_try', 'Hear {{pack}}', { pack: pack.label })}
            className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-xs
                       font-medium min-h-tap text-muted-foreground transition-colors
                       hover:text-foreground hover:bg-muted/60
                       disabled:opacity-50"
          >
            <Play className="size-3" />
            {pack.label}
          </button>
          {/* No `disabled:cursor-not-allowed`: a variant-prefixed cursor
              utility is one the cursor PACKS cannot reach — they ship
              `.cursor-x`, which never matches the token
              `disabled:cursor-x` — and `mods/coverage.test.ts` counts
              every such site. The dimming and the sentence beside it
              already say the button is refusing; a cursor shape the
              mods engine cannot theme is not worth a 35th. */}
          {/* The reason is VISIBLE, not a tooltip: a disabled button
              swallows pointer events, so a tip on it never opens — and
              this is the one state where the person most needs telling
              why nothing happens. */}
          <span className="text-2xs text-muted-foreground">
            {volume <= 0
              ? t('mods.sound_ui_silenced',
                'Sound is silenced — raise the volume under Sound.')
              : t('mods.sound_ui_pack_note',
                'Shared with Live alerts — the cue set is chosen under Sound.')}
          </span>
        </div>
      )}
    </div>
  );
}

export function KeyboardItem() {
  const offered = useOffered();
  const { t } = useTranslation();
  const { value: volume } = usePreference('mods.sound.volume');
  const { value: keySound, setValue: setKeySound } = usePreference('mods.sound.keyboard');
  const { value: keyPack, setValue: setKeyPack } = usePreference('mods.sound.keyboard.pack');
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-foreground">
          {t('mods.sound_keys_label', 'Keyboard')}
        </span>
        <Switch
          size="sm"
          checked={keySound}
          onCheckedChange={(next) => {
            setKeySound(next);
            // Same click, same reason as the interface switch: a
            // listener added mid-dispatch still reaches window, so the
            // gesture that turns this on is the gesture that unlocks
            // audio.
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
          {offered('keys', KEY_PACKS, (p) => p.id).map((p) => (
            <Chip key={p.id} value={p.id} current={keyPack} label={p.label}
              onClick={(v) => {
                setKeyPack(v);
                // Preview the letter, the one a person hears most.
                if (volume > 0) playCue(keyPackById(v)!.cues.letter, volume, KEY_LIMITS);
              }} />
          ))}
        </div>
      )}
      {keySound && (
        <p className="text-2xs text-muted-foreground mt-1.5">
          {keyPackById(keyPack)?.description ?? ''}
        </p>
      )}
    </div>
  );
}

/**
 * Alert sound is a SWITCH here, not a status line pointing somewhere
 * else. It used to read "Live alerts · off — turn on in the alerts
 * panel", which was honest about the state and useless about the fix:
 * that panel renders for dispatcher, fleet and safety only, so six of
 * the nine roles were told where to go and could not go there. Same
 * preference, reachable from the one place every role can see.
 */
export function LiveAlertsItem() {
  const { t } = useTranslation();
  const { value: alertSoundOn, setValue: setAlertSoundOn } = usePreference('dispatch.soundOn');
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
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

/**
 * The whole category in one block — what the panel and the profile card
 * render, and the same DOM they rendered before the split.
 *
 * Sounds is a GROUP of named things, not one switch. That is GX's shape
 * — its Sounds category holds Background Music, Browser sound and
 * Keyboard sound as separate items — and the owner's call. One volume
 * above them all, because `engine.test.ts` holds the line that there is
 * exactly ONE intensity: two numbers multiplying into one gain is how a
 * person reaches 40% of 40%, hears almost nothing, and decides the
 * feature is broken.
 */
export function SoundsGroup({ label }: { label: LabelClass }) {
  return (
    <div>
      <SoundVolume label={label} />
      <div className="border-t border-border mt-2.5 pt-2.5" />
      <div className="mt-2"><InterfaceSoundItem /></div>
      <div className="mt-2"><ActSoundItem /></div>
      <div className="mt-2"><KeyboardItem /></div>
      <div className="mt-2"><LiveAlertsItem /></div>
    </div>
  );
}

/**
 * Background sound — a bed under everything, off until asked for.
 *
 * The switch and the bed are separate controls on purpose: turning it
 * off must not lose which bed you had, or coming back means choosing
 * again. The same reason the interface cues keep their pack when
 * silenced.
 */
export function BackgroundSoundItem() {
  const offered = useOffered();
  const { t } = useTranslation();
  const { value: on, setValue: setOn } = usePreference('mods.sound.background');
  const { value: which, setValue: setWhich } = usePreference('mods.sound.background.pack');
  const { value: volume } = usePreference('mods.sound.volume');
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-foreground">
          {t('mods.sound_bg_label', 'Background sound')}
        </span>
        <Switch
          size="sm"
          checked={on}
          onCheckedChange={(next) => {
            setOn(next);
            // Armed from inside the click that turned it on — the same
            // reason the interface switch does it: a bed that cannot
            // start is a switch that appears not to work.
            if (next) armAudio();
          }}
          aria-label={t('mods.sound_bg_label', 'Background sound')}
        />
      </div>
      <div className="flex flex-wrap gap-1 mt-1.5">
        {offered('ambience', AMBIENCE_PACKS, (a) => a.id).map((a) => (
          <Chip key={a.id} value={a.id} current={which} label={a.label}
            onClick={(v) => {
              setWhich(v);
              if (on) armAudio();
            }} />
        ))}
      </div>
      <p className="text-2xs text-muted-foreground mt-1">
        {on && volume > 0
          ? (ambienceById(which)?.description ?? '')
          : t('mods.sound_bg_hint',
            'A quiet bed under everything — nothing to listen to, just somewhere to be.')}
      </p>
    </div>
  );
}


/**
 * Interaction sounds — the app answering your HAND.
 *
 * Its own master rather than a wider reading of "Interface sounds", and
 * the arithmetic is the argument: that gate is around thirty cues in a
 * shift, this axis is two orders of magnitude more. Folding them
 * together would take a device that already opted in from thirty to
 * roughly nineteen hundred, with no new consent, on a shared floor.
 *
 * Three families underneath, defaulting ON. Turning the master on gives
 * the whole vocabulary everywhere with no second decision — the point of
 * an axis that is the same wherever you are — and the sub-switches exist
 * so the first complaint at hour six has an answer that is not "turn it
 * all off".
 */
export function ActSoundItem() {
  const offered = useOffered();
  const { t } = useTranslation();
  const { value: volume } = usePreference('mods.sound.volume');
  const { value: on, setValue: setOn } = usePreference('mods.sound.acts');
  const { value: pack, setValue: setPack } = usePreference('mods.sound.acts.pack');
  const { value: snoozeUntil, setValue: setSnooze } = usePreference('mods.sound.snoozeUntil');
  const controls = usePreference('mods.sound.acts.controls');
  const places = usePreference('mods.sound.acts.places');
  const selection = usePreference('mods.sound.acts.selection');

  const FAMILIES = [
    { pref: controls, label: t('mods.acts_controls', 'Controls'),
      hint: t('mods.acts_controls_hint', 'Pressing, choosing, toggling.') },
    { pref: places, label: t('mods.acts_places', 'Places'),
      hint: t('mods.acts_places_hint', 'Pages, dialogs and panels opening and closing.') },
    { pref: selection, label: t('mods.acts_selection', 'Selection'),
      hint: t('mods.acts_selection_hint', 'Gathering and dropping a set of rows.') },
  ];

  const snoozed = Date.now() < snoozeUntil;

  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-foreground">
          {t('mods.sound_acts_label', 'Interaction sounds')}
        </span>
        <Switch
          size="sm"
          checked={on}
          onCheckedChange={(next) => {
            setOn(next);
            // Armed from inside the click, the same bargain the two
            // switches above make: this gesture is the one that unlocks
            // audio, so the first cue after it can be heard.
            if (next) armAudio();
          }}
          aria-label={t('mods.sound_acts_label', 'Interaction sounds')}
        />
      </div>
      <p className="text-2xs text-muted-foreground mt-1">
        {t('mods.sound_acts_hint',
          'A short cue when you press, choose, toggle or move around — everywhere in the app.')}
      </p>

      {on && (
        <>
          {/* SNOOZE FIRST. It is the control a person reaches for at
              hour six, and unlike the mute beside the volume it restores
              itself — which is what makes it safe to reach for. */}
          <div className="flex flex-wrap items-center gap-1.5 mt-2">
            <span className="text-2xs text-muted-foreground">
              {snoozed
                ? t('mods.acts_snoozed', 'Quiet for now.')
                : t('mods.acts_snooze', 'Quiet for')}
            </span>
            {snoozed ? (
              <Chip value="off" current="off"
                label={t('mods.acts_unsnooze', 'Turn sound back on')}
                onClick={() => setSnooze(0)} />
            ) : (
              [[30, t('mods.acts_snooze_30', '30 min')],
               [60, t('mods.acts_snooze_60', '1 hour')]].map(([mins, label]) => (
                <Chip key={String(mins)} value={String(mins)} current=""
                  label={String(label)}
                  onClick={() => setSnooze(Date.now() + Number(mins) * 60_000)} />
              ))
            )}
          </div>

          {FAMILIES.map((f) => (
            <div key={f.label} className="flex items-center justify-between gap-2 mt-2">
              <span className="text-2xs text-muted-foreground">
                {f.label} — {f.hint}
              </span>
              <Switch
                size="sm"
                checked={f.pref.value}
                onCheckedChange={f.pref.setValue}
                aria-label={f.label}
              />
            </div>
          ))}

          <div className="flex flex-wrap gap-1 mt-2">
            {offered('acts', ACT_PACKS, (p) => p.id).map((p) => (
              <Chip key={p.id} value={p.id} current={pack} label={p.label}
                onClick={(v) => {
                  setPack(v);
                  // Preview the press: the one a person hears most, by
                  // an order of magnitude over everything else here.
                  if (volume > 0) {
                    playCue(actPackById(v)!.cues.press, volume, ACT_LIMITS, 'action');
                  }
                }} />
            ))}
          </div>
          <p className="text-2xs text-muted-foreground mt-1.5">
            {actPackById(pack)?.description ?? ''}
          </p>
        </>
      )}
    </div>
  );
}
