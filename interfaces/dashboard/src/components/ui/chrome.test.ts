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

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path, out);
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(path);
  }
  return out;
}

/**
 * The tree, walked and read ONCE at module load.
 *
 * The first version of this file gave every check its own `walk()` +
 * `readFileSync()`, which meant six passes over 469 files — measured at
 * 84 seconds on top of vitest's own 9, on a suite everyone runs. A guard
 * that makes the test loop slower gets deleted, whatever it catches.
 */
const FILES: { rel: string; src: string }[] = walk(SRC).map((f) => ({
  rel: relative(SRC, f),
  src: readFileSync(f, 'utf8'),
}));
const TSX = FILES.filter((f) => f.rel.endsWith('.tsx'));
const SRC_OF = new Map(FILES.map((f) => [f.rel, f.src]));
const srcOf = (rel: string) => SRC_OF.get(rel) ?? '';

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
/**
 * A JSX text node holding one of those glyphs.
 *
 * `{` and `}` used to be excluded from the surrounding class, which
 * quietly exempted every text node containing an interpolation — and
 * that is most of them. `<span>📅 Period ends {formatDay(x)}</span>`
 * was invisible to this guard while `<span>📅 Period ends</span>` was
 * caught. Braces are allowed through now; what still bounds the match is
 * the tag delimiters and the newline.
 */
const CHROME_GLYPH = new RegExp(
  `>[^<>\n]*[\u{1F300}-\u{1FAFF}${ICON_SUBSTITUTES}][^<>\n]*<`,
  'u',
);

/**
 * Empty, and staying that way. It held one file — TaskDetailSheet, with
 * three icon-substitute glyphs — parked there only because someone else
 * had it open at the time. The comment said "deleting the entry is the
 * fix"; the file came free, the glyphs became RefreshCw / FileText /
 * Check, and the staleness check is what noticed the entry had stopped
 * exempting anything.
 *
 * Left in place rather than removed: the guard reads it, and a named
 * empty list is where the next unavoidable exception goes.
 */
const NOT_YET_CONVERTED: string[] = [];

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


/**
 * Does this file put `title=` on a lowercase DOM element?
 *
 * A scanner, not a regex, and that is not a style preference. The regex
 * this replaced — `<(?!iframe)[a-z]\w*\b(?:[^>]|\{[^}]*\})*?\stitle=` —
 * measured **82 seconds** across 469 files on its own, while every other
 * check in this file finished in milliseconds. The alternation inside a
 * lazy quantifier lets the engine try exponentially many ways to match
 * the same text; on a 3,000-line component that is minutes, not
 * milliseconds. A guard that costs 82s of everyone's test loop does not
 * survive contact with a deadline.
 *
 * `<iframe title>` is skipped: there the attribute is the element's
 * required accessible NAME, not a tooltip, and banning it would trade a
 * style rule for an a11y regression.
 */
function hasNativeTitle(src: string): boolean {
  for (const m of src.matchAll(/\stitle=/g)) {
    const at = m.index ?? 0;
    const open = src.lastIndexOf('<', at);
    if (open === -1) continue;
    const tag = /^<([a-z][a-z0-9]*)[\s/>]/.exec(src.slice(open, open + 16));
    if (!tag || tag[1] === 'iframe') continue;
    // Still inside that opening tag? A `>` between the two ends it —
    // unless it is inside a JSX expression, where `=>` and comparisons
    // live. Depth-count braces rather than banning `>` outright.
    let depth = 0;
    let closed = false;
    for (let i = open; i < at; i += 1) {
      const c = src[i];
      if (c === '{') depth += 1;
      else if (c === '}') depth -= 1;
      else if (c === '>' && depth === 0) { closed = true; break; }
    }
    if (!closed) return true;
  }
  return false;
}


/**
 * The rendered height of a control, from its class list alone, at Size 1.
 * `null` means "not computable" — see the abstention note on the test.
 */
const SPACING = (n: string) => Number(n) * 4;
const LINE_HEIGHT: Record<string, number> = {
  xs: 16, sm: 20, base: 24, lg: 28, xl: 28,
};

export function tapHeight(cls: string): number | null {
  const t = cls.split(/\s+/).filter(Boolean);
  const find = (re: RegExp) => t.find((x) => re.test(x));
  // The floor itself. Not a height — a minimum — which is exactly what
  // is being asserted, so it answers the question directly.
  if (t.includes('min-h-tap') || t.includes('h-tap')) return 24;

  const explicit = find(/^h-[\d.]+$/) || find(/^size-[\d.]+$/);
  if (explicit) return SPACING(explicit.split('-')[1]);

  const py = find(/^py-[\d.]+$/);
  const pt = find(/^pt-[\d.]+$/);
  const pb = find(/^pb-[\d.]+$/);
  const pAll = find(/^p-[\d.]+$/);
  const padding = py ? SPACING(py.split('-')[1]) * 2
    : (pt || pb)
      ? SPACING((pt ?? 'pt-0').split('-')[1]) + SPACING((pb ?? 'pb-0').split('-')[1])
      : pAll ? SPACING(pAll.split('-')[1]) * 2 : 0;

  const type = find(/^text-(3xs|2xs|xs|sm|base|lg|xl)$/);
  if (!type) return null;
  const step = type.slice('text-'.length);
  // `text-2xs` / `text-3xs` are size-only steps by design (see the
  // fontSize note in tailwind.config.js) — no line-height of their own.
  const line = LINE_HEIGHT[step];
  if (line === undefined) return null;

  const border = t.some((x) => /^border(-[trblxy])?$/.test(x)) ? 2 : 0;
  return padding + line + border;
}

/**
 * The rendered WIDTH of a control, from its class list and its children.
 *
 * Width is only knowable for an ICON-ONLY control. A control with text
 * is as wide as its text, which no class list determines and no scanner
 * should guess — so this abstains on all of them, and did on 164 of the
 * 195 it saw.
 *
 * The floor was guarded on one axis for a long time, and 2.5.8 asks for
 * both: thirteen controls passed `min-h-tap` and rendered 14-22px WIDE,
 * two of them while carrying `min-h-tap` explicitly. A 14x24 target is
 * not a 24x24 target.
 *
 * VALIDATED, not assumed, the same way `tapHeight` was: all 31
 * computable controls were rendered in headless Chrome against the built
 * CSS, and the predicted width matched `getBoundingClientRect().width`
 * exactly, 31 of 31 — before the repair and after it.
 */
export function tapWidth(cls: string, children: string): number | null {
  const t = cls.split(/\s+/).filter(Boolean);
  const find = (re: RegExp) => t.find((x) => re.test(x));
  if (t.includes('min-w-tap') || t.includes('w-tap') || t.includes('size-tap')) return 24;

  const explicit = find(/^w-[\d.]+$/) || find(/^size-[\d.]+$/);
  if (explicit) return SPACING(explicit.split('-')[1]);

  // Exactly one self-closing element inside, and nothing else. Anything
  // with text, a second child or an expression is content-sized.
  const only = /^<([A-Z][\w.]*)\b([^>]*)\/>$/.exec(children.trim());
  if (!only) return null;
  const iconSize = /\bsize-([\d.]+)\b/.exec(
    /className="([^"]*)"/.exec(only[2])?.[1] ?? '',
  );
  if (!iconSize) return null;

  const px = find(/^px-[\d.]+$/);
  const pl = find(/^pl-[\d.]+$/);
  const pr = find(/^pr-[\d.]+$/);
  const pAll = find(/^p-[\d.]+$/);
  const padding = px ? SPACING(px.split('-')[1]) * 2
    : (pl || pr)
      ? SPACING((pl ?? 'pl-0').split('-')[1]) + SPACING((pr ?? 'pr-0').split('-')[1])
      : pAll ? SPACING(pAll.split('-')[1]) * 2 : 0;

  const border = t.some((x) => /^border(-[trblxy])?$/.test(x)) ? 2 : 0;
  return SPACING(iconSize[1]) + padding + border;
}


/* ── The exemption lists, each paired with the offence it exempts ──
 *
 * All eight live here, at module scope, next to the predicate that
 * decides whether an entry still has a reason to exist. That pairing is
 * the whole point. Five of these used to be declared inside their own
 * `it()`, out of the staleness check's reach — and they rotted there,
 * unseen, while the suite stayed green. An exemption nobody can audit
 * is not an exemption; it is a hole with a comment next to it.
 */
const ARBITRARY_LEN =
  /\b(?:p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|space-[xy]|w|h|min-w|min-h|max-w|max-h|size|top|left|right|bottom|inset|translate-[xy])-\[\d[\d.]*(?:px|rem)\]/;

const usesWebStorage = (src: string) =>
  /\b(localStorage|sessionStorage)\.(get|set|remove)Item\b/.test(src);

const hasOwnInset = (src: string) =>
  src.split('\n').some(
    (l) => /calc\(100d?vh\s*[-\u2212]/.test(l)
      && !/^\s*(\/\/|\*|\/\*|\{\/\*)/.test(l.trim()),
  );

/** Type SIZE a call site imposed on <Button>. Colour is not a size. */
const buttonSizeOverrides = (src: string): string[] => {
  const out: string[] = [];
  for (const m of src.matchAll(/<Button\b[^>]*className="([^"]*)"/g)) {
    const bad = m[1].match(
      /\b(?:h|size|px|py)-[\d.]+\b|\btext-(?:3xs|2xs|xs|sm|base|lg|xl)\b/g,
    );
    if (bad) out.push(bad.join(' '));
  }
  return out;
};

/** A <span> that has re-drawn a Badge: a status colour plus its geometry.
 *  Both doors count. `statusClasses` was invisible to this for as long as
 *  the list below has existed, which is why every entry in it read as
 *  dead — the detector could not see the door they were walking through. */
const statusDoorSites = (src: string): number[] => {
  const out: number[] = [];
  for (const m of src.matchAll(/<span\s+className=\{`([^`]*)`\}/g)) {
    const cls = m[1];
    if (!/\$\{(?:toneClasses|statusClasses)\(/.test(cls)) continue;
    const t = cls.replace(/\$\{[^}]*\}/g, ' ').split(/\s+/);
    const geometry = t.filter((c) =>
      /^(rounded|px-|py-|text-(3xs|2xs|xs)$|font-medium$)/.test(c),
    );
    if (geometry.length >= 3) out.push(src.slice(0, m.index ?? 0).split('\n').length);
  }
  return out;
};

/** A dialog width written the way tailwind-merge cannot honour. */
const dialogWidthSites = (src: string): string[] =>
  [...src.matchAll(/<DialogContent\b[^>]*className="([^"]*)"/g)]
    .filter((m) => /\bmax-w-/.test(m[1]))
    .map((m) => m[1]);

const STORAGE_ALLOWED = [
  'preferences/',              // the service itself
  'api/client',                // session token
  'App.tsx',                   // logout clears the session token
  'features/ai/attachmentStore',
  'features/ai/thoughtStore',
  'hooks/usePoiLayers',        // map-tile cache with a TTL
  'features/carrier-directory/PublicCarrierIntake', // public draft
  'features/applications/public/',                  // public draft
  // Named individually because each was checked against the test in
  // preferences/CLAUDE.md — "a preference has a default, a user
  // CHOOSES it, and losing it is an annoyance" — and each fails it.
  'features/alerts/sections/LiveAckPanel',  // "what's new since" timestamp
  'features/knowledge/KnowledgeBase',       // 30s view-ping debounce
  'lib/safeReturnTo',                       // explicit-signout, session flow
  'router.tsx',                             // chunk-reload loop breaker
];


const OWN_INSET = ['components/ui/dialog.tsx'];


const OVERRIDE_DEBT = ['components/datagrid/DataGrid.tsx'];


const STATUS_DOOR_DEBT = [
  'features/loads/Loads.tsx',
  'features/vehicles/inventory/InventoryCard.tsx',
  'features/vehicles/inventory/InventoryPage.tsx',
  'features/vehicles/inventory/ItemDialog.tsx',
  'features/parking/badges.tsx',
  'features/applications/Applications.tsx',
];


const SIZE_DEBT = [
  'features/kpi/config/IncentiveEditor.tsx',
  'features/kpi/dispatch/IncentiveRuns.tsx',
  'features/kpi/dispatch/runs/EditRowDialog.tsx',
  'features/kpi/dispatch/runs/ExceptionDialog.tsx',
  'features/kpi/dispatch/runs/NewRunDialog.tsx',
];


type DebtList = {
  name: string;
  entries: string[];
  /** How an entry names a file: its exact rel, or a substring of one. */
  match: 'exact' | 'substring';
  scope: { rel: string; src: string }[];
  offends: (f: { rel: string; src: string }) => boolean;
};

const DEBT: DebtList[] = [
  { name: 'NOT_YET_CONVERTED',           entries: NOT_YET_CONVERTED,           match: 'exact',     scope: TSX,   offends: (f) => CHROME_GLYPH.test(f.src) },
  { name: 'TITLE_NOT_YET_CONVERTED',     entries: TITLE_NOT_YET_CONVERTED,     match: 'exact',     scope: TSX,   offends: (f) => hasNativeTitle(f.src) },
  { name: 'ARBITRARY_NOT_YET_CONVERTED', entries: ARBITRARY_NOT_YET_CONVERTED, match: 'exact',     scope: FILES, offends: (f) => ARBITRARY_LEN.test(f.src) },
  { name: 'STORAGE_ALLOWED',             entries: STORAGE_ALLOWED,             match: 'substring', scope: FILES, offends: (f) => usesWebStorage(f.src) },
  { name: 'OWN_INSET',                   entries: OWN_INSET,                   match: 'exact',     scope: FILES, offends: (f) => hasOwnInset(f.src) },
  { name: 'OVERRIDE_DEBT',               entries: OVERRIDE_DEBT,               match: 'exact',     scope: TSX,   offends: (f) => buttonSizeOverrides(f.src).length > 0 },
  { name: 'STATUS_DOOR_DEBT',            entries: STATUS_DOOR_DEBT,            match: 'exact',     scope: TSX,   offends: (f) => statusDoorSites(f.src).length > 0 },
  { name: 'SIZE_DEBT',                   entries: SIZE_DEBT,                   match: 'exact',     scope: TSX,   offends: (f) => dialogWidthSites(f.src).length > 0 },
];

describe('UI chrome', () => {

  /**
   * Recharts takes `fontSize` as a number and writes it onto an SVG <text>
   * as an attribute, so no Tailwind class reaches it — 31 axis, tick and
   * legend sizes sat frozen at 10-12px while every heading beside them
   * grew with the Size setting. lib/chartText.ts holds the three replacements.
   * A number here is not a style choice; it is a value that opted out of
   * the engine, which is exactly what a guard is for.
   */
  it('has no chart font size that opted out of the text axis', () => {
    // `\\d` alone was too narrow twice over. It walked past a `fontSize: 9`
    // during the original sweep — the census regex read `1[0-2]` — and it
    // walked past `fontSize: '10px'` entirely, because a quote is not a
    // digit. Both forms reach the SVG the same way.
    // A fresh regex per file, deliberately. A /g regex carries lastIndex
    // between calls, so a shared one would start the NEXT file wherever
    // it stopped in the last — quietly skipping matches near the top.
    const frozen = (src: string) =>
      src.match(/fontSize:\s*(?:\d|['"`]\s*\d)/g) ?? [];
    const offenders = FILES
      .map(({ rel, src }) => ({ rel, hits: frozen(src) }))
      .filter(({ hits }) => hits.length)
      .map(({ rel, hits }) => `${rel}: ${hits.join(', ')}`);
    expect(
      offenders,
      'use CHART_FONT_XS / _SM / _MD from lib/chartText.ts — a bare number ' +
        'becomes an SVG attribute the Size engine cannot reach',
    ).toEqual([]);
  });
  it('never uses emoji or dingbats where an icon belongs', () => {
    const offenders = TSX
      .filter((f) => CHROME_GLYPH.test(f.src))
      .map((f) => f.rel)
      .filter((f) => !NOT_YET_CONVERTED.includes(f));
    // design.md §11: "lucide-react only… no emoji as UI icons."
    expect(offenders).toEqual([]);
  });

  it('keeps every exemption list honest', () => {
    // An entry that no longer offends is dead weight that hides the next
    // real one — so no list may outlive its reason. This used to check
    // three of the eight, because the other five were declared inside
    // their own `it()` where it could not see them. STATUS_DOOR_DEBT had
    // gone entirely dead in that blind spot: every one of its six entries
    // read as stale, not because the debt was paid but because the
    // detector was looking through one door and the code was walking
    // through the other.
    const stale: string[] = [];
    for (const { name, entries, match, scope, offends } of DEBT) {
      for (const e of entries) {
        const covered = match === 'exact'
          ? scope.filter((f) => f.rel === e)
          : scope.filter((f) => f.rel.includes(e));
        if (!covered.some(offends)) {
          stale.push(`${name}: ${e}${covered.length ? '' : ' (file is gone)'}`);
        }
      }
    }
    expect(
      stale,
      'these exemptions no longer exempt anything — delete the entry, or ' +
        'the detector beside it has stopped seeing what the file does',
    ).toEqual([]);
  });

  it('routes per-user UI state through the preferences service', () => {
    // src/preferences/CLAUDE.md owns the exception table. These are the
    // documented non-preferences: session/auth, data caches with a TTL,
    // an operational timestamp, drafts with no logged-in user, and the
    // i18n library's own key. Everything else is a preference.
    const offenders = FILES
      .filter((f) => !STORAGE_ALLOWED.some((a) => f.rel.includes(a)))
      .filter((f) => /\b(localStorage|sessionStorage)\.(get|set|remove)Item\b/.test(f.src))
      .map((f) => f.rel);
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
    const offenders = FILES
      .filter(({ src }) => {
        // A line that only MENTIONS the class in prose is not a use.
        return src.split('\n').some(
          (l) => PALETTE.test(l) && !/^\s*(\/\/|\*|\/\*)/.test(l),
        );
      })
      .map((f) => f.rel);
    expect(offenders).toEqual([]);
  });

  it('never uses a native title= tooltip on a DOM element', () => {
    // Unthemed, delayed, and invisible on touch. `<Tip>` replaces it;
    // icon-only controls keep an aria-label. Component PROPS named
    // `title` (PageHeader, EmptyState, Dialog) are a different thing.
    const offenders = TSX
      .filter((f) => hasNativeTitle(f.src))
      .map((f) => f.rel)
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
    const offenders = FILES
      .filter((f) => ARBITRARY_LEN.test(f.src))
      .map((f) => f.rel)
      .filter((f) => !ARBITRARY_NOT_YET_CONVERTED.includes(f));
    expect(offenders).toEqual([]);
  });

  it('never extends Tailwind\'s shared `spacing` key', () => {
    // The quietest possible way to kill the Size engine. `spacing` is the
    // default every dimension key derives from, so extending it collapses
    // padding, margin, gap, width, height and size onto ONE multiplier —
    // and nothing fails. No error, no visual break; the four axes simply
    // stop being four, and the per-region sliders start doing something
    // else. You would find out in an audit. design.md §5.1.
    const cfg = readFileSync(join(SRC, '..', 'tailwind.config.js'), 'utf8');
    const inExtend = cfg.slice(cfg.indexOf('extend:'));
    expect(/^\s{6}spacing:/m.test(inExtend)).toBe(false);
  });

  it('never subtracts the shell frame in a viewport calc', () => {
    // `calc(100vh - 14rem)` encodes today's header + padding, so it is
    // wrong the moment Size moves any of it — and two of the three that
    // existed were already wrong at 1x. `h-full`, or `flex-1 min-h-0`
    // inside a flex column. design.md §5.1.
    // `ui/dialog.tsx` caps itself at the viewport minus its OWN 1rem
    // inset on each side. That is not the shell frame — nothing above it
    // moves — and without the cap a tall dialog overflows both edges and
    // its footer buttons become unreachable. One entry, named, so the
    // exception cannot spread silently.
    const offenders = FILES
      .filter((f) => !OWN_INSET.includes(f.rel))
      .filter((f) => f.src.split('\n').some(
        (l) => /calc\(100d?vh\s*[-−]/.test(l)
          && !/^\s*(\/\/|\*|\/\*|\{\/\*)/.test(l.trim()),
      ))
      .map((f) => f.rel);
    expect(offenders).toEqual([]);
  });

  it('never hand-rolls a control the Button primitive already ships', () => {
    // A raw <button> wearing a variant's own dimensions IS that variant,
    // typed out. `size="sm"` is not the violation — it is the vocabulary,
    // and 74 sites use it correctly. Spelling out `h-7 px-2.5 text-xs`
    // instead is how the value escapes button.tsx and stops following it.
    const VARIANTS: [string, RegExp][] = [
      ['xs', /\bh-6\b(?=[^"]*\bpx-2\b)(?=[^"]*\btext-xs\b)/],
      ['sm', /\bh-7\b(?=[^"]*\bpx-2\.5\b)(?=[^"]*\btext-xs\b)/],
      ['lg', /\bh-9\b(?=[^"]*\bpx-2\.5\b)/],
    ];
    const offenders: string[] = [];
    for (const { rel, src } of TSX) {
      for (const m of src.matchAll(/<button\b[^>]*className="([^"]*)"/g)) {
        const hit = VARIANTS.find(([, re]) => re.test(m[1]));
        if (hit) offenders.push(`${rel} → <Button size="${hit[0]}">`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('never overrides a Button variant\'s own dimensions', () => {
    // `<Button className="h-9">` moves the height back to the call site,
    // where it stops following button.tsx. Pick the variant that is that
    // height, or add the variant. Measured at 0 when this guard landed —
    // nobody was fighting the primitive, and this keeps it that way.
    // One entry, and it belongs to another developer's open file:
    // DataGrid strips a button's horizontal padding to sit flush in a
    // header cell. Whether that wants an `unpadded` variant or a `px-0`
    // escape is their call to make, not mine to make inside their diff.
    // DIMENSIONS only. The first version of the detector matched
    // `text-[\w.]+` and flagged `text-muted-foreground` — a colour, which
    // a call site is entitled to set. Type SIZE is a dimension; type
    // COLOUR is not.
    const offenders = TSX
      .filter((f) => !OVERRIDE_DEBT.includes(f.rel))
      .flatMap((f) => buttonSizeOverrides(f.src).map((b) => `${f.rel} → ${b}`));
    expect(offenders).toEqual([]);
  });

  it('never ships a pointer target under the 24px floor', () => {
    // The floor 193 controls were repaired to reach, guarded so a 194th
    // cannot land quietly. WCAG 2.5.8 AA; design.md §5.1.
    //
    // VALIDATED, not assumed. `tapHeight` was run against 728 elements
    // measured in headless Chrome against the built CSS: on the 428 it
    // can compute, its VERDICT (>=24 or not) matched the browser 428/428
    // — no false alarms, nothing missed.
    //
    // It ABSTAINS on 116 more, and that limitation is deliberate. A
    // control with no type class inherits its line-height from a parent
    // this scanner cannot see, so the class list genuinely does not
    // determine the height. All 116 measure >= 24 today; demanding a
    // token from them would have meant 116 false alarms, and a guard
    // that cries wolf is a guard someone deletes. The hole is real — a
    // NEW control that inherits its line-height and carries too little
    // padding slips through — and the Chrome harness is how it gets
    // closed, periodically, not by guessing here.
    const offenders: string[] = [];
    for (const { rel, src } of TSX) {
      const lineAt = (i: number) => src.slice(0, i).split('\n').length;
      for (const m of src.matchAll(/<(?:button|a|summary)\b[^>]*className="([^"]*)"/g)) {
        const h = tapHeight(m[1]);
        if (h !== null && h < 24) {
          offenders.push(`${rel}:${lineAt(m.index ?? 0)} is ${h}px tall — add min-h-tap`);
        }
      }
      // The other axis. 2.5.8 asks for 24x24, and this guard asked for
      // 24-by-anything until thirteen icon-only controls turned up
      // between 14 and 22px wide — two of them already carrying
      // `min-h-tap`, which is what made them look repaired.
      for (const m of src.matchAll(
        /<(button|a|summary)\b[^>]*?className="([^"]*)"[^>]*?>([\s\S]{0,300}?)<\/\1>/g,
      )) {
        const w = tapWidth(m[2], m[3]);
        if (w !== null && w < 24) {
          offenders.push(`${rel}:${lineAt(m.index ?? 0)} is ${w}px wide — add min-w-tap`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it('never restates what a primitive already ships', () => {
    // Different from the override guard above: this is not a call site
    // FIGHTING the primitive, it is a call site repeating it. Eleven
    // `<DialogFooter className="gap-2">` when `gap-2` is already in the
    // footer's base, and `<Button size="icon" className="h-8 w-8">` when
    // `size="icon"` IS `size-8`. Harmless today, and that is the danger:
    // the day the primitive's value changes, every restatement silently
    // keeps the old one.
    const BASE: Record<string, string[]> = {
      DialogFooter: ['gap-2', 'flex', 'flex-col-reverse', 'border-t', 'p-4',
        'sm:flex-row', 'sm:justify-end', 'rounded-b-xl'],
      DialogHeader: ['flex', 'flex-col', 'gap-2'],
    };
    const offenders: string[] = [];
    for (const { rel, src } of TSX) {
      for (const [name, base] of Object.entries(BASE)) {
        const re = new RegExp(`<${name}\\b[^>]*className="([^"]*)"`, 'g');
        for (const m of src.matchAll(re)) {
          const echoed = m[1].split(/\s+/).filter((c) => c && base.includes(c));
          if (echoed.length) offenders.push(`${rel} → <${name}> restates ${echoed.join(' ')}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it('never hand-rolls a Badge that the tone layer already describes', () => {
    // design.md's soft-pill recipe IS the Badge primitive's base class:
    // `rounded-md px-2 py-0.5 text-xs font-medium` plus a tone. A <span>
    // typing that out beside `toneClasses(...)` is <Badge tone="…">
    // spelled long-hand — 52 of them were, because the primitive's
    // variants are brand colours and had no door for a tone. It has one
    // now; this keeps the door the only way in.
    // `statusClasses` is a DIFFERENT door: it takes a domain status, not
    // a tone, and `<StatusBadge>` — which owns it — renders the label
    // itself, de-snake-casing as it goes. Several call sites format the
    // label their own way (`v.replace('_','-')`), so swapping them is a
    // per-site judgement about wording, not a mechanical rename. Ten of
    // those are named below rather than silently swept in here.
    const offenders: string[] = [];
    for (const { rel, src } of TSX) {
      if (rel === 'components/StatusBadge.tsx' || rel.startsWith('components/ui/')) continue;
      // Another developer's open area — theirs to convert inside their
      // own diff, not mine to rewrite from outside it.
      if (rel.startsWith('features/kpi/') || rel.startsWith('components/callouts/')) continue;
      if (STATUS_DOOR_DEBT.includes(rel)) continue;
      for (const line of statusDoorSites(src)) {
        offenders.push(`${rel}:${line} → <Badge tone={…}>`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('never sets a dialog width with a class that cannot take', () => {
    // `tailwind-merge` cannot replace a `sm:`-prefixed class with an
    // unprefixed one — they are different variants, so both survive and
    // the prefixed one wins from 640px up. DialogContent's base carried
    // `sm:max-w-sm`, so `className="max-w-lg"` — the obvious spelling,
    // and what 31 of 43 call sites used — rendered at 384px on every
    // desktop and had done for a long time. Nobody noticed until a
    // dialog whose content needed 460px started eating its own labels.
    // `size="lg"` cannot be written the not-taking way.
    const offenders: string[] = [];
    for (const { rel, src } of TSX) {
      if (SIZE_DEBT.includes(rel)) continue;
      if (dialogWidthSites(src).length) {
        offenders.push(`${rel} → use size="…" instead`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
