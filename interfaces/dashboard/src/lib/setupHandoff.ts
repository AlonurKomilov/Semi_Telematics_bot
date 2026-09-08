/**
 * The hand-off between a Google company sign-up and the page that
 * finishes it.
 *
 * /auth/register-google answers with a fifteen-minute ``aud=setup``
 * token; /complete-setup needs it on its next request.  This is the
 * ONE place that token touches browser storage, and it is
 * sessionStorage on purpose: the token is a bearer credential, not a
 * preference — it must not sync across devices, must not outlive the
 * tab, and must never sit beside the real session token.  It is
 * removed the moment setup completes.
 */

const TOKEN_KEY = '4truck.setupToken';
const EMAIL_KEY = '4truck.setupEmail';

export function stashSetupHandoff(token: string, email: string): void {
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
    sessionStorage.setItem(EMAIL_KEY, email);
  } catch { /* private mode: /complete-setup sends the person back to one Google click */ }
}

export function readSetupHandoff(): { token: string; email: string } {
  try {
    return {
      token: sessionStorage.getItem(TOKEN_KEY) || '',
      email: sessionStorage.getItem(EMAIL_KEY) || '',
    };
  } catch {
    return { token: '', email: '' };
  }
}

export function clearSetupHandoff(): void {
  try {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(EMAIL_KEY);
  } catch { /* nothing to clear */ }
}
