/**
 * Parse the AI parking verdict into its parts.
 *
 * The model is prompted (features/parking/ai_vision.py) to reply in a
 * fixed block:
 *
 *     CLASSIFICATION: UNSAFE
 *     CONFIDENCE: HIGH
 *     REASON: <prose>
 *
 * Shown raw, CLASSIFICATION restated the card's own badge in different
 * words while CONFIDENCE — how sure the verdict is — stayed buried in a
 * collapsed panel.  So: confidence is lifted out for display next to the
 * classification badge, the reason becomes the body, and the duplicated
 * classification is dropped.
 *
 * Split into its own module (with tests) because it is the one piece of
 * real logic on this surface and the piece most likely to break silently
 * when the prompt is reworded — a regex quietly returning '' renders as
 * an empty panel, not as an error.
 */

export interface AiAnalysisParts {
  /** HIGH / MEDIUM / LOW, verbatim from the model.  '' when absent. */
  confidence: string;
  /** The prose explanation.  '' when absent. */
  reason: string;
  /** The whole raw text, but ONLY when neither field parsed — so an
   *  unrecognised format still reaches the user instead of vanishing. */
  rest: string;
}

// Anchored with (?:^|\n) instead of ^ + the /m flag, ON PURPOSE.
//
// With /m, `$` matches at the end of EVERY line — so the lazy
// `([\s\S]+?)` in the reason pattern stopped at the first line break and
// silently discarded the rest.  A two-sentence verdict rendered as one
// sentence, with no error and nothing in the console: the panel just
// looked like the model had been terse.
//
// Dropping /m makes `$` mean end-of-input again, and the "next field"
// half of the lookahead does the real work.  `[A-Z_]{2,}` rather than
// `+` so a stray single capital before a colon inside prose cannot be
// mistaken for a field header.
const CONFIDENCE_RE = /(?:^|\n)[ \t]*CONFIDENCE:[ \t]*(.+)/i;
const REASON_RE = /(?:^|\n)[ \t]*REASON:[ \t]*([\s\S]+?)(?=\n[ \t]*[A-Z_]{2,}:|$)/i;

export function parseAiAnalysis(raw: string): AiAnalysisParts {
  const confidence = CONFIDENCE_RE.exec(raw)?.[1]?.trim() ?? '';
  const reason = REASON_RE.exec(raw)?.[1]?.trim() ?? '';
  const rest = (confidence || reason)
    ? ''
    : raw.trim();
  return { confidence, reason, rest };
}
