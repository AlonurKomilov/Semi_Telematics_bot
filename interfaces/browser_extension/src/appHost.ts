/**
 * WHICH 4truck host a deep link from the panel should open.
 *
 * The apex serves almost nothing.  nginx hands `4truck.us` exactly
 * `/`, `/login`, the password and verify pages, `/extension/connect`,
 * `/assets/` and the favicons; every other path answers 404.  The app
 * itself lives on per-persona subdomains — `dash.`, `fleet.`,
 * `dispatch.`, `safety.`, `hr.`, `accounting.`, `recruiter.` — which
 * all serve the SAME bundle, with the dashboard reading the hostname to
 * pre-select the matching shell.
 *
 * The panel did not know that.  `DASHBOARD_BASE` is the apex, which is
 * correct for the two auth pages it was first written for, and every
 * deep link built on it went to a 404: the overlay card's "Web" button
 * (`/vehicles/001?company=PTG`), and both "open Inventory on the web"
 * doors.  The owner found the first one; the other two had the same
 * bug and nobody had pressed them.
 *
 * So there are TWO bases, and they are different things:
 *   DASHBOARD_BASE — where a person signs in and connects.  Apex.
 *   appBase        — where their app is.  Persona subdomain.
 */
import { DASHBOARD_BASE } from './connect';

/** The production apex.  A base that is not this — localhost, a
 *  preview host — has no persona subdomains, and rewriting it would
 *  break the dev loop for no gain. */
const APEX = '4truck.us';

/**
 * Role → subdomain label.
 *
 * MIRROR of `ROLE_HOST` in the dashboard's
 * `src/context/RoleViewContext.tsx`, which docs/architecture/PERSONA.md
 * names as the place to edit when a persona is added.  The dashboard
 * already keeps a second copy in `AuthContext.tsx` and says the
 * duplication is deliberate; this is the third, and it is across a
 * package boundary, so it is guarded rather than trusted —
 * tests/test_persona_hosts_agree.py reads all three and fails when they
 * drift.
 *
 * `dispatcher` → `dispatch` is not a typo: the ROLE is "dispatcher",
 * the HOST label is "dispatch".
 */
export const ROLE_SUBDOMAIN: Record<string, string> = {
  owner: 'dash',
  admin: 'dash',
  fleet: 'fleet',
  dispatcher: 'dispatch',
  safety: 'safety',
  hr: 'hr',
  accounting: 'accounting',
  recruiter: 'recruiter',
  driver: 'dash',
};

/** Where every role that has no subdomain of its own goes.  It serves
 *  the same bundle, so this is a correct answer and never a broken
 *  one — just not the person's branded URL. */
const DEFAULT_LABEL = 'dash';

/**
 * The host this person's app lives on.
 *
 * An unknown role lands on `dash.`, which works for everyone: the
 * persona subdomains are a hint to the shell, not a permission.
 */
export function appBaseFor(role: string, base: string = DASHBOARD_BASE): string {
  try {
    const u = new URL(base);
    const apex = u.hostname.toLowerCase().replace(/^www\./, '');
    if (apex !== APEX) return base;   // dev or preview — leave it alone
    const label = ROLE_SUBDOMAIN[(role || '').toLowerCase()] ?? DEFAULT_LABEL;
    return `${u.protocol}//${label}.${APEX}`;
  } catch {
    // A base that will not parse is a misconfiguration, not a reason to
    // hand back nothing: the caller is about to open a tab.
    return base;
  }
}

/**
 * One key, three readers.
 *
 * The panel learns the role from `/extension/me` and writes the
 * resolved base here; the content script on google.com/maps and the
 * background worker both read it, because neither of them can ask.
 * The same shape `ACTIVE_FEATURE_KEY` already uses next door.
 */
export const APP_BASE_KEY = 'appBase';

export async function rememberAppBase(role: string): Promise<void> {
  try {
    await chrome.storage.local.set({ [APP_BASE_KEY]: appBaseFor(role) });
  } catch { /* the fallback below is a working answer */ }
}

/** The stored base, or the one that works for everybody. */
export async function readAppBase(): Promise<string> {
  try {
    const got = await chrome.storage.local.get(APP_BASE_KEY);
    const v = got[APP_BASE_KEY];
    if (typeof v === 'string' && v.startsWith('http')) return v;
  } catch { /* fall through */ }
  return appBaseFor('');
}
