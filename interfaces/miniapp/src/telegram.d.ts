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

interface TelegramBackButton {
  isVisible: boolean;
  show(): void;
  hide(): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
}

interface TelegramMainButton {
  text: string;
  color?: string;
  textColor?: string;
  isVisible: boolean;
  isActive: boolean;
  isProgressVisible: boolean;
  setText(text: string): void;
  setParams(params: {
    text?: string;
    color?: string;
    text_color?: string;
    is_active?: boolean;
    is_visible?: boolean;
  }): void;
  show(): void;
  hide(): void;
  enable(): void;
  disable(): void;
  showProgress(leaveActive?: boolean): void;
  hideProgress(): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
}

interface TelegramBiometricManager {
  isInited: boolean;
  isBiometricAvailable: boolean;
  init(cb?: () => void): void;
  authenticate(
    params: { reason?: string },
    cb?: (success: boolean) => void,
  ): void;
}

interface TelegramCloudStorage {
  setItem(key: string, value: string, cb?: (err: Error | null, ok: boolean) => void): void;
  getItem(key: string, cb: (err: Error | null, value: string) => void): void;
  removeItem(key: string, cb?: (err: Error | null, ok: boolean) => void): void;
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
  onEvent(eventType: string, cb: () => void): void;
  offEvent(eventType: string, cb: () => void): void;
  /** Sets the app background color. Accepts a hex color or 'bg_color' | 'secondary_bg_color'. */
  setBackgroundColor(color: string): void;
  /** Sets the mini app header color. Accepts 'bg_color' | 'secondary_bg_color' or a hex color (v7.10+). */
  setHeaderColor(colorOrKey: 'bg_color' | 'secondary_bg_color' | string): void;
  /** Native haptic feedback (Bot API 6.1+). All methods are no-ops on
      desktop / unsupported clients. */
  HapticFeedback?: {
    impactOccurred(style: 'light' | 'medium' | 'heavy' | 'rigid' | 'soft'): void;
    notificationOccurred(type: 'error' | 'success' | 'warning'): void;
    selectionChanged(): void;
  };
  BackButton?: TelegramBackButton;
  MainButton?: TelegramMainButton;
  BiometricManager?: TelegramBiometricManager;
  CloudStorage?: TelegramCloudStorage;
  shareToStory?(mediaUrl: string, params?: { text?: string }): void;
}

declare global {
  interface Window {
    Telegram?: {
      WebApp?: TelegramWebApp;
    };
  }
}

export {};
