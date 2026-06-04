import type { PTIInspectionMedia } from '../../types';
import { type Tone, toneClasses, toneText } from '../../lib/status';

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

/**
 * AI verdict → semantic tone.  Owns the verdict→colour mapping in one
 * place (like ``statusTone`` does for domain statuses) so a verdict
 * can't render amber in the item list and red in the gallery dot.
 */
const VERDICT_TONE_MAP: Record<Verdict, Tone> = {
  ok:             'ok',
  possible_issue: 'warn',
  likely_defect:  'danger',
  unclear:        'neutral',
};

export function verdictTone(verdict: Verdict): Tone {
  return VERDICT_TONE_MAP[verdict];
}

/** Soft-pill classes for a verdict (tinted fill + solid text + border). */
export function verdictClasses(verdict: Verdict): string {
  return toneClasses(verdictTone(verdict));
}

/** Solid foreground colour class for a verdict — icons, dots. */
export function verdictText(verdict: Verdict): string {
  return toneText(verdictTone(verdict));
}

export const VERDICT_EMOJI: Record<Verdict, string> = {
  ok: '✓', possible_issue: '⚠', likely_defect: '✕', unclear: '?',
};
