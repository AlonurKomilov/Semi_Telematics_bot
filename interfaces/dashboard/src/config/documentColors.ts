/**
 * Colours for things drawn to a canvas and kept as a DOCUMENT.
 *
 * Separate from the CSS tokens for a reason that is not the map's: a
 * signature is captured to a PNG and stored against an FMCSA
 * application. The file outlives the session that made it, gets emailed,
 * printed and read years later — so its ink cannot come from a token
 * that follows whoever happened to be in dark mode that afternoon.
 * `getContext('2d')` cannot resolve `var()` either, but that is the
 * lesser reason; even if it could, the answer would still be a literal.
 *
 * There were two signature pads with two different inks — `#0a0a0a` in
 * the inspection pad and `#1e293b`, a blue-grey, in the public applicant
 * form. Nothing documented the difference and nothing depended on it, so
 * it was drift rather than a decision. One ink now.
 */

/** Near-black. A signature, not a UI element — no hue. */
export const SIGNATURE_INK = '#0a0a0a';

/** The sheet the signature is written on, and the PNG's background. */
export const SIGNATURE_PAPER = '#ffffff';
