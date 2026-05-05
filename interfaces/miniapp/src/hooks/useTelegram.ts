// Wrapper for the Telegram Mini App JS SDK (window.Telegram.WebApp).
// Returns safe defaults when running outside of Telegram (browser dev).

import { useEffect, useRef, useState } from 'react';

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

/** Wire Telegram's hardware back-button to a handler.  Auto-shows while
 *  active and hides on unmount.  Safe outside Telegram. */
export function useBackButton(active: boolean, handler: () => void): void {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    const bb = window.Telegram?.WebApp?.BackButton;
    if (!bb) return;
    const cb = () => ref.current();
    if (active) {
      bb.onClick(cb);
      bb.show();
      return () => {
        bb.offClick(cb);
        bb.hide();
      };
    }
  }, [active]);
}

interface MainButtonOpts {
  text: string;
  visible: boolean;
  enabled?: boolean;
  loading?: boolean;
  onClick: () => void;
}

/** Drive Telegram's MainButton (the floating CTA at the screen bottom). */
export function useMainButton({ text, visible, enabled = true, loading, onClick }: MainButtonOpts): void {
  const cb = useRef(onClick);
  cb.current = onClick;
  useEffect(() => {
    const mb = window.Telegram?.WebApp?.MainButton;
    if (!mb) return;
    const handler = () => cb.current();
    mb.setText(text);
    mb.onClick(handler);
    if (visible) mb.show(); else mb.hide();
    if (enabled) mb.enable(); else mb.disable();
    if (loading) mb.showProgress(true); else mb.hideProgress();
    return () => {
      mb.offClick(handler);
      mb.hide();
      mb.hideProgress();
    };
  }, [text, visible, enabled, loading]);
}

/** Listens to Telegram viewport changes so layouts can react to the keyboard. */
export function useViewport(): { height: number; stableHeight: number } {
  const tg = window.Telegram?.WebApp;
  const [v, setV] = useState({
    height: tg?.viewportHeight ?? window.innerHeight,
    stableHeight: tg?.viewportStableHeight ?? window.innerHeight,
  });
  useEffect(() => {
    const w = window.Telegram?.WebApp;
    if (!w) return;
    const update = () => setV({
      height: w.viewportHeight,
      stableHeight: w.viewportStableHeight,
    });
    w.onEvent('viewportChanged', update);
    return () => w.offEvent('viewportChanged', update);
  }, []);
  return v;
}

/** Convenience wrappers around HapticFeedback.  All no-op when unsupported. */
export const haptics = {
  light(): void {
    try { window.Telegram?.WebApp?.HapticFeedback?.impactOccurred?.('light'); } catch { /* noop */ }
  },
  medium(): void {
    try { window.Telegram?.WebApp?.HapticFeedback?.impactOccurred?.('medium'); } catch { /* noop */ }
  },
  selection(): void {
    try { window.Telegram?.WebApp?.HapticFeedback?.selectionChanged?.(); } catch { /* noop */ }
  },
  success(): void {
    try { window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred?.('success'); } catch { /* noop */ }
  },
  error(): void {
    try { window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred?.('error'); } catch { /* noop */ }
  },
  warning(): void {
    try { window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred?.('warning'); } catch { /* noop */ }
  },
};
