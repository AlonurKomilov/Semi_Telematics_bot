/**
 * The one place a callout key becomes rendered words.
 *
 * Every shape (strip, inline note, chip) resolves through this hook so
 * they cannot disagree about what a key says or which tone it wears.
 * Unknown keys degrade to the key itself rather than throwing — a
 * missing translation should look wrong, not take the page down.
 */
import { useTranslation } from 'react-i18next';
import { calloutSpec, type CalloutData } from './calloutCatalog';
import { toneIcon, type Tone } from '../../lib/status';

export interface ResolvedCallout {
  key: string;
  tone: Tone;
  title: string;
  /**
   * The INLINE form — what a row shows where its value would be.
   *
   * Separate from ``title`` because the two answer different
   * questions.  A strip says WHAT is wrong ("No engine data"); a note
   * sits inside a row already labelled "Fuel" or "Oil Pressure" and
   * only needs to say the value is absent for a known reason.
   * Repeating the category there is redundant AND locks the wording
   * to one kind of fault — the next callout would need "No GPS data",
   * "No camera data", and so on down a list nobody maintains.
   *
   * Falls back to ``title`` for callouts that never render inline
   * (the mileage caveats are chips), so declaring it is optional.
   */
  short: string;
  /**
   * The three questions a reader has, as separate answers rather than
   * one paragraph they must mine.  Any may be empty — a caveat that
   * qualifies a number has no action, and the strip simply omits the
   * line rather than printing an empty label.
   *
   *   why      what is happening, and why the value is missing
   *   affects  which readings this costs them — the impact, stated
   *            rather than buried at the end of a sentence
   *   act      what to do about it
   */
  why: string;
  affects: string;
  act: string;
  Icon: ReturnType<typeof toneIcon>;
  /** Only `guidance` may ever be dismissed — see calloutCatalog. */
  dismissible: boolean;
}

export function useCallout(c: CalloutData): ResolvedCallout {
  const { t } = useTranslation();
  const spec = calloutSpec(c.key);
  const tone: Tone = spec?.severity ?? 'info';
  // `since` and the backend's params are merged into one substitution
  // bag so copy can say "since {{since}}" or "gateway {{gateway}}"
  // without the component knowing which callout it is rendering.
  const vars = { ...(c.params ?? {}), since: c.since ?? '' };
  const shortKey = `callout.${c.key}.short`;
  const short = t(shortKey, vars);
  // i18next echoes the key back when a string is missing; treat that
  // as "this callout does not answer that question" and let the strip
  // omit the line.
  const line = (name: string): string => {
    const key = `callout.${c.key}.${name}`;
    const v = t(key, vars);
    return v === key ? '' : v;
  };
  return {
    key: c.key,
    tone,
    title: t(`callout.${c.key}.title`, vars),
    // i18next echoes the key back when a string is missing — that is
    // the "not declared" signal, so fall back to the title.
    short: short === shortKey ? t(`callout.${c.key}.title`, vars) : short,
    why: line('why'),
    affects: line('affects'),
    act: line('do'),
    Icon: toneIcon(tone),
    dismissible: spec?.kind === 'guidance',
  };
}
