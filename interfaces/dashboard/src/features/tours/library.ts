/**
 * The library's reading of the catalog — pure, so it is a unit test away
 * from proof.  What Claude Code's onboarding checklist taught us, kept
 * to what our tours already know: progress that counts what the person
 * ALREADY does (adoption by signals, not only "Done" by running), one
 * "next" that is chosen for them, and the done ones out of the way.
 */
import type { TourCtx, TourSpec, TourState } from '../../components/tour';

export type LibraryStatus = 'new' | 'skipped' | 'done' | 'adopted';

export interface LibraryRow<T extends TourSpec, F> {
  tour: T;
  feature: F;
  status: LibraryStatus;
}

export interface LibraryModel<T extends TourSpec, F> {
  /** Every reachable tour, the open ones first, the settled ones last. */
  rows: LibraryRow<T, F>[];
  /** Counted as done: run to the end, OR already done on one's own. */
  done: number;
  total: number;
  /** The first tour the person has neither run nor answered — or null. */
  next: LibraryRow<T, F> | null;
}

const ORDER: Record<LibraryStatus, number> = { new: 0, skipped: 1, adopted: 2, done: 3 };

export function libraryModel<T extends TourSpec, F>(
  reachable: { tour: T; feature: F }[],
  state: TourState,
  signals: TourCtx['signals'],
): LibraryModel<T, F> {
  const ctx: TourCtx = { count: 0, canCreate: false, signals };
  const rows: LibraryRow<T, F>[] = reachable.map(({ tour, feature }) => {
    const verdict = state[tour.key]?.s;
    // Adoption outranks the verdicts: a person who does the thing on
    // their own has finished the lesson whether or not they ran it.
    const status: LibraryStatus = tour.adopted?.(ctx) ? 'adopted'
      : verdict === 'done' ? 'done'
      : verdict === 'skipped' ? 'skipped'
      : 'new';
    return { tour, feature, status };
  });
  const sorted = [...rows].sort((a, b) => ORDER[a.status] - ORDER[b.status]);
  const done = rows.filter((r) => r.status === 'done' || r.status === 'adopted').length;
  // Skipped is an answer; "next" never nags past it.
  const next = sorted.find((r) => r.status === 'new') ?? null;
  return { rows: sorted, done, total: rows.length, next };
}

/** The signal pairs every reachable tour declares — one request for the page. */
export function signalPairs(reachable: { tour: TourSpec }[]): string[] {
  return [...new Set(reachable.flatMap(({ tour }) => tour.signals ?? []))];
}
