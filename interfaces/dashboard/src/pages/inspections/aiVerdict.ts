import type { PTIInspectionMedia } from '../../types';

/**
 * Shared helpers for reading the per-photo AI vision verdict stored on
 * inspection media rows.  The verdict is a JSON string written by the
 * backend (``capabilities/pti/ai_review.py``); the dashboard switches
 * on the small, stable ``verdict`` vocabulary rather than parsing the
 * free-text summary.
 */

export type Verdict = 'ok' | 'possible_issue' | 'likely_defect' | 'unclear';

export interface AIVerdict {
  verdict: Verdict;
  confidence?: string;
  summary?: string;
  model?: string;
}

export function parseVerdict(m: PTIInspectionMedia): AIVerdict | null {
  if (!m.ai_review_result) return null;
  try {
    const v = JSON.parse(m.ai_review_result) as AIVerdict;
    return v && v.verdict ? v : null;
  } catch {
    return null;
  }
}

/** A verdict the fleet reviewer should look at twice. */
export function isFlagged(v: AIVerdict | null): boolean {
  return v?.verdict === 'possible_issue' || v?.verdict === 'likely_defect';
}

export const VERDICT_TONE: Record<Verdict, string> = {
  ok:             'bg-green-500/15 text-green-700 dark:text-green-400',
  possible_issue: 'bg-amber-500/15 text-amber-700 dark:text-amber-400',
  likely_defect:  'bg-red-500/15 text-red-700 dark:text-red-400',
  unclear:        'bg-muted text-muted-foreground',
};

export const VERDICT_EMOJI: Record<Verdict, string> = {
  ok: '✓', possible_issue: '⚠', likely_defect: '✕', unclear: '?',
};
