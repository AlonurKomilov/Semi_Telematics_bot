#!/usr/bin/env node
/**
 * Drift guards for the persona-aware UI conventions.
 *
 * TWO independent guards run in sequence; the script exits non-zero
 * if either fails:
 *
 *   1. **Role-literal drift** — bans stray ``role === '<persona>'``
 *      (and ``realRole === …``, ``activeView === …``, plus their
 *      ``!==`` cousins) comparisons outside the small allow-list of
 *      places where they're legitimate (form state on admin pages,
 *      message author in /ai/chat, etc.).  Every persona-aware UI
 *      decision should funnel through ``useShellConfig`` — this
 *      guard keeps the convention honest.
 *
 *   2. **Section-contract enforcement** — bans ``useShellConfig``
 *      imports inside ``src/features/<feature>/sections/**.tsx``.
 *      Sections must be persona-agnostic by Pattern B contract:
 *      they receive pre-resolved values via ``sectionProps`` that
 *      the page wrapper computed once at the page level.  A section
 *      reaching into ``useShellConfig`` defeats the "add a persona
 *      in one config edit" promise of Pattern B because every such
 *      section would need a code change for the new persona too.
 *
 * Run as part of CI:
 *
 *     npm run check:role-drift
 *
 * Exit code 0 = clean, 1 = drift found in either guard.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');
const SRC = join(ROOT, 'src');

// ── Guard 1: role-literal drift ───────────────────────────────────

// PERSONA keys — the presentation identities.  Must not be hardcoded in
// `=== / !==` comparisons outside the hook + context, AND every one must
// appear in both host maps (Guard 3).  Mirrors the Persona union in
// features/_lib/types.ts — both lists stay in lockstep per persona.
//
// NB: "manager" is NOT a persona (nor a role) — it's a per-user tier
// (is_manager) layered on the base role.  A recruiter manager IS a
// recruiter for presentation, so nothing manager-related belongs here.
const ROLE_KEYS = [
  'owner', 'admin', 'fleet', 'safety', 'dispatcher', 'driver',
  'hr', 'accounting', 'recruiter',
];

// Files where a `role === '<persona>'` comparison is a legitimate,
// non-shell concern and should be ignored by the drift check:
//   • useShellConfig    — the hook itself owns these comparisons
//   • RoleViewContext   — owns the view-switching machinery
//   • Sidebar           — filters nav by the auth user's real role
//   • settings/Invites  — `role` is the form state of the invite being
//                         drafted, not the current user's persona
//   • settings/WorkHours — `target_role` is a field on schedule rows
//   • ai/Chat           — `msg.role === 'user'` is the chat message
//                         author (user vs. model), not a persona
//   • permissions/Permissions — `role` is the matrix's role COLUMN (the row
//                         of permissions being edited) + the owner-lockout
//                         protected check; not the current user's persona
const ALLOWLIST = new Set([
  'src/hooks/useShellConfig.ts',
  'src/context/RoleViewContext.tsx',
  'src/components/Sidebar.tsx',
  'src/features/settings/Invites.tsx',
  'src/features/settings/WorkHours.tsx',
  'src/features/ai/Chat.tsx',
  // The permissions feature is exempt as a FAMILY, not as one file: on
  // this page a role literal is the ROW BEING EDITED (which role's
  // grants am I looking at, which tier stores them), never "who is the
  // viewer".  Splitting Permissions.tsx into a row model + a lens moved
  // that same code into these two files.
  'src/features/permissions/Permissions.tsx',
  'src/features/permissions/permRows.ts',
  'src/features/permissions/RoleLens.tsx',
]);

// Match BOTH === and !== so an "if (role !== 'owner')" check is
// caught with the same weight as "===".  The leading
// ``(?<![\w.])`` is a negative lookbehind that excludes object
// property access — ``obj.role === 'fleet'`` shouldn't be flagged
// because the LHS is a form-state object accessor, not a persona
// check.  ``selected.role`` (Users/Invites pages) is allow-listed
// at the file level anyway, but the lookbehind handles future
// pages we haven't audited yet.
const ROLE_PATTERN = new RegExp(
  `(?<![\\w.])(?:role|realRole|activeView)\\s*[!=]==\\s*['"](?:${ROLE_KEYS.join('|')})['"]`,
);

// ── Guard 2: section-contract enforcement ─────────────────────────

// Only ``.tsx`` / ``.ts`` files in this glob are subject to the
// section-contract guard.  Matched against the POSIX path relative
// to the repo root.
const SECTION_PATH_PATTERN = /^src\/features\/[^/]+\/sections\//;

// Any of these import patterns are forbidden inside a section file.
// Matched against COMMENT- AND STRING-STRIPPED source (see helper
// below) so doc comments mentioning "useShellConfig" don't trip.
const FORBIDDEN_IN_SECTION = [
  {
    name: 'useShellConfig import',
    pattern: /from\s+['"][^'"]*useShellConfig['"]/,
    why: 'Sections must be persona-agnostic — receive resolved values via sectionProps, never read persona from context.',
  },
];

// Sections that legitimately need persona-conditional rendering may
// be allow-listed here, BUT prefer to refactor the persona-aware
// decision UP to the page wrapper (see features/overview/Overview.tsx
// + personaConfig.ts for the canonical pattern).  Empty by default —
// add a path here only after exhausting that refactor.
const SECTION_ALLOWLIST = new Set([
  // (empty)
]);

// ── Source stripping (drops comments + string literals) ───────────

/**
 * Strip ``//`` and ``/* * /`` comments from source code while
 * RESPECTING string-literal boundaries.  Preserves newline count so
 * line numbers stay accurate in violation reports.
 *
 * Uses a character-by-character state machine that tracks whether
 * we're inside a single-quoted, double-quoted, or template-literal
 * string.  Inside a string, ``//`` is not a comment.  This
 * correctly handles inputs like:
 *
 *   const api = "https://example.com"; if (role === 'fleet') ...
 *
 * The original regex-based implementation would have eaten everything
 * after ``//`` in the URL, hiding the real ``role === 'fleet'``
 * violation.
 *
 * String CONTENTS survive — the role-literal regex needs to see
 * ``'fleet'`` inside ``role === 'fleet'``, and the section-contract
 * regex needs to see the import path inside ``from '...'``.  Both
 * are intentional.  The remaining limitation: a stringified version
 * of a violation (e.g. ``const s = "role === 'owner'";``) still
 * trips the regex — that's the documented trade-off (see test
 * ``DOCUMENTED LIMITATION`` in check-role-drift.test.ts).
 *
 * Not a full TS parser — doesn't handle regex literals (e.g.
 * ``/role/`` could be misread as the start of a block comment if
 * preceded by an operator).  Real codebases don't tend to put
 * persona-aware decisions next to regex literals; if this becomes a
 * concern, switch to a tokenizer like ``@babel/parser``.
 */
function stripComments(src) {
  let out = '';
  const n = src.length;
  let i = 0;
  let inStr = null; // null | '"' | "'" | '`'

  while (i < n) {
    const c = src[i];
    const next = src[i + 1];

    if (inStr) {
      // Inside a string — pass through, watching for escape + close.
      if (c === '\\' && i + 1 < n) {
        out += c + src[i + 1];
        i += 2;
        continue;
      }
      if (c === inStr) {
        inStr = null;
      }
      out += c;
      i++;
      continue;
    }

    // Not in a string — check for string OPEN or comment START.
    if (c === '"' || c === "'" || c === '`') {
      inStr = c;
      out += c;
      i++;
      continue;
    }

    if (c === '/' && next === '*') {
      // Block comment — replace with spaces, preserve newlines.
      const end = src.indexOf('*/', i + 2);
      const stop = end === -1 ? n : end + 2;
      for (let j = i; j < stop; j++) {
        out += src[j] === '\n' ? '\n' : ' ';
      }
      i = stop;
      continue;
    }

    if (c === '/' && next === '/') {
      // Line comment — skip to end of line; the '\n' itself stays.
      while (i < n && src[i] !== '\n') i++;
      continue;
    }

    out += c;
    i++;
  }

  return out;
}


// ── Walker ────────────────────────────────────────────────────────

function walk(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry);
    const s = statSync(p);
    if (s.isDirectory()) walk(p, out);
    else if (entry.endsWith('.ts') || entry.endsWith('.tsx')) out.push(p);
  }
  return out;
}

// ── Run both guards ───────────────────────────────────────────────

const literalViolations = [];
const sectionViolations = [];

for (const file of walk(SRC)) {
  const rel = relative(ROOT, file).split('\\').join('/');
  const rawSrc = readFileSync(file, 'utf8');
  const rawLines = rawSrc.split('\n');

  // Comment-stripping (block + line) kills the main false-positive
  // source: doc comments that mention persona names or hook paths
  // for documentation purposes.  Both guards use the same cleaned
  // view.  String contents survive because the role-literal regex
  // needs to see the persona name INSIDE the quotes (``'fleet'``),
  // and the section-contract regex needs to see the import path
  // INSIDE the quotes (``'.../useShellConfig'``).
  const commentsStripped = stripComments(rawSrc);
  const commentsStrippedLines = commentsStripped.split('\n');

  // Guard 1 — role-literal drift
  if (!ALLOWLIST.has(rel)) {
    commentsStrippedLines.forEach((line, i) => {
      if (ROLE_PATTERN.test(line)) {
        literalViolations.push({
          file: rel,
          line: i + 1,
          text: rawLines[i].trim(),
        });
      }
    });
  }

  // Guard 2 — section-contract enforcement.  Uses comment-stripped
  // source so a doc comment mentioning useShellConfig in a section
  // doesn't trip.  String literals containing "useShellConfig" are
  // a theoretical false-positive source but extraordinarily rare in
  // practice (sections don't typically construct dynamic import
  // paths as strings).
  if (SECTION_PATH_PATTERN.test(rel) && !SECTION_ALLOWLIST.has(rel)) {
    commentsStrippedLines.forEach((line, i) => {
      for (const rule of FORBIDDEN_IN_SECTION) {
        if (rule.pattern.test(line)) {
          sectionViolations.push({
            file: rel,
            line: i + 1,
            text: rawLines[i].trim(),
            rule: rule.name,
            why: rule.why,
          });
        }
      }
    });
  }
}

// ── Guard 3: role→host map completeness ───────────────────────────
//
// The persona→subdomain map is hand-duplicated in AuthContext.tsx
// (ROLE_TO_HOST, drives post-login redirect) and RoleViewContext.tsx
// (ROLE_HOST, drives the Owner/Admin "view as" switcher).  When the two
// drift, a persona logs in fine but can't be previewed (or vice-versa)
// — exactly how hr/accounting shipped half-wired.  This guard parses the
// keys of both maps and asserts each covers EVERY persona in ROLE_KEYS.
const hostMaps = [
  ['src/context/AuthContext.tsx', 'ROLE_TO_HOST'],
  ['src/context/RoleViewContext.tsx', 'ROLE_HOST'],
];
const hostMapViolations = [];
for (const [rel, name] of hostMaps) {
  let txt;
  try {
    txt = readFileSync(join(ROOT, rel), 'utf8');
  } catch {
    // File absent in this tree (e.g. the integration test's temporary
    // src/ fixture, which only writes the files under test) — skip,
    // don't crash the whole script.
    continue;
  }
  // Object body between `const <name> ... = {` and the map's closing
  // `}` at the START of a line.  Anchoring on ``\n}`` (not the first
  // ``}``) is essential: the values are template literals like
  // ``dash.${APEX_DOMAIN}`` whose inner ``}`` would end the match early.
  const m = txt.match(new RegExp(`${name}[^=]*=\\s*\\{([\\s\\S]*?)\\n\\}`));
  if (!m) {
    hostMapViolations.push(`${rel}: could not find map ${name}`);
    continue;
  }
  const keys = new Set();
  for (const km of m[1].matchAll(/(?:^|\n)\s*([a-z_]+)\s*:/g)) keys.add(km[1]);
  for (const persona of ROLE_KEYS) {
    if (!keys.has(persona)) {
      hostMapViolations.push(`${rel}: ${name} is missing persona '${persona}'`);
    }
  }
}

let failed = false;

if (hostMapViolations.length > 0) {
  failed = true;
  console.error('✗ Role→host map completeness check failed.');
  console.error('  Every persona must appear in BOTH AuthContext.ROLE_TO_HOST');
  console.error('  (post-login redirect) AND RoleViewContext.ROLE_HOST');
  console.error('  (persona switcher), or the persona half-works.\n');
  for (const v of hostMapViolations) console.error(`  ${v}`);
  console.error('');
}

if (literalViolations.length > 0) {
  failed = true;
  console.error('✗ Role-literal drift check failed.');
  console.error('  Move these comparisons into hooks/useShellConfig.ts,');
  console.error('  or add the file to ALLOWLIST in scripts/check-role-drift.mjs');
  console.error('  if the comparison is genuinely not a persona check.\n');
  for (const v of literalViolations) {
    console.error(`  ${v.file}:${v.line}  ${v.text}`);
  }
  console.error('');
}

if (sectionViolations.length > 0) {
  failed = true;
  console.error('✗ Section-contract drift check failed.');
  console.error('  Sections must be persona-agnostic.  Refactor the');
  console.error('  persona-aware decision UP to the page wrapper and');
  console.error('  pass the resolved value via sectionProps — see');
  console.error('  features/overview/Overview.tsx + personaConfig.ts');
  console.error('  for the canonical pattern.\n');
  for (const v of sectionViolations) {
    console.error(`  ${v.file}:${v.line}  [${v.rule}]  ${v.text}`);
    console.error(`    ${v.why}`);
  }
  console.error('');
}

// ── Router perm-guard keys must exist in the Permission type ─────────
// A <Route> guarded on a flag the Permission interface (types/index.ts)
// doesn't define is silently ALWAYS-false: viewHasAny() returns false and
// ProtectedRoute bounces the user to "/".  The sidebar (featureCatalog)
// and the route then disagree — the nav shows the link but the page won't
// open.  This is exactly how the `_own` (should be `_vehicle`) typo broke
// AI / Vehicles / Live Map / Alerts / Scorecards for own-scope personas.
const permKeyViolations = [];
{
  // The authoritative set of permission flags is the backend FeatureSet
  // dataclass (capabilities/permissions/roles.py) — that's what gets
  // serialized to the client.  src/types/index.ts is a hand-maintained
  // (and incomplete) mirror, so validating against it gives false
  // positives; read the real source instead.
  let featureSetSrc = '';
  let routerSrc = '';
  try {
    featureSetSrc = readFileSync(
      resolve(ROOT, '../../capabilities/permissions/roles.py'), 'utf8',
    );
    routerSrc = readFileSync(join(SRC, 'router.tsx'), 'utf8');
  } catch {
    // A file moved — skip rather than crash the whole guard.
  }
  if (featureSetSrc && routerSrc) {
    // Dataclass field declarations: `can_xxx: bool = ...`.
    const known = new Set(
      [...featureSetSrc.matchAll(/\b(can_[a-z_]+)\s*:\s*bool/g)].map((m) => m[1]),
    );
    for (const guard of stripComments(routerSrc).matchAll(/perm=(?:\{[^}]*\}|"[^"]*")/g)) {
      for (const km of guard[0].matchAll(/['"](can_[a-z_]+)['"]/g)) {
        if (!known.has(km[1])) permKeyViolations.push(km[1]);
      }
    }
  }
}

if (permKeyViolations.length > 0) {
  failed = true;
  console.error('✗ Router permission-key check failed.');
  console.error('  These <Route perm=...> flags are NOT in the Permission');
  console.error('  type (src/types/index.ts) — viewHasAny() is always false');
  console.error('  for them, so the route silently bounces to "/" even though');
  console.error('  the sidebar shows the link.  Own-scope is `_vehicle`, not');
  console.error('  `_own`.\n');
  for (const v of [...new Set(permKeyViolations)]) console.error(`  router.tsx: ${v}`);
  console.error('');
}

if (failed) process.exit(1);

console.log(
  '✓ Role-drift check passed — no stray persona literals, section-contract, or router perm-key violations.',
);
process.exit(0);
