/**
 * Two rules that had no guard, both of which this repo has just paid to
 * fix once. A rule that lives only in a doc decays — design.md §11 has
 * banned emoji-as-icons since it was written, and the audit still found
 * twelve of them in one file.
 *
 * They live together because they are the same shape of check: read the
 * source, grep for a banned pattern, name the file rather than count it.
 */
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC = join(__dirname, '..', '..');

function walk(dir: string, out: string[] = [], ext = /\.tsx?$/): string[] {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path, out, ext);
    else if (ext.test(name) && !/\.test\.tsx?$/.test(name)) out.push(path);
  }
  return out;
}

/**
 * Emoji and icon-substitute dingbats used as UI CHROME — inside a JSX
 * text node, which is where a control's own glyph sits. Emoji in DATA is
 * fine and common (a note, a message body), so this scans only text
 * between tags, never string literals.
 *
 * The dingbat list is explicit rather than a codepoint RANGE, and that
 * distinction was learned the expensive way: the first version banned
 * U+2190–U+21FF and U+2600–U+27BF wholesale and flagged eighteen sites,
 * six of which were the arrow in "Drivers -> Onboarding" and one the
 * keyboard hint "up/down to navigate, enter to open". Those glyphs are
 * TYPOGRAPHY — an arrow showing a transition, a symbol naming a key —
 * and no icon library has a substitute for them. What the rule is
 * actually about is a glyph standing in for an icon that lucide ships:
 * a tick, a cross, a warning triangle.
 */
const ICON_SUBSTITUTES = '\u2713\u2714\u2715\u2716\u2717\u2718'  // tick / cross
  + '\u2705\u274C\u26A0\u2728\u2B50\u21BB\u21A9';               // check / x / warn / sparkle / star / refresh
const CHROME_GLYPH = new RegExp(
  `>[^<>{}\n]*[\u{1F300}-\u{1FAFF}${ICON_SUBSTITUTES}][^<>{}\n]*<`,
  'u',
);

/**
 * Named debt, not an exemption. This file carried three icon-substitute
 * glyphs when the guard landed and was being edited by someone else at
 * the time, so converting it would have meant writing into a colleague's
 * open work. Deleting the entry is the fix; the guard still fails the
 * build the moment a file NOT on this list grows one.
 */
const NOT_YET_CONVERTED = ['features/maintenance/TaskDetailSheet.tsx'];

describe('UI chrome', () => {
  it('never uses emoji or dingbats where an icon belongs', () => {
    const offenders = walk(SRC, [], /\.tsx$/)
      .filter((f) => CHROME_GLYPH.test(readFileSync(f, 'utf8')))
      .map((f) => relative(SRC, f))
      .filter((f) => !NOT_YET_CONVERTED.includes(f));
    // design.md §11: "lucide-react only… no emoji as UI icons."
    expect(offenders).toEqual([]);
  });

  it('keeps the not-yet-converted list honest', () => {
    // An entry that no longer offends is dead weight that hides the next
    // real one — so the list must not outlive its reason.
    const stale = NOT_YET_CONVERTED.filter(
      (f) => !CHROME_GLYPH.test(readFileSync(join(SRC, f), 'utf8')),
    );
    expect(stale).toEqual([]);
  });

  it('routes per-user UI state through the preferences service', () => {
    // src/preferences/CLAUDE.md owns the exception table. These are the
    // documented non-preferences: session/auth, data caches with a TTL,
    // an operational timestamp, drafts with no logged-in user, and the
    // i18n library's own key. Everything else is a preference.
    const ALLOWED = [
      'preferences/',              // the service itself
      'api/client',                // session token
      'context/AuthContext',       // session
      'App.tsx',                   // logout clears the session token
      'features/ai/attachmentStore',
      'features/ai/thoughtStore',
      'hooks/usePoiLayers',        // map-tile cache with a TTL
      'features/carrier-directory/PublicCarrierIntake', // public draft
      'features/applications/public/',                  // public draft
      'i18n',
      // Named individually because each was checked against the test in
      // preferences/CLAUDE.md — "a preference has a default, a user
      // CHOOSES it, and losing it is an annoyance" — and each fails it.
      'features/alerts/sections/LiveAckPanel',  // "what's new since" timestamp
      'features/knowledge/KnowledgeBase',       // 30s view-ping debounce
      'lib/safeReturnTo',                       // explicit-signout, session flow
      'router.tsx',                             // chunk-reload loop breaker
    ];
    const offenders = walk(SRC)
      .filter((f) => !ALLOWED.some((a) => f.includes(a)))
      .filter((f) => /\b(localStorage|sessionStorage)\.(get|set|remove)Item\b/.test(
        readFileSync(f, 'utf8'),
      ))
      .map((f) => relative(SRC, f));
    expect(offenders).toEqual([]);
  });
});
