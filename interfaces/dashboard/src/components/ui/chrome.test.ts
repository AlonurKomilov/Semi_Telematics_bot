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
 * One entry, and it is not a UI length at all.
 *
 * `ApplyPreview` renders the public application form inside a real
 * iframe at `w-[390px] h-[780px]` — a phone viewport, so the form under
 * test lays itself out the way a phone would. Scaling that with the
 * reader's Size setting would preview a phone that does not exist.
 *
 * The other 31 that used to be here were converted rather than excused.
 * They were not off-ladder by choice: the ladder stopped at 384px while
 * the app has a 680px form column and a 512px popover, and there was no
 * `w-55` to write for 220px. `EXTRA_STEPS` in tailwind.config.js names
 * the fourteen steps that were missing, and every one of those sites now
 * follows the user's setting instead of promising never to.
 */
const ARBITRARY_NOT_YET_CONVERTED: string[] = [
  'features/applications/ApplyPreview.tsx',
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

  const type = find(/^text-(2xs|xs|sm|base|lg|xl)$/);
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
 * All eleven live here, at module scope, next to the predicate that
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
      /\b(?:h|size|px|py)-[\d.]+\b|\btext-(?:2xs|xs|sm|base|lg|xl)\b/g,
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
    // Whitespace after `${` — a call site that wraps its line was
    // invisible to this, which is where four of them were hiding.
    if (!/\$\{\s*(?:toneClasses|statusClasses)\s*\(/.test(cls)) continue;
    const t = cls.replace(/\$\{[^}]*\}/g, ' ').split(/\s+/);
    const geometry = t.filter((c) =>
      /^(rounded|px-|py-|text-(2xs|xs)$|font-medium$)/.test(c),
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


/**
 * A hand-rolled card: the bordered surface `components/ui/card.tsx`
 * exists to be. 223 copies of it are why the app had two radii and five
 * paddings before the primitive landed.
 *
 * The rule is narrow on purpose, because a first pass matching
 * "bg-card + border + rounded" found 50 sites and most were not cards at
 * all. Three exclusions do the work:
 *   · `rounded-md` is the CHIP/ROW radius (8px), not the card's 10px
 *   · `bg-card/NN` is translucent floating chrome — a Leaflet control,
 *     the 3D info panel — sitting over content, not holding it
 *   · `absolute` / `fixed` / `sticky` is a thing that floats, not a
 *     surface the page sits on
 * That takes 50 down to 7, and those 7 are named below.
 */
const cardShellSites = (src: string): number[] => {
  const out: number[] = [];
  for (const m of src.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)) {
    const c = m[1] ?? m[2] ?? '';
    if (!/\bbg-card(?![/\w-])/.test(c)) continue;
    if (!/\bborder\b/.test(c)) continue;
    if (!/\brounded-(?:lg|xl)\b/.test(c)) continue;
    if (/\b(?:absolute|fixed|sticky)\b/.test(c)) continue;
    out.push(src.slice(0, m.index ?? 0).split('\n').length);
  }
  return out;
};

/**
 * Surfaces that wear a card's clothes and are not cards. Every one was
 * read before it was listed — the batch this guard was written for
 * turned out to be one card and seven other things, which is the whole
 * reason the rule above is as narrow as it is.
 *
 *   · Chat 1834/1973 — a chip column and the message composer's input
 *     frame. Form-control chrome; a Card would give the composer a
 *     card's padding and a card's meaning.
 *   · Applications 1090/1092, TeamManagement 2233 — inline "Loading…"
 *     and "nothing here" rows inside a list. Not <EmptyState> either:
 *     that is a dashed `p-10` box for a whole page. These three are a
 *     THIRD empty treatment nobody has named, and naming it is a design
 *     decision, not a padding one.
 *   · DatatruckSyncPanel 335, Permissions 315 — a status notice row and
 *     a filter strip. Asymmetric padding is the tell: a card breathes
 *     evenly, a row is wider than it is tall.
 */
const CARD_NOT_A_CARD = [
  'features/ai/Chat.tsx',
  'features/applications/Applications.tsx',
  'features/integrations/DatatruckSyncPanel.tsx',
  'features/permissions/Permissions.tsx',
  'features/settings/TeamManagement.tsx',
];

/**
 * A literal px/rem length inside an inline `style` object. A class can be
 * multiplied by the Size axes; a number in a style object cannot, and it
 * is the one spelling the arbitrary-length guard above never sees.
 *
 * Narrow on purpose, the same way the card rule is. Percentages, `vh`,
 * `var()`, `calc()` and template-interpolated values are relative or
 * computed and stay alone; `0` has no scale to lose; `lineHeight` is
 * unitless by design. A first draft that skipped those exclusions
 * reported 127 sites, of which 117 were recharts `margin` props in SVG
 * units, a `topPerformers` key matched by a `top` prefix, and Leaflet
 * `divIcon` HTML. The real number was ten.
 *
 * `lib/scaledLength.ts` is the replacement, and it mirrors the config's
 * axis-by-magnitude rule so `220px` there behaves like `h-55` here.
 */
const inlineLengthSites = (src: string): { line: number; prop: string }[] => {
  const PROPS = new Set([
    'width', 'height', 'fontSize', 'padding', 'margin', 'top', 'left',
    'right', 'bottom', 'gap', 'minWidth', 'maxWidth', 'minHeight',
    'maxHeight', 'borderRadius', 'borderWidth', 'inset', 'flexBasis',
  ]);
  const out: { line: number; prop: string }[] = [];
  // `[Ss]tyle`: recharts passes its own boxes as `contentStyle` /
  // `labelStyle` / `itemStyle`, and twenty of those sat in the blind
  // spot of a regex that only knew the lowercase prop.
  for (const style of src.matchAll(/[A-Za-z]*[Ss]tyle=\{\{([\s\S]{0,400}?)\}\}/g)) {
    for (const m of style[1].matchAll(
      /(?:^|[,{\s])([A-Za-z]+)\s*:\s*(`[^`]*`|'[^']*'|"[^"]*"|-?[0-9][0-9.]*)/g,
    )) {
      if (!PROPS.has(m[1])) continue;
      const v = m[2].replace(/^['"`]|['"`]$/g, '');
      if (/%|\$\{|vh|vw|var\(|calc\(/.test(v)) continue;
      if (!/^-?\d[\d.]*(?:px|rem)?$/.test(v)) continue;
      if (v === '0' || v === '0px') continue;
      out.push({ line: src.slice(0, style.index ?? 0).split('\n').length, prop: m[1] });
    }
  }
  return out;
};

/**
 * The three that must NOT be converted:
 *   · PublicApply — `left: -9999px; width: 1; height: 1` is the
 *     off-screen trick that keeps an input reachable to a screen reader
 *     and invisible to everyone else. Scaling a hiding place is absurd.
 *   · PoiLayerPanel, MapTypeControl — Leaflet control chrome. Settled by
 *     the owner (2026-08-26) and written down in design.md §8: map-canvas
 *     lengths ride the MAP's scale, not the interface setting. A marker is
 *     measured against tiles that have their own zoom, and growing it with
 *     the Size control would put two scales in one viewport. Chart TEXT is
 *     not covered — that is read like any other text.
 */
const INLINE_LENGTH_ALLOWED: { file: string; props: string[] }[] = [
  // Exempt by PROPERTY, not by file. PublicApply was excused for the
  // off-screen trick — `left: -9999px; width: 1; height: 1` — and a
  // whole-file pass handed it `borderRadius` too, on the one surface
  // where a corner most needs to come from the token.
  { file: 'features/applications/public/PublicApply.tsx', props: ['left', 'width', 'height'] },
  { file: 'features/live-map/PoiLayerPanel.tsx', props: ['minWidth', 'maxWidth'] },
  { file: 'features/live-map/MapTypeControl.tsx', props: ['minWidth'] },
];
const inlineLengthAllows = (rel: string, prop: string) =>
  INLINE_LENGTH_ALLOWED.some((e) => e.file === rel && e.props.includes(prop));

/** Source with comments removed — a rule that explains a past bug must
 *  not read as the bug. */
const codeOnly = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:"'`\\])\/\/[^\n]*/g, '$1');

/**
 * A corner that ignores the Corners setting.
 *
 * `rounded-[10px]` is the radius twin of an arbitrary length, and
 * `rounded-4xl` is worse than arbitrary: it is a step the scale never
 * defined, so it compiles to NOTHING and the corner falls back to square
 * with no warning. That is exactly how it reached StatusBadge and stayed.
 *
 * Comments are stripped first. StatusBadge's docblock still explains the
 * old `rounded-4xl` bug, and a guard that cannot tell an explanation from
 * an offence teaches people to delete the explanation.
 */
const radiusClassSites = (src: string): string[] => {
  const code = src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:"'`\\])\/\/[^\n]*/g, '$1');
  return [...code.matchAll(/\brounded(?:-[trblse]{1,2})?-\[[^\]]+\]|\brounded-4xl\b/g)]
    .map((m) => m[0]);
};

/**
 * design.md §3: a CAPSULE states a fact you read; a bordered rounded
 * rectangle invites a click. So a control may not wear `rounded-full`
 * unless it is circular by geometry — an icon button, an avatar — which
 * is what the size/dimension classes tell us.
 *
 * Two subsystems had opposite grammars for the same shape (alerts wrote
 * theirs down, Scorecards inverted it inside one file). Settled by the
 * owner 2026-08-26 in favour of the twelve-to-two majority; this keeps it
 * settled. It matters most at Sharp, where a rounded rect collapses to
 * 2px and a capsule stays a capsule.
 */
const capsuleControlSites = (src: string): number[] => {
  const out: number[] = [];
  for (const m of src.matchAll(
    /<(button|a)\b([\s\S]{0,400}?)>([\s\S]{0,160}?)<\/\1>/g,
  )) {
    const [, , attrs, inner] = m;
    if (!/rounded-full/.test(attrs)) continue;
    // circular BY GEOMETRY — an icon button in a square box — is fine
    if (/\b(?:size|w|h)-[\d.]+\b/.test(attrs)) continue;
    if (!/\bpx-[\d.]/.test(attrs)) continue;
    const text = inner.replace(/<[^>]*>/g, '').trim();
    if (!/[A-Za-z]{2}/.test(text) && !/\{[a-zA-Z_.]+\}/.test(inner)) continue;
    out.push(src.slice(0, m.index ?? 0).split('\n').length);
  }
  return out;
};

/**
 * The one sanctioned arbitrary corner. `tooltip.tsx` rotates a small
 * square to make the arrow, and at Pill `rounded-sm` is 12px on a 10px
 * square — the arrow degenerates into a dot. The comment at the call site
 * carries the arithmetic, which is what an exception should look like.
 */
const RADIUS_ARBITRARY_ALLOWED = ['components/ui/tooltip.tsx'];

/**
 * The rung a `rounded-*` class sits on. These are OFFSETS from the token
 * (`xl` is `--radius + 4`, `sm` is `--radius − 4`), so the ORDER is fixed
 * — an `xl` is rounder than an `lg` at every preset, including Sharp
 * where the token is 0. That is what makes the rule below checkable from
 * source: it needs no measurement, because the scale decides it.
 */
const RADIUS_RUNG: Record<string, number> = {
  'rounded-sm': -4, rounded: -3, 'rounded-md': -2, 'rounded-lg': 0,
  'rounded-xl': 4, 'rounded-2xl': 8, 'rounded-3xl': 16,
};
const rungOf = (cls: string): string | null => {
  // The SIDE prefixes matter and were missed the first time: a header
  // docked on three edges rounds only its top two, so the adjacency
  // defects live in `rounded-t-*` almost by definition. Without this the
  // rule was blind to `rounded-t-xl` — the one shape it was written for.
  // Take the ROUNDEST step named, since that is the corner that can
  // overshoot its parent.
  const found = [...cls.matchAll(
    /\brounded(?:-(?:t|b|l|r|tl|tr|bl|br|s|e|ss|se|es|ee))?-(sm|md|lg|xl|2xl|3xl)\b/g,
  )].map((m) => `rounded-${m[1]}`);
  if (found.length) {
    return found.reduce((a, b) => (RADIUS_RUNG[b] > RADIUS_RUNG[a] ? b : a));
  }
  return /\brounded\b(?![-\w])/.test(cls) ? 'rounded' : null;
};

/**
 * A child rounder than the parent it sits against.
 *
 * Concentric corners want `inner = outer − gap`; inner LARGER than outer
 * has no gap that makes it right and always reads broken — a crescent of
 * the parent showing inside its own corner. RunBoard shipped one:
 * `rounded-t-xl` inside a `rounded-lg` Card, a constant +4px at every
 * preset, and at Sharp a 4px-rounded band floating inside a dead-square
 * card.
 *
 * ADJACENCY IS THE WHOLE QUESTION, and this approximates it by source
 * proximity: a child whose className is within 8 lines of its parent's
 * and indented deeper. Loosen that window and the rule dissolves — at 20
 * lines it reports 9, at 60 it reports 22, and at no limit 41, of which
 * the furthest pair is 325 lines apart with dozens of elements between
 * and arcs that never meet. Measured: at 8 lines, zero. At 6, it would
 * still have caught RunBoard.
 *
 * The complete answer is a DOM walk — for every element inset ≤2px in a
 * rounded parent, assert child ≤ parent — and it needs a browser, which
 * this file does not have. This catches the shape that actually shipped.
 */
const nestedRadiusSites = (src: string): string[] => {
  const NEAR = 8;
  const marks: { line: number; indent: number; step: string }[] = [];
  src.split('\n').forEach((l, i) => {
    const m = /className=(?:"([^"]*)"|\{`([^`]*)`\})/.exec(l);
    let step = m ? rungOf(m[1] ?? m[2] ?? '') : null;
    // A primitive carries its radius in the component, not the class
    // string. Without this the rule misses the ONE case it was written
    // for: RunBoard's header is `rounded-t-xl` inside a <Card> whose
    // className reads `overflow-visible` and nothing else. Found by
    // mutation-testing the guard against the defect it was built from —
    // it passed, which is how a guard becomes theatre.
    if (!step && /<Card\b|cardVariants\(/.test(l)) step = 'rounded-lg';
    if (step) marks.push({ line: i + 1, indent: l.search(/\S/), step });
  });
  const out: string[] = [];
  for (let a = 0; a < marks.length; a += 1) {
    for (let b = a + 1; b < marks.length; b += 1) {
      if (marks[b].indent <= marks[a].indent) break;
      if (marks[b].line - marks[a].line > NEAR) break;
      if (RADIUS_RUNG[marks[b].step] > RADIUS_RUNG[marks[a].step]) {
        out.push(`:${marks[b].line} ${marks[b].step} inside ${marks[a].step} (:${marks[a].line})`);
        break;
      }
    }
  }
  return out;
};

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
  { name: 'CARD_NOT_A_CARD',             entries: CARD_NOT_A_CARD,             match: 'exact',     scope: TSX,   offends: (f) => cardShellSites(f.src).length > 0 },
  { name: 'INLINE_LENGTH_ALLOWED',       entries: INLINE_LENGTH_ALLOWED.map((e) => e.file), match: 'exact', scope: FILES, offends: (f) => inlineLengthSites(f.src).length > 0 },
  { name: 'RADIUS_ARBITRARY_ALLOWED',    entries: RADIUS_ARBITRARY_ALLOWED,    match: 'exact',     scope: FILES, offends: (f) => radiusClassSites(f.src).length > 0 },
];

describe('UI chrome', () => {

  /**
   * design.md §11 carries a table of which rules are enforced. An audit
   * found it listing ten of the fifteen guards that existed — so five
   * real guards read as "not enforced" to anyone consulting the doc
   * before deciding what they could get away with. A table that lags is
   * worse than no table: it gives permission.
   *
   * The doc states the count in words. Adding a guard here breaks this
   * test until the sentence — and, one hopes, the row beside it — moves
   * too.
   */
  it('never hand-rolls the card surface the primitive already ships', () => {
    // design.md §6: `rounded-lg` is the card default and card padding is
    // p-3/p-4. Everything outside that was drift — two radii and five
    // paddings across 223 copies. `rounded-xl` is the SHELL FRAME's
    // radius, so a card wearing it repeats the curvature of the box it
    // sits inside; the primitive settles that rather than offering both.
    //
    // An element that already exists for another reason — a <button>, a
    // <section> with an id, a ContextMenu's render target — still takes
    // the one definition via `cn(cardVariants({ padding }), …)`.
    const offenders = TSX
      .filter((f) => !CARD_NOT_A_CARD.includes(f.rel))
      .filter((f) => !f.rel.startsWith('components/ui/card'))
      .flatMap((f) => cardShellSites(f.src).map(
        (line) => `${f.rel}:${line} → <Card> or cn(cardVariants({…}), …)`,
      ));
    expect(offenders).toEqual([]);
  });

  it('never writes a literal length into an inline style', () => {
    const offenders = FILES
      .filter((f) => !f.rel.startsWith('lib/scaledLength'))
      .flatMap((f) => inlineLengthSites(f.src)
        .filter(({ prop }) => !inlineLengthAllows(f.rel, prop))
        .map(
        // A corner is not a length on the Size axis. Pointing the author
        // at scaledPx() for a borderRadius would turn a silent bug into a
        // confidently wrong one.
        ({ line, prop }) => `${f.rel}:${line} → ${prop === 'borderRadius'
          ? "'var(--radius)', or useRadiusPx() from lib/radius.ts"
          : 'scaledPx() from lib/scaledLength.ts'}`,
        ));
    expect(
      offenders,
      'a number in a style object cannot be multiplied by the Size axes, ' +
        'and no other guard in this file can see it',
    ).toEqual([]);
  });

  it('keeps the tap floor off every axis', () => {
    // `min-h-tap` / `min-w-tap` are the only config entries that
    // deliberately ride NO axis: WCAG 2.5.8 is a floor in CSS pixels, and
    // a floor that shrinks with a user's Size setting is not a floor.
    // `tapHeight`/`tapWidth` above return a hardcoded 24 for them — so if
    // someone ever wraps `tap` in scaled(), those two would keep saying 24
    // while the page rendered something smaller, and the guard would go on
    // passing. This is the lid on that.
    const cfg = readFileSync(join(SRC, '..', 'tailwind.config.js'), 'utf8');
    const entries = [...cfg.matchAll(/\btap:\s*('[^']*'|"[^"]*"|`[^`]*`)/g)]
      .map((m) => m[1].replace(/^['"`]|['"`]$/g, ''));
    expect(entries.length, 'no `tap` entry found in tailwind.config.js').toBeGreaterThan(0);
    expect(
      entries.filter((v) => v !== '24px'),
      'the tap floor must stay a literal 24px on every key that defines it',
    ).toEqual([]);
  });

  it('never uses the retired 10px type step', () => {
    // `3xs` was removed from the config, so `text-3xs` no longer emits a
    // font-size AT ALL — the element would silently inherit its parent's
    // instead of rendering small. A class that quietly does nothing is
    // worse than the 8.5px it used to render at the 85% floor.
    const offenders = FILES
      .filter(({ src }) => /\btext-3xs\b/.test(src))
      .map(({ rel }) => `${rel} → text-2xs (11px); 3xs was retired`);
    expect(offenders).toEqual([]);
  });

  it('never writes a corner the Corners setting cannot reach', () => {
    const offenders = FILES
      .filter((f) => !RADIUS_ARBITRARY_ALLOWED.includes(f.rel))
      .flatMap((f) => radiusClassSites(f.src).map(
        (cls) => `${f.rel} → ${cls}; use rounded-sm|md|lg|xl, which track --radius`,
      ));
    expect(
      offenders,
      'an arbitrary radius ignores the setting, and `rounded-4xl` is a step ' +
        'the scale never defined — it compiles to nothing and falls back to square',
    ).toEqual([]);
  });

  it('never gives a control a capsule', () => {
    // design.md §3. Circular BY GEOMETRY (an icon button in a square box)
    // is exempt and detected by its own size classes.
    const offenders = TSX.flatMap((f) => capsuleControlSites(f.src).map(
      (line) => `${f.rel}:${line} → rounded-md; a capsule states a fact, it is not a control`,
    ));
    expect(offenders).toEqual([]);
  });

  it('keeps the three Corners presets wired to the token', () => {
    // The first test in this repo to read a stylesheet. Until it existed,
    // a fourth preset could ship as a silent no-op — the picker would
    // offer it, the attribute would land on <html>, and no rule would
    // answer. The same hole lets an existing preset be neutered by
    // deleting one line.
    const css = readFileSync(join(SRC, 'index.css'), 'utf8');
    const registry = readFileSync(join(SRC, 'preferences/registry.ts'), 'utf8');

    const declared = (/THEME_RADII[^=]*=\s*\[([^\]]*)\]/.exec(registry)?.[1] ?? '')
      .split(',').map((x) => x.trim().replace(/['"`]/g, '')).filter(Boolean);
    expect(declared, 'THEME_RADII should list the presets the picker offers')
      .toEqual(['sharp', 'rounded', 'pill']);

    // ':root' carries the middle preset — "rounded" IS the absence of an
    // override, which is why it has no block of its own.
    expect(/:root[^}]*--radius:\s*[\d.]+rem/.test(css), ':root must define --radius').toBe(true);
    const missing = declared
      .filter((r) => r !== 'rounded')
      .filter((r) => !new RegExp(`\\[data-radius="${r}"\\][^}]*--radius:`).test(css));
    expect(missing, 'a preset with no --radius override does nothing at all').toEqual([]);

    // EVERY value carries a unit, and that is not pedantry. `0` and `0px`
    // are different here: the scale is `calc(var(--radius) + 4px)`, and
    // `calc(<number> + <length>)` is invalid — so a bare `0` collapses all
    // seven steps to 0px instead of four, AND both JS readers
    // (`lib/radius.ts`, `DataGrid.tsx`) branch on `.endsWith('rem'|'px')`
    // and fall through to their 10px default. That would put 10px chart
    // corners against 0px CSS. Measured in Chrome: `0px` -> xl = 4px,
    // bare `0` -> xl = 0px.
    const unitless = [...css.matchAll(/(:root|\[data-radius="[a-z]+"\])[^}]*--radius:\s*([^;]+);/g)]
      .filter((m) => !/(?:px|rem|em)\s*$/.test(m[2].trim()))
      .map((m) => `${m[1]} has --radius: ${m[2].trim()} — needs a unit`);
    expect(unitless).toEqual([]);
  });

  it('assigns --radius nowhere but the stylesheet', () => {
    // One writer, one place. A component redefining it would scope the
    // Corners setting to its own subtree and the picker would appear to
    // half-work — the failure is invisible until someone tries Sharp.
    const offenders = FILES
      // Four syntaxes, because the first version of this guard knew one.
      // `applications/public/theme.ts` writes all EIGHTEEN of its custom
      // properties as `['--brand']: value` — a computed key — so the one
      // file most likely to reach for `--radius` was the one the guard
      // could not see. `setProperty` is the imperative third form.
      .filter(({ src }) => /(?:\[\s*)?['"`]?--radius['"`]?\s*(?:\]\s*)?:|setProperty\(\s*['"`]--radius['"`]/.test(src))
      .map(({ rel }) => `${rel} assigns --radius; index.css owns it`);
    expect(offenders).toEqual([]);
  });

  it('keeps the borderRadius scale derived from the token', () => {
    // Seven of nine keys must read --radius. `none` and `full` are
    // deliberately literal: they are shapes, not softness (design.md §6).
    const cfg = readFileSync(join(SRC, '..', 'tailwind.config.js'), 'utf8');
    const block = /borderRadius:\s*\{([\s\S]*?)\n\s{6}\}/.exec(cfg)?.[1] ?? '';
    expect(block, 'no borderRadius block found in tailwind.config.js').not.toBe('');
    const derived = (block.match(/var\(--radius\)/g) ?? []).length;
    expect(
      derived,
      'the borderRadius scale stopped deriving from --radius — the Corners ' +
        'picker would still stamp the attribute and nothing would move',
    ).toBeGreaterThanOrEqual(5);
    // And it rides NO size axis — the second of exactly two deliberate
    // exceptions in this system (the first is the 24px tap floor).
    // Tailwind emits ONE borderRadius scale serving buttons, cards,
    // panels and dialogs, so any axis chosen is right for one of them and
    // wrong for the other three. design.md §8 carries the arithmetic.
    expect(
      (block.match(/var\(--size-/g) ?? []).length,
      'the borderRadius scale must stay size-invariant — see design.md §8',
    ).toBe(0);

    for (const key of ['sm', 'md', 'lg', 'xl'])
      expect(new RegExp(`['"\`]?${key}['"\`]?\\s*:`).test(block), `${key} missing`).toBe(true);
  });

  it('never lets a primitive animate its own geometry', () => {
    // `transition-all` on a control animates height, padding, font-size
    // and border-radius along with the colours nobody objected to. The
    // Size slider writes straight to the DOM on every drag frame, so each
    // 60fps frame retargeted an in-flight 150ms transition and every
    // Button and Badge rubber-banded behind the cursor — a
    // layout-triggering animation on a continuous control that nobody
    // chose. `transition-control` names what a control may animate.
    //
    // Scoped to the primitives on purpose: the other `transition-all`
    // sites are progress bars animating width, which is the point of them.
    const PRIMITIVES = ['components/ui/button.tsx', 'components/ui/badge.tsx'];
    const offenders = TSX
      .filter((f) => PRIMITIVES.includes(f.rel))
      .filter((f) => /\btransition-all\b/.test(f.src))
      .map((f) => `${f.rel} → transition-control; \`all\` includes geometry`);
    expect(offenders).toEqual([]);
  });

  it('never gives a popup a corner it will not clip', () => {
    // A rounded box that hands its edge to a square child and does not
    // clip gets that child painted over its own arc — 0.293r, so 4.7px at
    // Pill, and the artifact GROWS as the reader asks for softer corners.
    // Seven DataGrid menus had it and five siblings did not; the split
    // was one rule being re-decided at every call site.
    const offenders = TSX.flatMap((f) => {
      const out: string[] = [];
      for (const m of codeOnly(f.src).matchAll(
        /<\w*Popup\b[^>]{0,300}?className=(?:"([^"]*)"|\{`([^`]*)`\})/g,
      )) {
        const cls = m[1] ?? m[2] ?? '';
        if (!/\brounded-(?:sm|md|lg|xl|2xl|3xl)\b/.test(cls)) continue;
        if (/overflow-/.test(cls)) continue;
        out.push(`${f.rel} → add overflow-hidden; a rounded popup must clip its rows`);
      }
      return out;
    });
    expect(offenders).toEqual([]);
  });

  it('never spans the viewport with a fixed strip', () => {
    // The shell owns the frame. A `fixed bottom-0 left-0 right-0` bar
    // reaches past <main> into the sidebar and the 8px chrome gutter, and
    // swallows both of the card's bottom corners — 20px of arc at Pill,
    // gone at every preset because the overlap is 49px deep.
    // A page's own save bar belongs to the page: `sticky bottom-2` inside
    // the scroller it already has. kpi/config/KpiConfiguration.tsx got
    // there first and wrote down why.
    const offenders = TSX
      .filter((f) => !f.rel.startsWith('shells/'))
      .filter((f) => /fixed\s+(?:bottom-0\s+left-0\s+right-0|inset-x-0\s+bottom-0|top-0\s+left-0\s+right-0)/
        .test(codeOnly(f.src)))
      .map((f) => `${f.rel} → sticky inside the page, not fixed to the viewport`);
    expect(offenders).toEqual([]);
  });

  it('keeps the edge-to-edge card variant clipping', () => {
    // `padding="none"` exists for a card whose children own its edges — a
    // DataGrid, a divided list. That is precisely the case that must clip,
    // and leaving it to 22 call sites meant four of them did not. The
    // variant carries it; a call site that genuinely must not clip says
    // `overflow-visible` and wins, because className merges last.
    const card = readFileSync(join(SRC, 'components/ui/card.tsx'), 'utf8');
    const none = /none:\s*"([^"]*)"/.exec(card)?.[1];
    expect(none, 'no `none` padding variant found in card.tsx').toBeDefined();
    expect(
      none,
      'the edge-to-edge variant must clip, or every call site re-decides it',
    ).toContain('overflow-hidden');
  });

  it('never rounds a child more than the parent it sits against', () => {
    const offenders = TSX.flatMap((f) => nestedRadiusSites(f.src).map(
      (hit) => `${f.rel}${hit} — the offsets are fixed, so this is wrong at EVERY preset`,
    ));
    expect(
      offenders,
      'concentric corners want inner = outer − gap; inner LARGER than outer ' +
        'has no gap that makes it right',
    ).toEqual([]);
  });

  it('is counted correctly in design.md', () => {
    const doc = readFileSync(join(SRC, '..', 'design.md'), 'utf8');
    // Built, not listed: the hand-written map ran out at every tenth
    // guard, and the failure it produced ("expected undefined") named
    // the map, not the missing table row.
    const ONES = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven',
      'eight', 'nine'];
    const TEENS = ['ten', 'eleven', 'twelve', 'thirteen', 'fourteen',
      'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen'];
    const TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty',
      'seventy', 'eighty', 'ninety'];
    const NUMBER: Record<string, number> = {};
    ONES.forEach((w, i) => w && (NUMBER[w] = i));
    TEENS.forEach((w, i) => (NUMBER[w] = 10 + i));
    for (let t = 2; t <= 9; t++) {
      NUMBER[TENS[t]] = t * 10;
      ONES.forEach((w, u) => w && (NUMBER[`${TENS[t]}-${w}`] = t * 10 + u));
    }
    const claimed = /\b([A-Za-z-]+) live in `src\/components\/ui\/chrome\.test\.ts`/
      .exec(doc)?.[1]?.toLowerCase();
    const actual = (readFileSync(join(SRC, 'components/ui/chrome.test.ts'), 'utf8')
      .match(/^ {2}it\(/gm) ?? []).length;
    expect(
      claimed && NUMBER[claimed],
      `design.md §11 says "${claimed}", this file has ${actual} guards — ` +
        'update the sentence AND add the row',
    ).toBe(actual);
  });

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

  // ── Guard 31 ────────────────────────────────────────────────────────
  // `@media print` re-declares, at its light value, every colour token
  // the dark themes override.  That is a copy, and a copy drifts: add
  // `--card-hover` to `.dark` alone and a dark-mode user prints one more
  // invisible surface.  Nobody reads a print stylesheet, and nobody
  // reviews a printout, so the drift would live for years.
  //
  // This asserts set equality both ways AND that each printed value is
  // byte-identical to `:root`'s, which is the only thing that makes the
  // duplication safe to keep.
  it('the print block re-declares every dark-overridden token, at its light value', () => {
    const css = readFileSync(join(SRC, 'index.css'), 'utf8');
    const block = (name: string, re: RegExp): Record<string, string> => {
      const m = re.exec(css);
      if (!m) throw new Error(`index.css: the ${name} block moved or was renamed`);
      const out: Record<string, string> = {};
      for (const d of m[1].matchAll(/(--[a-z0-9-]+):\s*(.+?);/g)) {
        if (!(d[1] in out)) out[d[1]] = d[2].trim();
      }
      return out;
    };
    // index.css splits `:root` across several blocks (fonts, colour,
    // swatches, size).  Merge them in source order, exactly as the
    // cascade does — reading only the first finds no colour at all.
    const root: Record<string, string> = {};
    for (const m of css.matchAll(/^ {2}:root \{$([\s\S]*?)^ {2}\}$/gm)) {
      for (const d of m[1].matchAll(/(--[a-z0-9-]+):\s*(.+?);/g)) root[d[1]] = d[2].trim();
    }
    const dark = block('.dark', /^ {2}\.dark \{$([\s\S]*?)^ {2}\}$/m);
    const print = block(
      'print',
      /^ {2}:root, :root\.dark, :root\.dark\[data-theme\] \{$([\s\S]*?)^ {2}\}$/m,
    );

    const problems: string[] = [];
    for (const k of Object.keys(dark)) {
      if (!(k in print)) problems.push(`${k}: .dark overrides it, print never puts it back`);
      else if (print[k] !== root[k]) {
        problems.push(`${k}: print says ${print[k]}, :root says ${root[k]}`);
      }
    }
    for (const k of Object.keys(print)) {
      if (!(k in dark)) problems.push(`${k}: print resets it, but .dark never touched it`);
    }
    expect(problems).toEqual([]);
  });

  // ── Guard 32 ────────────────────────────────────────────────────────
  // A class Tailwind never emits is invisible. There is no error and no
  // warning; the element simply renders unstyled, and it looks like a
  // design choice. This project is Tailwind 3 while its primitives were
  // pasted from shadcn's v4 era, so classes across select, dialog, sheet,
  // table, tooltip, avatar, button, badge, textarea and DataGrid
  // compiled to nothing: invalid fields drew no error ring, disabled
  // options did not dim, sheets did not animate, every modal scrim
  // rendered without its blur, `truncate` sat next to a `max-w-55` that
  // did not exist, and a Badge rendered as a link had no hover at all.
  // Every one of those files reads correctly. Only compiling them finds
  // it.
  //
  // The first version of this guard compiled only VARIANT-PREFIXED
  // tokens, and reported one only when the bare utility compiled and the
  // prefixed form did not — a trick that let prose filter itself out, at
  // the price of being blind to a class that is dead on both halves.
  // That blind spot was not theoretical: it hid `backdrop-blur-xs`,
  // `field-sizing-content`, `max-w-55`, `underline-offset-3` and
  // `outline-hidden`, five of the six real defects in the sweep that
  // followed.
  //
  // So this compiles bare utilities too, and pays for it with three
  // STRUCTURAL filters instead of an allowlist — an allowlist here would
  // silently re-admit the exact bug the guard exists to catch:
  //
  //   1. comments are stripped first. An apostrophe in prose ("it's")
  //      opens a fake string literal and swallows the sentence, which is
  //      where 69 phantom candidates like `hand-rolled` and `read-only`
  //      came from.
  //   2. a quoted string immediately followed by `:` is an object KEY,
  //      not a class — that is how cva's `"icon-sm":` variant names were
  //      being read as dead utilities.
  //   3. classes index.css defines itself (`.ai-response`) count as
  //      emitted. They are ours; Tailwind is simply not the one that
  //      emits them.
  //
  // With those three the candidate set is ~1050 and the dead set is 0,
  // with nothing exempted by name.
  //
  // Two limits worth knowing before trusting a green run:
  //   * it proves a rule is EMITTED, not that the rule is VALID.
  //     tailwindcss-animate negates by string-prefixing `-`, and against
  //     this repo's calc() spacing that produced `-calc(…)`: a rule
  //     exists, the guard is happy, and the browser drops the whole
  //     `transform`. That one is now pinned by a plain-rem
  //     `animationTranslate` scale in the config.
  //   * a module that builds class strings without `className`/`cn(`
  //     nearby contributes nothing — `lib/status.ts` holds the tone
  //     recipes in a bare Record and is invisible here.
  it('never adds a raw form control below the tap floor', () => {
    // The tap-floor guard above scans <button|a|summary> only, so every
    // raw <input>, <textarea> and <select> in the app has always been
    // invisible to it — including 32 bare checkboxes, which render ~13px
    // and can never reach 24 without the token. That blind spot is why
    // `min-h-tap` was missing from the <Input> primitive itself, and so
    // from all 66 of its call sites, until it was added.
    //
    // A RATCHET rather than a pass/fail, because 241 raw controls
    // predate this and a guard that fails 241 times is a guard someone
    // deletes. It fails in BOTH directions on purpose: upward means a
    // new offender was added, downward means debt was paid and the
    // number below is now a lie. Same discipline as the exemption lists
    // — no baseline may outlive its reason.
    const BASELINE = { input: 241, textarea: 21, select: 19 };

    const countBare = (tag: string) => {
      let n = 0;
      for (const { src } of TSX) {
        // Walk to the element's own '>' ignoring any inside {...}, so an
        // arrow function in onChange doesn't truncate the attributes.
        const re = new RegExp(`<${tag}\\b`, 'g');
        let m: RegExpExecArray | null;
        while ((m = re.exec(src))) {
          let i = m.index + m[0].length, depth = 0;
          while (i < src.length) {
            const ch = src[i];
            if (ch === '{') depth++;
            else if (ch === '}') depth--;
            else if (ch === '>' && depth === 0) break;
            i++;
          }
          if (!src.slice(m.index, i).includes('min-h-tap')) n++;
        }
      }
      return n;
    };

    const now = {
      input: countBare('input'),
      textarea: countBare('textarea'),
      select: countBare('select'),
    };
    expect(
      now,
      'raw form controls missing min-h-tap changed. UP means a new one ' +
        'was added below the 24px floor (WCAG 2.5.8) — give it the token. ' +
        'DOWN means debt was paid — lower the baseline so it keeps ' +
        'guarding the real number.',
    ).toEqual(BASELINE);
  });

  it('every class the code writes compiles to a real rule', async () => {
    // `class=` as well as `className=`: formatAI.ts builds markup for
    // dangerouslySetInnerHTML and its classes are as real as any JSX
    // one. NOT `className:` — that is Leaflet's divIcon option, whose
    // value is a marker identity hook (`vehicle-moving`), not a utility;
    // five of those turned up the moment it was included.
    const MARK = /(class(?:Name)?\s*=\s*)|(\b(?:cn|cva|clsx|twMerge)\s*\()/g;
    // String literals reachable from a class-building context. A plain
    // `className="…"` contributes exactly one literal — scanning past it
    // walks into the next attribute, which is how `style="box-shadow:…"`
    // used to arrive here as a candidate class.
    const literals = (input: string): string[] => {
      const s = codeOnly(input);
      const out: string[] = [];
      for (const m of s.matchAll(MARK)) {
        let i = m.index! + m[0].length;
        let depth: number;
        if (m[1]) {
          while (i < s.length && ' \t\n'.includes(s[i])) i++;
          if (s[i] === '"' || s[i] === "'") {
            let j = i + 1;
            while (j < s.length && s[j] !== s[i]) j += s[j] === '\\' ? 2 : 1;
            out.push(s.slice(i + 1, j));
            continue;
          }
          if (s[i] !== '{') continue;
          depth = 0;
        } else {
          depth = 1;
        }
        for (; i < s.length; i++) {
          const c = s[i];
          if (c === '{' || c === '(') depth++;
          else if (c === '}' || c === ')') { if (--depth <= 0) break; }
          else if (c === '"' || c === "'" || c === '`') {
            let j = i + 1;
            while (j < s.length && s[j] !== c) j += s[j] === '\\' ? 2 : 1;
            // An object KEY, skipped: `"icon-sm": "…"`. Look BACKWARDS
            // as well as forwards, or this eats the true branch of every
            // ternary — `cond ? 'a' : 'b'` puts a colon after a real
            // class string, and that dropped 115 live literals.
            let k = j + 1;
            while (k < s.length && ' \t\n'.includes(s[k])) k++;
            let b = i - 1;
            while (b >= 0 && ' \t\n'.includes(s[b])) b--;
            const isKey = s[k] === ':' && (s[b] === '{' || s[b] === ',');
            if (!isKey) out.push(s.slice(i + 1, j));
            i = j;
          }
        }
      }
      return out;
    };
    // Split on colons at bracket depth zero, so `data-[a:b]:flex` keeps
    // its arbitrary value intact.
    const split = (t: string): [string[], string] => {
      let depth = 0, cur = '';
      const parts: string[] = [];
      for (const ch of t) {
        if (ch === '[' || ch === '(') depth++;
        else if (ch === ']' || ch === ')') depth--;
        if (ch === ':' && depth === 0) { parts.push(cur); cur = ''; }
        else cur += ch;
      }
      return [parts, cur];
    };
    const UTIL = /^-?[a-z][a-z0-9]*(-[a-z0-9.]+)*(\/[0-9]+)?(\[[^\]]*\])?$/;
    const cand = new Map<string, string>();
    for (const { src } of FILES) {
      for (const lit of literals(src)) {
        for (const raw of lit.replace(/\$\{[^}]*\}/g, ' ').split(/\s+/)) {
          const t = raw.replace(/^["'`\\]+|["'`\\]+$/g, '');
          const [v, u] = split(t);
          if (!u || !UTIL.test(u)) continue;
          // A bare word with no hyphen or bracket is almost always a
          // stray identifier, and `flex`-shaped ones compile anyway.
          if (v.length ? v.every(Boolean) : u.includes('-') || u.includes('[')) {
            cand.set(t, u);
          }
        }
      }
    }

    const probe = [...new Set([...cand.keys(), ...cand.values()])].join(' ');
    const [{ default: postcss }, { default: tw }, cfgMod] = await Promise.all([
      import('postcss'),
      import('tailwindcss'),
      // The Tailwind config is plain JS and ships no declarations; the
      // guard only spreads it, so `unknown` costs nothing here.
      // @ts-expect-error -- untyped JS config
      import('../../../tailwind.config.js'),
    ]);
    const cfg = (cfgMod as { default: Record<string, unknown> }).default;
    const { css } = await postcss([
      tw({ ...cfg, content: [{ raw: `<i class="${probe}"></i>`, extension: 'html' }] }),
    ]).process('@tailwind utilities;', { from: undefined });

    // Read class names straight out of the emitted selectors, undoing
    // CSS escaping, and stop at the first character that ends a class.
    const emitted = new Set<string>();
    for (let i = 0; i < css.length; i++) {
      if (css[i] !== '.') continue;
      let j = i + 1, name = '';
      for (; j < css.length; j++) {
        const c = css[j];
        if (c === '\\') { name += css[++j] ?? ''; continue; }
        if (' ,{>+~[:)\n\t'.includes(c)) break;
        name += c;
      }
      if (name) emitted.add(name);
      i = j;
    }
    // …plus the ones we write ourselves.
    // Comments stripped first — index.css's prose mentions `.md`, `.ts`
    // and `.config`, and harvesting those would bless a dead class that
    // happened to share the name.
    for (const m of readFileSync(join(SRC, 'index.css'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .matchAll(/\.([a-z][a-z0-9-]*)\b/g)) emitted.add(m[1]);
    // …and CSS injected from TS. LiveMap builds a <style> holding
    // `.vehicle-moving { animation: … }` and writes the class into a
    // markup string, so the class is ours and defined, just not in a
    // .css file. The `{` is required here so this cannot harvest a
    // property access or a file extension out of ordinary code.
    for (const { src } of FILES) {
      for (const m of src.matchAll(/\.([a-z][a-z0-9-]{2,})\s*\{/g)) emitted.add(m[1]);
    }

    const dead = [...cand]
      .filter(([t]) => !emitted.has(t))
      .map(([t, u]) =>
        t === u
          ? `${t} — no such utility`
          : `${t} — the variant emits nothing (bare \`${u}\` ${
              emitted.has(u) ? 'compiles' : 'is dead too'
            })`)
      .sort();
    expect(
      dead,
      'these classes are written but never generated. Check the spelling ' +
        'against Tailwind 3: v4 idioms (`data-open:`, `**:`, `[a]:`, ' +
        '`in-data-[…]:`, `backdrop-blur-xs`, `field-sizing-content`) and ' +
        'steps the config never defined both produce no rule at all',
    ).toEqual([]);
  }, 20_000);
});
