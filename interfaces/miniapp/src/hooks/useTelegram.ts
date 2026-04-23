// Wrapper for the Telegram Mini App JS SDK (window.Telegram.WebApp).
// Returns safe defaults when running outside of Telegram (browser dev).

function getTgPlatform(raw: string): 'ios' | 'android' | 'base' {
  if (raw === 'ios') return 'ios';
  if (raw === 'android' || raw === 'android_x') return 'android';
  return 'base';
}

export interface TelegramContext {
  /** True when opened inside Telegram Mini App. */
  isMiniApp: boolean;
  /** Raw initData string — send to /api/auth/telegram for JWT. */
  initData: string;
  /** Telegram theme: 'light' | 'dark'. */
  colorScheme: 'light' | 'dark';
  /** Platform for @telegram-apps/telegram-ui AppRoot. */
  platform: 'ios' | 'android' | 'base';
  /** Expand mini app to full height. */
  expand(): void;
  /** Signal to Telegram that the app is ready (hides loading screen). */
  ready(): void;
}

export function useTelegram(): TelegramContext {
  const tg = window.Telegram?.WebApp;
  const isMiniApp = !!(tg?.initData);

  return {
    isMiniApp,
    initData: tg?.initData ?? '',
    colorScheme: tg?.colorScheme ?? 'dark',
    platform: getTgPlatform(tg?.platform ?? ''),
    expand: () => tg?.expand(),
    ready: () => tg?.ready(),
  };
}
