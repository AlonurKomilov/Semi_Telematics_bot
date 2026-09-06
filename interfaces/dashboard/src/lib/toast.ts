/**
 * The toast lane, and the one place a toast makes a sound.
 *
 * Every file that raises a toast imports `toast` from here rather than
 * from `sonner`, and `src/test/toastLane.test.ts` holds that line. The
 * object below IS sonner's — same call signatures, same return values,
 * `promise` / `custom` / `dismiss` / `loading` untouched — so migrating
 * a call site is an import line and nothing else.
 *
 * WHY A WRAPPER AND NOT A LISTENER. Sonner exposes `useSonner()`, so a
 * component mounted beside the `<Toaster>` could watch every toast
 * appear and sound it, with no import churn at all. It cannot work, and
 * the reason is worth keeping: `undoable.ts` and `stagedAction.tsx`
 * raise `toast.success` for an action that opened an UNDO WINDOW, and
 * that wants the `undo` cue — "you can take this back" — not the
 * success chime. The sonner type says `success` in both cases. The tone
 * does not determine the cue; only the caller knows, and a listener has
 * no way to be told. Both of those files used to sound themselves right
 * before raising the toast, which is exactly the double-sound a
 * listener would have shipped.
 *
 * So: sound by tone BY DEFAULT, and let a caller that knows better say
 * `cue`. That is the same option `showBanner` already takes, with the
 * same two forms — a named cue to override, or `false` for silence —
 * because a person moving between the two lanes should not have to
 * learn a second vocabulary.
 */
import { toast as sonnerToast, type ExternalToast } from 'sonner';
import { playToastCue } from '../mods/sound/cue';
import type { CueName } from '../mods/sound/engine';

/** Sonner's options, plus the cue override. */
export type ToastOptions = ExternalToast & {
  /** A named cue instead of the one this tone would pick, or `false`
   *  for a toast that must arrive silently. */
  cue?: CueName | false;
};

/**
 * What each tone sounds like.
 *
 * `warning` takes the `error` cue and not a warmer one: every warning
 * toast in this app reports a part that did NOT go through — rows
 * skipped, a scan that failed to attach, a reminder throttled. "Something
 * was refused" is what that cue means, scoped to part of a batch.
 *
 * `info` and `loading` are silent, and `custom` too — the banner lane is
 * built on `toast.custom` and sounds itself through `playBannerCue`,
 * with its own gate. Sounding it here would announce every alert twice.
 */
const TONE_CUE = {
  success: 'success',
  error: 'error',
  warning: 'error',
} as const satisfies Record<string, CueName>;

function sound(tone: keyof typeof TONE_CUE, opts?: ToastOptions): void {
  if (opts?.cue === false) return;
  playToastCue(opts?.cue ?? TONE_CUE[tone]);
}

/** `cue` is ours; sonner must never see it. */
function strip(opts?: ToastOptions): ExternalToast | undefined {
  if (!opts) return opts;
  const { cue: _cue, ...rest } = opts;
  return rest;
}

type Message = Parameters<typeof sonnerToast.success>[0];

export const toast = Object.assign(
  (message: Parameters<typeof sonnerToast>[0], opts?: ExternalToast) =>
    sonnerToast(message, opts),
  sonnerToast,
  {
    success: (message: Message, opts?: ToastOptions) => {
      sound('success', opts);
      return sonnerToast.success(message, strip(opts));
    },
    error: (message: Message, opts?: ToastOptions) => {
      sound('error', opts);
      return sonnerToast.error(message, strip(opts));
    },
    warning: (message: Message, opts?: ToastOptions) => {
      sound('warning', opts);
      return sonnerToast.warning(message, strip(opts));
    },
  },
);
