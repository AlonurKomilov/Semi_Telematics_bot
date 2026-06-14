import { useEffect, useRef } from 'react';

// Cloudflare Turnstile widget.  Loaded via the global script (added in
// index.html when a site key is configured); we use the explicit render
// API so React owns the DOM node lifecycle without fighting the auto-
// render attribute scan.
//
// When ``siteKey`` is empty (Turnstile not configured), the widget is
// a no-op — the parent form should still submit, and the backend
// verifier returns True without ``TURNSTILE_SECRET_KEY``.

declare global {
  interface Window {
    turnstile?: {
      render: (
        el: HTMLElement | string,
        opts: {
          sitekey: string;
          callback?: (token: string) => void;
          'error-callback'?: (errorCode?: string) => void;
          'expired-callback'?: () => void;
          theme?: 'light' | 'dark' | 'auto';
          appearance?: 'always' | 'execute' | 'interaction-only';
        },
      ) => string;
      remove: (widgetId: string) => void;
      reset: (widgetId?: string) => void;
    };
  }
}

interface Props {
  siteKey: string;
  onToken: (token: string) => void;
  onExpire?: () => void;
  // Bump this number to force a fresh challenge — Turnstile tokens are
  // single-use, so the parent should increment it after every submit
  // attempt (success or failure) or the next POST sends a burnt token
  // and Cloudflare rejects it with timeout-or-duplicate.
  resetNonce?: number;
}

export default function TurnstileWidget({ siteKey, onToken, onExpire, resetNonce }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const widgetIdRef = useRef<string | null>(null);

  // Callbacks live in refs so the mount effect depends ONLY on siteKey.
  // Parents naturally pass inline arrows (new identity every render);
  // if those were effect deps, every keystroke in the surrounding form
  // would tear the widget down and re-mount it — the user sees an
  // endless "Verifying…" loop and never gets a token.
  const onTokenRef = useRef(onToken);
  const onExpireRef = useRef(onExpire);
  onTokenRef.current = onToken;
  onExpireRef.current = onExpire;

  useEffect(() => {
    if (!siteKey) return;

    let cancelled = false;
    let pollInterval: ReturnType<typeof setInterval> | undefined;
    let pollTimeout: ReturnType<typeof setTimeout> | undefined;

    const mount = () => {
      if (cancelled || !window.turnstile || !containerRef.current) return;
      // Defensive: don't double-mount if a previous effect run already
      // bound a widget to this element (StrictMode double-invoke).
      if (widgetIdRef.current) return;
      widgetIdRef.current = window.turnstile.render(containerRef.current, {
        sitekey: siteKey,
        theme: 'auto',
        callback: (token) => onTokenRef.current(token),
        'expired-callback': () => onExpireRef.current?.(),
        'error-callback': (errorCode) => {
          // Surface the Cloudflare error code — it pinpoints the cause
          // (110200 = hostname not in the widget's allowlist, 1101xx =
          // bad sitekey, 300xxx/600xxx = client-side interference from
          // extensions).  Without this the user only sees a generic
          // "Verification failed" and we're debugging blind.
          console.warn('[turnstile] challenge error, code:', errorCode ?? 'unknown');
          onExpireRef.current?.();
        },
      });
    };

    if (window.turnstile) {
      mount();
    } else {
      // Wait for the global script (added by index.html) to finish
      // loading.  Poll cheaply rather than wiring an event listener —
      // the script is in <head> with async, so it usually resolves
      // before this effect runs anyway.
      pollInterval = setInterval(() => {
        if (window.turnstile) {
          if (pollInterval) clearInterval(pollInterval);
          mount();
        }
      }, 100);
      // Guard against the script never loading (CSP block, ad-blocker).
      pollTimeout = setTimeout(() => {
        if (pollInterval) clearInterval(pollInterval);
      }, 10_000);
    }

    return () => {
      cancelled = true;
      if (pollInterval) clearInterval(pollInterval);
      if (pollTimeout) clearTimeout(pollTimeout);
      if (widgetIdRef.current && window.turnstile) {
        try {
          window.turnstile.remove(widgetIdRef.current);
        } catch {
          /* widget already gone */
        }
        widgetIdRef.current = null;
      }
    };
  }, [siteKey]);

  // Single-use token refresh: parent bumps resetNonce after a submit
  // attempt; we ask Turnstile for a fresh challenge in-place instead of
  // a full remount (faster, no layout shift).
  useEffect(() => {
    if (!resetNonce) return;
    if (widgetIdRef.current && window.turnstile) {
      try {
        window.turnstile.reset(widgetIdRef.current);
      } catch {
        /* widget not mounted yet — fresh mount will issue a token anyway */
      }
    }
  }, [resetNonce]);

  if (!siteKey) return null;

  return <div ref={containerRef} className="flex justify-center" />;
}
