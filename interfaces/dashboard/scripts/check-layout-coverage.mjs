#!/usr/bin/env node
/**
 * Pattern B layout-coverage audit.
 *
 * For every ``src/features/<feature>/layouts.ts`` file:
 *
 *   1. ERROR if a layout uses a persona key that isn't in the
 *      canonical ``Persona`` union (typo, removed role, etc.).
 *   2. WARN  if any canonical persona is missing a layout entry.
 *      Missing personas fall back to the owner-superset layout at
 *      runtime via PageLayoutHost — this is non-fatal, but worth
 *      knowing so the persona isn't accidentally invisible.
 *
 * Run as part of CI:
 *
 *     npm run check:layout-coverage
 *
 * Exit code 0 = no errors (warnings still allowed); 1 = errors.
 *
 * Regex-based parsing — keeps the script dependency-free and runnable
 * in any CI environment without a TS transpiler.  The runtime
 * fallback in PageLayoutHost is the real safety net; this script
 * catches drift early.
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');
const FEATURES_DIR = join(ROOT, 'src/features');
const TYPES_FILE = join(FEATURES_DIR, '_lib/types.ts');

if (!existsSync(FEATURES_DIR)) {
  console.log('No src/features/ directory — Pattern B not bootstrapped yet, skipping.');
  process.exit(0);
}
if (!existsSync(TYPES_FILE)) {
  console.error(`FAIL: ${TYPES_FILE} missing — Pattern B types not in place.`);
  process.exit(1);
}

// ── Extract canonical Persona keys from types.ts ────────────────
//
// The body of the union runs from the first ``=`` after ``export type
// Persona`` until either:
//   (a) the next ``;`` on its own (TS style with semicolons), OR
//   (b) the start of the next top-level ``export`` declaration
//       (TS style without semicolons; common in Prettier configs
//       that have ``semi: false``), OR
//   (c) end of file.
//
// Whichever comes first.  This makes the audit robust against the
// no-semicolons code style some teams prefer; the original regex
// required a trailing ``;`` and would fail silently on its absence.
const typesSrc = readFileSync(TYPES_FILE, 'utf8');
const personaTypeMatch = typesSrc.match(
  /export type Persona\s*=([\s\S]*?)(?:;|\n(?=export\b)|$)/,
);
if (!personaTypeMatch) {
  console.error('FAIL: could not find `export type Persona` in types.ts');
  process.exit(1);
}
const CANONICAL = [...personaTypeMatch[1].matchAll(/'([a-z_]+)'/g)].map(
  (m) => m[1],
);
if (CANONICAL.length === 0) {
  console.error('FAIL: Persona union appears to be empty');
  process.exit(1);
}
console.log(`Canonical personas (${CANONICAL.length}): ${CANONICAL.join(', ')}`);
console.log();

// ── Walk features/ for layouts.ts files ─────────────────────────
const features = readdirSync(FEATURES_DIR, { withFileTypes: true })
  .filter((d) => d.isDirectory() && d.name !== '_lib')
  .map((d) => d.name)
  .sort();

let errors = 0;
let warnings = 0;
let featuresWithLayouts = 0;

for (const feature of features) {
  const layoutsFile = join(FEATURES_DIR, feature, 'layouts.ts');
  if (!existsSync(layoutsFile)) {
    console.log(`  · ${feature}: no layouts.ts (not a Pattern B feature)`);
    continue;
  }
  featuresWithLayouts++;
  const src = readFileSync(layoutsFile, 'utf8');

  // Find every persona key used as an object property assigning to an
  // array literal.  Matches ``fleet: [`` and ``'fleet': [`` patterns.
  const used = new Set();
  const unknown = new Set();
  const keyRegex = /^\s*['"]?([a-z_][a-z_0-9]*)['"]?\s*:\s*\[/gm;
  let m;
  while ((m = keyRegex.exec(src))) {
    const key = m[1];
    if (CANONICAL.includes(key)) {
      used.add(key);
    } else {
      unknown.add(key);
    }
  }

  if (unknown.size > 0) {
    for (const key of unknown) {
      console.error(
        `  ✗ ${feature}: unknown persona "${key}" in layouts.ts — typo or stale Persona type?`,
      );
      errors++;
    }
  }

  const missing = CANONICAL.filter((p) => !used.has(p));
  if (missing.length === 0) {
    console.log(`  ✓ ${feature}: all ${CANONICAL.length} personas covered`);
  } else {
    console.log(
      `  ⚠ ${feature}: missing layouts for [${missing.join(', ')}] — falls back to owner-superset at runtime`,
    );
    warnings += missing.length;
  }
}

console.log();
if (featuresWithLayouts === 0) {
  console.log(
    'No Pattern B feature layouts found yet — that\'s expected during Phase 0.',
  );
  process.exit(0);
}
if (errors > 0) {
  console.error(`FAIL: ${errors} error(s) across ${featuresWithLayouts} feature(s)`);
  process.exit(1);
}
if (warnings > 0) {
  console.log(
    `OK with ${warnings} warning(s) — missing-persona layouts fall back to the owner-superset.`,
  );
} else {
  console.log(
    `OK — every Pattern B feature has a layout entry for every persona.`,
  );
}
process.exit(0);
