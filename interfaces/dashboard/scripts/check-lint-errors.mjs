#!/usr/bin/env node
/**
 * Fail the build on ESLint ERRORS. Warnings are allowed through.
 *
 * ESLint was never part of CI — `.github/workflows/deploy.yml` runs
 * `npm test` and `npm run build`, and neither invokes it. So rules that
 * catch real crashes were only ever seen by whoever happened to run
 * `npx eslint` by hand.
 *
 * That is not hypothetical. `ServiceTaskDetail` called `useAssemblies()`
 * below three early returns — a genuine hook (two `useQuery`s), so on a
 * COLD cache the first render bailed before it and the second called it,
 * and React threw "Rendered more hooks than during the previous render".
 * The page white-screened on hard-refresh and worked fine when you
 * arrived from another page, which is how it survived. `rules-of-hooks`
 * had been pointing at it the whole time, as the only error in the repo,
 * and nothing was looking.
 *
 * WARNINGS stay allowed on purpose. There are ~220 — the `title=` → <Tip>
 * migration and dependency-array notes — and failing on those would mean
 * either a red build for months or a mass `eslint-disable`, which is how
 * a rule gets switched off wholesale. Errors are the ones that mean
 * "this is broken", and there are currently zero.
 */
import { ESLint } from 'eslint';

const eslint = new ESLint({ cwd: new URL('..', import.meta.url).pathname });
const results = await eslint.lintFiles(['src']);

const offenders = results.filter((r) => r.errorCount > 0);
const errors = offenders.reduce((n, r) => n + r.errorCount, 0);

if (errors === 0) {
  const warnings = results.reduce((n, r) => n + r.warningCount, 0);
  console.log(`✓ eslint: 0 errors (${warnings} warnings, allowed)`);
  process.exit(0);
}

// Name the file and the rule — a count alone sends the reader back to
// re-run the tool to find out what it meant.
console.error(`\n✗ eslint: ${errors} error${errors === 1 ? '' : 's'}\n`);
for (const r of offenders) {
  const rel = r.filePath.replace(`${process.cwd()}/`, '');
  for (const m of r.messages.filter((m) => m.severity === 2)) {
    console.error(`  ${rel}:${m.line}:${m.column}`);
    console.error(`    ${m.message}  [${m.ruleId}]`);
  }
}
console.error('\nWarnings are allowed; errors are not. Fix the above, or\n'
  + 'add an eslint-disable WITH a reason if it is genuinely correct.\n');
process.exit(1);
