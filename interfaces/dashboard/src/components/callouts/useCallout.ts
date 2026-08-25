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

/**
 * The vocabulary of labelled lines, in the order they render.
 *
 * Shared across every callout so the FORM is learned once: a reader
 * who has met one of these strips knows where to look on the next.
 * The order is the order a person asks the questions —
 *
 *   where    which record this is about (omitted where the surface
 *            already says it: a vehicle's own page); on an account-
 *            wide list it is the difference between a question and
 *            an unanswerable question
 *   changed  the old → new pair, alone, because on a change event it
 *            IS the evidence the decision is made on
 *   why      what it means / what is happening
 *   affects  what it costs — stated, not buried at the end of a
 *            sentence
 *   do       what to do about it; omitted when the strip's own
 *            buttons already are the answer
 *
 * Adding one is deliberately a small, visible act: a name here, a
 * `callout.labels.<name>` string in nine locales, and CALLOUT_FIELDS
 * in tests/test_callouts.py (which parses THIS array, so the two
 * cannot drift).  That friction is the point — a vocabulary that
 * grows a word per callout teaches the reader nothing.
 */
export const CALLOUT_LINES = ['where', 'changed', 'why', 'affects', 'do'] as const;

export type CalloutLineName = (typeof CALLOUT_LINES)[number];

export interface CalloutLine {
  name: CalloutLineName;
  /** Already translated — the strip renders it verbatim. */
  label: string;
  value: string;
}

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
   * The labelled lines this callout answers, in canonical order.
   *
   * Open, not fixed at three.  `why`/`affects`/`do` is a FAULT
   * vocabulary — a cause, a cost, a remedy — and it fits a fault
   * exactly.  It does not fit an EVENT: a changed VIN has no remedy
   * to perform, its headline fact is the old→new pair, and pressing
   * that pair into a sentence labelled "Why" buries the one thing
   * the reader is deciding on.  So the vocabulary is shared and the
   * SELECTION is per callout: answer the questions your statement
   * actually raises, omit the rest.
   *
   * A line exists iff its copy exists — nothing else declares it, so
   * a locale can never promise a line the resolver won't render (see
   * the two guards in tests/test_callouts.py).
   */
  lines: CalloutLine[];
  /**
   * One sentence for a hover, where no strip fits.
   *
   * The chip and the inline note have room for a label and nothing
   * else, so their tooltip carries the explanation — the `why` line,
   * or the title when the callout answers no questions at all.  Named
   * for what it is FOR rather than which line it comes from: those two
   * shapes want "explain this in one line", and if that answer ever
   * moves to a different line, it moves here and not at two call
   * sites.
   */
  explanation: string;
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
  const lines = CALLOUT_LINES.flatMap((name) => {
    const value = line(name);
    return value ? [{ name, label: t(`callout.labels.${name}`), value }] : [];
  });
  const title = t(`callout.${c.key}.title`, vars);
  return {
    key: c.key,
    tone,
    title,
    // i18next echoes the key back when a string is missing — that is
    // the "not declared" signal, so fall back to the title.
    short: short === shortKey ? title : short,
    lines,
    explanation: lines.find((l) => l.name === 'why')?.value ?? title,
    Icon: toneIcon(tone),
    dismissible: spec?.kind === 'guidance',
  };
}
