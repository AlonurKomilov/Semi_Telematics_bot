/**
 * i18n bootstrap for the dashboard.
 *
 * - One namespace ("translation") so callers can use plain `t("nav.fleet")`
 *   without specifying a namespace.
 * - All 9 locales bundled at build time. Bundle size impact is small
 *   (~10 KB gzipped each); much simpler than runtime fetching given
 *   the editor lives behind auth.
 * - Detection order: localStorage → browser `navigator.language` →
 *   "en" fallback.  The selected locale is also persisted to
 *   localStorage so it survives reloads.
 * - Backend sync: when the user changes language via the top-bar
 *   selector, capabilities elsewhere may also POST it to the backend
 *   user profile so the bot side picks the same locale.
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

void i18n
  .use(LanguageDetector)
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
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
      lookupLocalStorage: 'app.locale',
    },
    returnNull: false,
  });

export default i18n;
