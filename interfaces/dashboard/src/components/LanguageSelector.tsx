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
import { Languages, Check } from 'lucide-react';
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
        body: JSON.stringify({ language: lng }),
      });
    } catch {
      /* best-effort — localStorage persistence keeps the UI consistent */
    }
  }

  return (
    <div ref={wrapperRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={t('language.switch')}
        className="inline-flex items-center gap-1.5 px-2 py-1.5 rounded-md text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition"
      >
        <Languages size={14} />
        <span className="font-mono tracking-wider">{SHORT_CODE[current]}</span>
      </button>
      {open && (
        <div
          role="listbox"
          aria-label={t('language.label')}
          className="absolute right-0 top-full mt-1 z-50 w-44 max-h-80 overflow-auto bg-popover border border-border rounded-md shadow-lg py-1"
        >
          {SUPPORTED_LOCALES.map((lng) => {
            const active = lng === current;
            return (
              <button
                key={lng}
                type="button"
                role="option"
                aria-selected={active}
                onClick={() => pick(lng)}
                className={`w-full text-left flex items-center justify-between gap-2 px-3 py-1.5 text-sm hover:bg-muted transition ${
                  active ? 'text-foreground font-medium' : 'text-muted-foreground'
                }`}
              >
                <span>{NATIVE_LABEL[lng]}</span>
                {active && <Check size={14} aria-hidden />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
