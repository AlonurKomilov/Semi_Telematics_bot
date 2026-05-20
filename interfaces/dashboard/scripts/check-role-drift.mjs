#!/usr/bin/env node
/**
 * Drift guard for hardcoded role literals.
 *
 * Phase 6 of the role-shell migration centralised every persona-aware
 * UI decision into `useShellConfig`.  This script enforces that
 * convention going forward: it scans the dashboard source for stray
 * `role === '<persona>'` (and `realRole === …`, `activeView === …`)
 * comparisons and fails if any appear outside the hook itself, the
 * RoleViewContext, or the small allow-list of files where the
 * comparison is legitimate (form state, message author, the row of a
 * user being edited, schedule target filter, etc.).
 *
 * Run as part of CI:
 *
 *     node scripts/check-role-drift.mjs
 *
 * Exit code 0 = clean, 1 = drift found.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');
const SRC = join(ROOT, 'src');

// Persona keys that must not be hardcoded in `=== '<key>'` comparisons
// outside the hook + context.  Mirrors VIEW_LABELS in RoleViewContext.
const ROLE_KEYS = ['owner', 'admin', 'fleet', 'safety', 'dispatcher', 'driver'];

// Files where a `role === '<persona>'` comparison is a legitimate,
// non-shell concern and should be ignored by the drift check:
//   • useShellConfig    — the hook itself owns these comparisons
//   • RoleViewContext   — owns the view-switching machinery
//   • Sidebar           — filters nav by the auth user's real role
//   • admin/Invites     — `role` is the form state of the invite being
//                         drafted, not the current user's persona
//   • admin/Users       — `selected.role` is the row of the user being
//                         edited; `roleFilter` is a chip filter
//   • admin/WorkHours   — `target_role` is a field on schedule rows
//   • ai/Chat           — `msg.role === 'user'` is the chat message
//                         author (user vs. model), not a persona
const ALLOWLIST = new Set([
  'src/hooks/useShellConfig.ts',
  'src/context/RoleViewContext.tsx',
  'src/components/Sidebar.tsx',
  'src/pages/admin/Invites.tsx',
  'src/pages/admin/Users.tsx',
  'src/pages/admin/WorkHours.tsx',
  'src/pages/ai/Chat.tsx',
]);

const ROLE_PATTERN = new RegExp(
  `\\b(?:role|realRole|activeView)\\s*===\\s*['"](?:${ROLE_KEYS.join('|')})['"]`,
);

function walk(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry);
    const s = statSync(p);
    if (s.isDirectory()) walk(p, out);
    else if (entry.endsWith('.ts') || entry.endsWith('.tsx')) out.push(p);
  }
  return out;
}

const violations = [];
for (const file of walk(SRC)) {
  const rel = relative(ROOT, file);
  if (ALLOWLIST.has(rel)) continue;
  const lines = readFileSync(file, 'utf8').split('\n');
  lines.forEach((line, i) => {
    if (ROLE_PATTERN.test(line)) {
      violations.push({ file: rel, line: i + 1, text: line.trim() });
    }
  });
}

if (violations.length === 0) {
  console.log('✓ Role-drift check passed — no stray persona literals.');
  process.exit(0);
}

console.error('✗ Role-drift check failed.');
console.error('  Move these comparisons into hooks/useShellConfig.ts,');
console.error('  or add the file to ALLOWLIST in scripts/check-role-drift.mjs');
console.error('  if the comparison is genuinely not a persona check.\n');
for (const v of violations) {
  console.error(`  ${v.file}:${v.line}  ${v.text}`);
}
process.exit(1);
