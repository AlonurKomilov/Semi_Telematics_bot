/**
 * Single source of truth for the dashboard's language picker.
 *
 * Mirrors ``capabilities.localization.i18n.SUPPORTED_LANGUAGES`` /
 * ``LANGUAGE_NAMES`` on the backend.  The codes here are the same
 * ones the API regex accepts, the bot keyboards offer, and the
 * miniapp uses — there's exactly one whitelist across the stack.
 *
 * Display rules:
 *   - Use each language's *own* native script as the label, so a
 *     Punjabi speaker instantly recognises ਪੰਜਾਬੀ regardless of which
 *     language is currently active in the UI.  Latin-script "Punjabi"
 *     defeats the point of a language picker.
 *   - No flag emojis — they render as country-code letters
 *     (``us`` / ``ua`` / ``et``) on Windows + some Linux fonts, and
 *     a country flag isn't a clean proxy for a language anyway
 *     (e.g. Amharic with an Ethiopian flag, Punjabi with an Indian
 *     flag, English with a US flag exclude millions of speakers).
 *   - Order: English first (default + most-common), then the other
 *     8 sorted alphabetically by their native label.
 */

export interface LanguageOption {
  /** ISO 639-1 code — stored in ``users.language`` and used as the
   *  i18next resource key on the dashboard + miniapp. */
  value: string;
  /** Native-script display label. */
  label: string;
}

export const LANGUAGE_OPTIONS: LanguageOption[] = [
  { value: 'en', label: 'English' },
  { value: 'es', label: 'Español' },
  { value: 'fr', label: 'Français' },
  { value: 'pa', label: 'ਪੰਜਾਬੀ' },
  { value: 'ru', label: 'Русский' },
  { value: 'so', label: 'Soomaali' },
  { value: 'uk', label: 'Українська' },
  { value: 'uz', label: 'Oʻzbekcha' },
  { value: 'am', label: 'አማርኛ' },
];

const _BY_VALUE: Record<string, LanguageOption> = Object.fromEntries(
  LANGUAGE_OPTIONS.map((o) => [o.value, o]),
);

/** Return the native-script label for a code, or the code itself when
 *  unknown (legacy values that pre-date the current whitelist). */
export function languageLabel(code: string | null | undefined): string {
  if (!code) return 'English';
  return _BY_VALUE[code]?.label ?? code;
}
