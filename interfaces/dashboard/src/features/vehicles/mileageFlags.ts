/**
 * Mileage coverage flags — the boundary between this feature's WIRE
 * and the shared callout vocabulary.
 *
 * The mileage endpoints stamp a short `flag` string on a row
 * (`features/vehicles/router.py::_merge_unit_rows`).  That wire value
 * is deliberately untouched: this file only translates it into the
 * callout key the shared catalog knows, so the words, tone and icon
 * come from one place instead of being re-typed per surface.
 *
 * The labels and explanations that used to live here as FLAG_NOTE now
 * live in the locale files under `callout.mileage.*` — which is also
 * how they finally became translatable (they were English-only for
 * every one of the nine languages the dashboard ships).
 */
import type { CalloutData } from '../../components/callouts';

export type MileageFlag =
  | '' | 'partial' | 'reset' | 'catchup' | 'estimated' | 'device_change' | 'rebase';

/** `"device_change"` → `"mileage.device_change"`, or null when absent. */
export function flagCallout(flag: string | null | undefined): CalloutData | null {
  const f = String(flag ?? '').trim();
  if (!f) return null;
  return { key: `mileage.${f}` };
}
