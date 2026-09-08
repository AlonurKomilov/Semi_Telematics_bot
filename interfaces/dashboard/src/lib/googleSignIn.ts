/**
 * Google Identity Services, loaded once and asked for one thing: a
 * button that hands us an ID token.
 *
 * No One Tap, no auto-select, no script anywhere but the page that
 * draws the button — a sign-in prompt that appears on its own is a
 * prompt somebody did not ask for.  The token goes to OUR API, which
 * verifies it against OUR client id; nothing here talks to Google
 * beyond rendering the button.
 */

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (cfg: {
            client_id: string;
            callback: (r: { credential: string }) => void;
            auto_select?: boolean;
            cancel_on_tap_outside?: boolean;
            ux_mode?: 'popup' | 'redirect';
          }) => void;
          renderButton: (el: HTMLElement, opts: Record<string, string | number>) => void;
          disableAutoSelect: () => void;
        };
      };
    };
  }
}

const SRC = 'https://accounts.google.com/gsi/client';
let loading: Promise<void> | null = null;

/** Resolve when the GIS script is on the page (injected once). */
export function loadGoogleIdentity(): Promise<void> {
  if (window.google?.accounts?.id) return Promise.resolve();
  if (loading) return loading;
  loading = new Promise<void>((resolve, reject) => {
    const s = document.createElement('script');
    s.src = SRC;
    s.async = true;
    s.defer = true;
    s.onload = () => resolve();
    s.onerror = () => { loading = null; reject(new Error('Google sign-in could not be loaded')); };
    document.head.appendChild(s);
  });
  return loading;
}

/**
 * Draw Google's button into ``el`` and call ``onCredential`` with the
 * ID token when a person picks an account.  Re-initialises on every
 * call: the callback closes over the caller's current state (remember
 * me, mode), and a stale one would sign in with yesterday's choices.
 */
export async function renderGoogleButton(
  el: HTMLElement,
  clientId: string,
  onCredential: (credential: string) => void,
  opts: { text?: 'signin_with' | 'continue_with' | 'signup_with'; width?: number } = {},
): Promise<void> {
  await loadGoogleIdentity();
  const g = window.google?.accounts?.id;
  if (!g) throw new Error('Google sign-in is unavailable');
  g.initialize({
    client_id: clientId,
    callback: (r) => { if (r?.credential) onCredential(r.credential); },
    auto_select: false,
    cancel_on_tap_outside: true,
    ux_mode: 'popup',
  });
  el.innerHTML = '';
  g.renderButton(el, {
    type: 'standard', theme: 'outline', size: 'large', shape: 'rectangular',
    text: opts.text ?? 'signin_with', logo_alignment: 'left',
    width: opts.width ?? 320,
  });
}
