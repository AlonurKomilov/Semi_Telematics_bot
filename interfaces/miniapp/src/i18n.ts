/**
 * i18n bootstrap for the Telegram mini-app.
 *
 * Mirrors the dashboard setup: 9 locales bundled at build time, single
 * "translation" namespace, localStorage persistence keyed under
 * "app.locale".  Detection order also considers Telegram's
 * ``initDataUnsafe.user.language_code`` so a user whose Telegram is
 * set to Russian sees the mini-app in Russian on first launch with
 * zero configuration.
 */
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

import en from './locales/en.json';
import ru from './locales/ru.json';
import uk from './locales/uk.json';
import uz from './locales/uz.json';
import es from './locales/es.json';
import fr from './locales/fr.json';
import pa from './locales/pa.json';
import so from './locales/so.json';
import am from './locales/am.json';

export const SUPPORTED_LOCALES = [
  'en', 'ru', 'uk', 'uz', 'es', 'fr', 'pa', 'so', 'am',
] as const;

export type Locale = (typeof SUPPORTED_LOCALES)[number];

// Custom detector that reads Telegram WebApp init data first.
const TelegramDetector = {
  name: 'telegram',
  lookup() {
    try {
      const user = window.Telegram?.WebApp?.initDataUnsafe?.user as
        | { language_code?: string }
        | undefined;
      const lang = user?.language_code;
      if (typeof lang === 'string' && lang) return lang.toLowerCase().slice(0, 2);
    } catch {
      /* ignore */
    }
    return undefined;
  },
  cacheUserLanguage() { /* no-op — Telegram detector is read-only */ },
};

const detector = new LanguageDetector();
detector.addDetector(TelegramDetector);

void i18n
  .use(detector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      ru: { translation: ru },
      uk: { translation: uk },
      uz: { translation: uz },
      es: { translation: es },
      fr: { translation: fr },
      pa: { translation: pa },
      so: { translation: so },
      am: { translation: am },
    },
    fallbackLng: 'en',
    supportedLngs: SUPPORTED_LOCALES as unknown as string[],
    nonExplicitSupportedLngs: true,
    interpolation: { escapeValue: false },
    detection: {
      // Telegram first, then any prior user choice, then browser.
      order: ['localStorage', 'telegram', 'navigator'],
      caches: ['localStorage'],
      lookupLocalStorage: 'app.locale',
    },
    returnNull: false,
  });

export default i18n;
