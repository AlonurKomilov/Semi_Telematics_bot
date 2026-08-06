/**
 * Top-bar language selector — dropdown of the 9 supported locales.
 *
 * - Renders the active language's native name as the trigger.
 * - On selection: switches i18next, which persists to localStorage
 *   (key: "app.locale"), and posts the choice to /api/user/locale
 *   so the bot side picks up the same preference.
 * - Native names are sourced from each locale's own `language.*` keys
 *   so the dropdown shows scripts users actually read.
 */
import { useTranslation } from 'react-i18next';
import { useState, useRef, useEffect } from 'react';
import { Check } from 'lucide-react';
import { ActionMenu, type MenuAction } from './ui/context-menu';
import { SUPPORTED_LOCALES, type Locale } from '../i18n';
import { apiJSON } from '../api/client';

// Native-script labels — these intentionally stay constant regardless
// of the active UI language so users always see their target language
// in its own script in the dropdown.
const NATIVE_LABEL: Record<Locale, string> = {
  en: 'English',
  ru: 'Русский',
  uk: 'Українська',
  uz: 'Oʻzbek',
  es: 'Español',
  fr: 'Français',
  pa: 'ਪੰਜਾਬੀ',
  so: 'Soomaali',
  am: 'አማርኛ',
};

const SHORT_CODE: Record<Locale, string> = {
  en: 'EN', ru: 'RU', uk: 'UK', uz: 'UZ',
  es: 'ES', fr: 'FR', pa: 'PA', so: 'SO', am: 'AM',
};

export function LanguageSelector() {
  const { i18n, t } = useTranslation();
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);

  const current = (i18n.resolvedLanguage as Locale) || 'en';

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [open]);

  async function pick(lng: Locale) {
    setOpen(false);
    if (lng === current) return;
    await i18n.changeLanguage(lng);
    // Mirror to backend so the Telegram bot side picks up the same
    // locale on the next interaction. Reuses the existing
    // /user/preferences endpoint that the miniapp also writes to.
    try {
      await apiJSON<{ ok: boolean }>('/user/preferences', {
        method: 'PUT',
        body: { language: lng },
      });
    } catch {
      /* best-effort — localStorage persistence keeps the UI consistent */
    }
  }

  // ActionMenu, not a hand-rolled dropdown.  The old one was
  // ``absolute right-0 … w-44`` — it extended 176px LEFT from the
  // trigger with no idea where the viewport edge was, so on a phone the
  // menu ran off the left of the screen and the language names were cut
  // in half ("…ний", "…нська").  Base UI's positioner shifts to stay on
  // screen, which is the entire reason the dashboard rule says never
  // hand-roll a menu.
  //
  // Keeping the tick: ``MenuAction.icon`` is a fully-styled node, so the
  // active language still shows a ✓ — as a leading icon now rather than
  // a trailing one, which is what the shared list renders.
  const items: MenuAction[] = SUPPORTED_LOCALES.map((lng) => ({
    key: lng,
    label: NATIVE_LABEL[lng],
    icon: lng === current
      ? <Check size={14} aria-hidden className="text-foreground" />
      : <span aria-hidden className="inline-block size-3.5" />,
    onSelect: () => pick(lng),
  }));

  return (
    <ActionMenu items={items} align="end">
      <button
        type="button"
        aria-label={t('language.switch')}
        className="inline-flex items-center px-2 py-1.5 rounded-md text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition"
      >
        <span className="font-mono tracking-wider">{SHORT_CODE[current]}</span>
      </button>
    </ActionMenu>
  );
}
