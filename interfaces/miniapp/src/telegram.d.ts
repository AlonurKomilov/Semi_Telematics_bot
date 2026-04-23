// Type declarations for the Telegram Mini App JS SDK
// loaded via <script src="https://telegram.org/js/telegram-web-app.js">

interface TelegramThemeParams {
  bg_color?: string;
  text_color?: string;
  hint_color?: string;
  link_color?: string;
  button_color?: string;
  button_text_color?: string;
  secondary_bg_color?: string;
  header_bg_color?: string;
  accent_text_color?: string;
  section_bg_color?: string;
  section_header_text_color?: string;
  subtitle_text_color?: string;
  destructive_text_color?: string;
}

interface TelegramWebApp {
  initData: string;
  initDataUnsafe: Record<string, unknown>;
  version: string;
  platform: string;
  colorScheme: 'light' | 'dark';
  themeParams: TelegramThemeParams;
  isExpanded: boolean;
  viewportHeight: number;
  viewportStableHeight: number;
  expand(): void;
  ready(): void;
  close(): void;
  /** Sets the app background color. Accepts a hex color or 'bg_color' | 'secondary_bg_color'. */
  setBackgroundColor(color: string): void;
  /** Sets the mini app header color. Accepts 'bg_color' | 'secondary_bg_color' or a hex color (v7.10+). */
  setHeaderColor(colorOrKey: 'bg_color' | 'secondary_bg_color' | string): void;
}

declare global {
  interface Window {
    Telegram?: {
      WebApp?: TelegramWebApp;
    };
  }
}

export {};
