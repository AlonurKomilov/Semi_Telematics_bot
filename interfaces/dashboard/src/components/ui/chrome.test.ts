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
  + '\u2705\u274C\u26A0\u2728\u2B50\u21BB\u21A9'                 // check / x / warn / sparkle / star / refresh
  + '\u270E\u270F\u2712\u2709\u260E\u2699';                       // pencil / pen / envelope / phone / gear
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

/**
 * The `title=` -> `<Tip>` migration, which predates this guard by a long
 * way — CLAUDE.md describes it as live and unfinished, and it is why the
 * ESLint rule that flags it cannot be raised above a warning. Eleven
 * sites were converted when this guard landed; these are what is left.
 * The list exists so the guard can be ON while the migration finishes:
 * nothing NEW can appear, and every entry removed is progress the build
 * can see. The staleness test below deletes the list's right to linger.
 */
/**
 * Arbitrary lengths with NO exact step on the 4px scale. Two of the
 * thirty had one (`min-w-[6rem]` -> `min-w-24`, `max-w-[10rem]` ->
 * `max-w-40`) and were converted; the rest — `w-[220px]`,
 * `min-h-[400px]`, `max-h-[32rem]` and friends — sit between steps, so
 * clearing them means choosing a nearby step and accepting the pixels
 * that move. That is a design decision per site, not a rename, which is
 * exactly why it is a list and not a sweep.
 */
const ARBITRARY_NOT_YET_CONVERTED: string[] = [
  'components/datagrid/DataGrid.tsx',
  'components/datagrid/pivot/PivotPanel.tsx',
  'components/ui/sheet.tsx',
  'features/alerts/NotificationsPanel.tsx',
  'features/applications/Applications.tsx',
  'features/applications/ApplyPreview.tsx',
  'features/applications/public/steps.tsx',
  'features/costs/CostPerMile.tsx',
  'features/drivers/Drivers.tsx',
  'features/geofences/Geofences.tsx',
  'features/maintenance/CalendarMonth.tsx',
  'features/maintenance/badges.tsx',
  'features/object-storage/ObjectStorageFileTable.tsx',
  'features/overview/sections/OverviewStatusChart.tsx',
  'features/routes/Routes.tsx',
  'features/scorecards/ScorecardRules.tsx',
  'features/settings/Invites.tsx',
  'features/settings/TeamManagement.tsx',
  'features/work-orders/WorkOrderForm.tsx',
  'pages/Profile.tsx',
  'shells/AppShell.tsx',
];

const TITLE_NOT_YET_CONVERTED: string[] = [
  'components/datagrid/ColumnFilterMenu.tsx',
  'components/datagrid/ManageColumnsMenu.tsx',
  'components/datagrid/pivot/PivotView.tsx',
  'components/shell/LastUpdated.tsx',
  'features/applications/Applications.tsx',
  'features/applications/ApplyPreview.tsx',
  'features/drivers/Drivers.tsx',
  'features/inspections/InspectionDetail.tsx',
  'features/inspections/MediaGallery.tsx',
  'features/inspections/TemplateEditor.tsx',
  'features/integrations/ConflictsPanel.tsx',
  'features/integrations/FeedsTable.tsx',
  'features/integrations/IntegrationCard.tsx',
  'features/knowledge/KnowledgeBase.tsx',
  'features/loads/Loads.tsx',
  'features/maintenance/AddTaskDialog.tsx',
  'features/maintenance/CalendarMonth.tsx',
  'features/maintenance/TaskDetailSheet.tsx',
  'features/maintenance/Tasks.tsx',
  'features/maintenance/badges.tsx',
  'features/maintenance/columns.tsx',
  'features/maintenance/pickers.tsx',
  'features/object-storage/ObjectStorageBackendCard.tsx',
  'features/object-storage/ObjectStorageFileTable.tsx',
  'features/reports/ScheduledReports.tsx',
  'features/safety-events/EventVideoModal.tsx',
  'features/scorecards/DriverInsights.tsx',
  'features/scorecards/ScorecardRules.tsx',
  'features/scorecards/Scorecards.tsx',
  'features/settings/Companies.tsx',
  'features/settings/Invites.tsx',
  'features/settings/TeamManagement.tsx',
  'features/settings/WorkHours.tsx',
  'pages/Profile.tsx',
];

describe('UI chrome', () => {
  it('never uses emoji or dingbats where an icon belongs', () => {
    const offenders = walk(SRC, [], /\.tsx$/)
      .filter((f) => CHROME_GLYPH.test(readFileSync(f, 'utf8')))
      .map((f) => relative(SRC, f))
      .filter((f) => !NOT_YET_CONVERTED.includes(f));
    // design.md §11: "lucide-react only… no emoji as UI icons."
    expect(offenders).toEqual([]);
  });

  it('keeps the not-yet-converted lists honest', () => {
    // An entry that no longer offends is dead weight that hides the next
    // real one — so neither list may outlive its reason.
    const stale = [
      ...NOT_YET_CONVERTED.filter(
        (f) => !CHROME_GLYPH.test(readFileSync(join(SRC, f), 'utf8')),
      ),
      ...TITLE_NOT_YET_CONVERTED.filter(
        (f) => !/<(?!iframe\b)[a-z][a-z0-9]*\b(?:[^>]|\{[^}]*\})*?\stitle=/
          .test(readFileSync(join(SRC, f), 'utf8')),
      ),
      ...ARBITRARY_NOT_YET_CONVERTED.filter(
        (f) => !/\b(?:p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|space-[xy]|w|h|min-w|min-h|max-w|max-h|size|top|left|right|bottom|inset|translate-[xy])-\[\d[\d.]*(?:px|rem)\]/
          .test(readFileSync(join(SRC, f), 'utf8')),
      ),
    ];
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

  it('never picks a raw palette colour to carry meaning', () => {
    // design.md §11: colour that MEANS something comes from the tone
    // layer (`toneClasses`/`toneText`) or, for a set whose members are
    // merely different, the categorical ramp (`text-chart-1..5`). The
    // ramp existed as `--chart-*` tokens for a long time and was not
    // exposed as classes, so 25 call sites reached for the palette
    // because it was the only class-shaped option.
    const PALETTE =
      /\b(?:text|bg|border|from|to|via|ring|divide)-(?:red|green|amber|yellow|blue|orange|emerald|rose|slate|gray|zinc|indigo|violet|purple|teal|cyan|lime|fuchsia|pink|sky|stone|neutral)-\d{2,3}\b/;
    const offenders = walk(SRC)
      .filter((f) => {
        const src = readFileSync(f, 'utf8');
        // A line that only MENTIONS the class in prose is not a use.
        return src.split('\n').some(
          (l) => PALETTE.test(l) && !/^\s*(\/\/|\*|\/\*)/.test(l),
        );
      })
      .map((f) => relative(SRC, f));
    expect(offenders).toEqual([]);
  });

  it('never uses a native title= tooltip on a DOM element', () => {
    // Unthemed, delayed, and invisible on touch. `<Tip>` replaces it;
    // icon-only controls keep an aria-label. Component PROPS named
    // `title` (PageHeader, EmptyState, Dialog) are a different thing.
    // NOT <iframe title>, where the attribute is the element's required
    // accessible NAME, not a tooltip — banning it there would trade a
    // style rule for an a11y regression.
    const NATIVE_TITLE =
      /<(?!iframe\b)[a-z][a-z0-9]*\b(?:[^>]|\{[^}]*\})*?\stitle=/;
    const offenders = walk(SRC, [], /\.tsx$/)
      .filter((f) => NATIVE_TITLE.test(readFileSync(f, 'utf8')))
      .map((f) => relative(SRC, f))
      .filter((f) => !TITLE_NOT_YET_CONVERTED.includes(f));
    expect(offenders).toEqual([]);
  });

  it('never writes an arbitrary px/rem length for layout', () => {
    // design.md §5.1: an arbitrary length is the one thing the Size
    // multipliers cannot reach — a promise that the element will never
    // follow the user's setting. Viewport units (`max-h-[60vh]`) and
    // token references (`w-[var(--assistant-w)]`) are deliberately NOT
    // matched: those are relative by design and a multiplier would be
    // wrong on them.
    const ARBITRARY =
      /\b(?:p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|space-[xy]|w|h|min-w|min-h|max-w|max-h|size|top|left|right|bottom|inset|translate-[xy])-\[\d[\d.]*(?:px|rem)\]/;
    const offenders = walk(SRC)
      .filter((f) => ARBITRARY.test(readFileSync(f, 'utf8')))
      .map((f) => relative(SRC, f))
      .filter((f) => !ARBITRARY_NOT_YET_CONVERTED.includes(f));
    expect(offenders).toEqual([]);
  });
});
