/**
 * Integration tests for check-role-drift.mjs.
 *
 * Each test spawns the audit script against a TEMPORARY src/ tree
 * staged under a tmp dashboard root so the real codebase is never
 * touched.  This catches regressions in the regex patterns, the
 * comment-stripper, and the section-contract guard without coupling
 * the tests to current source files.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, cpSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REAL_DASHBOARD = resolve(__dirname, '..');
const REAL_SCRIPT = join(REAL_DASHBOARD, 'scripts/check-role-drift.mjs');

interface RunResult {
  stdout: string;
  stderr: string;
  exitCode: number;
}

let tmpRoots: string[] = [];

afterEach(() => {
  for (const r of tmpRoots) {
    try {
      rmSync(r, { recursive: true, force: true });
    } catch {
      /* ignore */
    }
  }
  tmpRoots = [];
});

/**
 * Stage a temporary dashboard root: the script + src/ + just enough
 * structure to run the audit.  Files object maps path-under-src → content.
 */
function stage(files: Record<string, string>): string {
  const root = mkdtempSync(join(tmpdir(), 'drift-test-'));
  tmpRoots.push(root);
  mkdirSync(join(root, 'scripts'), { recursive: true });
  // Copy script verbatim — the script resolves SRC relative to itself.
  cpSync(REAL_SCRIPT, join(root, 'scripts/check-role-drift.mjs'));
  for (const [rel, body] of Object.entries(files)) {
    const full = join(root, 'src', rel);
    mkdirSync(dirname(full), { recursive: true });
    writeFileSync(full, body, 'utf8');
  }
  return root;
}

function runScript(root: string): RunResult {
  const r = spawnSync(
    'node',
    [join(root, 'scripts/check-role-drift.mjs')],
    { encoding: 'utf8' },
  );
  return {
    stdout: r.stdout || '',
    stderr: r.stderr || '',
    exitCode: r.status ?? -1,
  };
}

// ── Role-literal drift (Guard 1) ──────────────────────────────────

describe('check-role-drift: role-literal guard', () => {
  it('passes on a clean tree', () => {
    const root = stage({
      'pages/Clean.tsx': `export default function C() { return null; }`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(0);
    expect(r.stdout).toContain('Role-drift check passed');
  });

  it('catches role === \'persona\' outside the allow-list', () => {
    const root = stage({
      'pages/Bad.tsx': `const x = role === 'fleet' ? 1 : 0;`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(1);
    expect(r.stderr).toContain('Role-literal drift check failed');
    expect(r.stderr).toContain('pages/Bad.tsx');
  });

  it('catches role !== \'persona\' (inequality)', () => {
    const root = stage({
      'pages/Bad.tsx': `if (role !== 'safety') doSomething();`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(1);
    expect(r.stderr).toContain('Role-literal drift check failed');
  });

  it('catches realRole and activeView the same way as role', () => {
    const root = stage({
      'pages/A.tsx': `if (realRole === 'admin') {}`,
      'pages/B.tsx': `if (activeView === 'dispatcher') {}`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(1);
    expect(r.stderr).toContain('pages/A.tsx');
    expect(r.stderr).toContain('pages/B.tsx');
  });

  it('catches new personas — hr, accounting, and recruiter', () => {
    const root = stage({
      'pages/A.tsx': `if (role === 'hr') {}`,
      'pages/B.tsx': `if (activeView === 'accounting') {}`,
      'pages/C.tsx': `if (role === 'recruiter') {}`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(1);
    expect(r.stderr).toContain('pages/A.tsx');
    expect(r.stderr).toContain('pages/B.tsx');
    expect(r.stderr).toContain('pages/C.tsx');
  });

  it('DOCUMENTED LIMITATION: persona patterns inside string literals DO trip the regex (rare in practice — acceptable trade-off)', () => {
    // The script strips comments but NOT string contents, because the
    // role-literal regex needs to see ``'fleet'`` inside ``role === 'fleet'``
    // to fire.  As a side-effect, a literal that mentions the pattern
    // (e.g. test-fixture strings) trips the guard.  Accepted because
    // (a) such constructs are vanishingly rare in real code, and
    // (b) the alternative (full string-stripping) breaks the primary
    // use case.  This test documents the limitation so future readers
    // don't try to "fix" it by stripping strings.
    const root = stage({
      'pages/Doc.tsx': `const msg = "role === 'owner' is a hint";`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(1);
    expect(r.stderr).toContain('pages/Doc.tsx');
  });

  it('does NOT trigger on persona names inside // line comments', () => {
    const root = stage({
      'pages/Doc.tsx': `// example: role === 'owner' for owner branch\nconst x = 1;`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(0);
  });

  it('does NOT trigger on persona names inside /* block comments */', () => {
    const root = stage({
      'pages/Doc.tsx': `/* role === 'fleet' is documented here */\nconst x = 1;`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(0);
  });

  it('honours the file allow-list', () => {
    const root = stage({
      // Path matches an existing allow-list entry.
      'hooks/useShellConfig.ts': `if (activeView === 'fleet') {}`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(0);
  });

  it('still catches role check when a URL ("//") appears earlier on the same line', () => {
    // Verifier finding: original regex-based stripper ate everything
    // after // including the URL's // and the role check.  Fixed by
    // string-aware stripComments() that respects string boundaries.
    const root = stage({
      'pages/Bad.tsx': `const api = "https://example.com"; if (role === 'admin') return;`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(1);
    expect(r.stderr).toContain('pages/Bad.tsx');
  });

  it('does NOT trigger on object property access (obj.role)', () => {
    // Verifier finding: original word-boundary anchor matched the
    // 'role' in 'obj.role' too.  Fixed by negative lookbehind
    // (?<![\\w.]) on the LHS variable name.
    const root = stage({
      'pages/Form.tsx': `if (selected.role === 'driver') update();`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(0);
  });

  it('does NOT trigger on object property access with realRole/activeView either', () => {
    const root = stage({
      'pages/A.tsx': `if (user.realRole === 'admin') {}`,
      'pages/B.tsx': `if (ctx.activeView === 'fleet') {}`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(0);
  });

  it('still catches bare role/realRole/activeView after the lookbehind fix', () => {
    // Sanity: the lookbehind should only exclude property access,
    // not the bare variable case that's the original use case.
    const root = stage({
      'pages/A.tsx': `const x = role === 'fleet' ? 1 : 0;`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(1);
  });
});

// ── Section-contract guard (Guard 2) ──────────────────────────────

describe('check-role-drift: section-contract guard', () => {
  it('catches useShellConfig import inside a section file', () => {
    const root = stage({
      'features/foo/sections/Bar.tsx':
        `import { useShellConfig } from '../../../hooks/useShellConfig';\nexport default function B() { return null; }`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(1);
    expect(r.stderr).toContain('Section-contract drift check failed');
    expect(r.stderr).toContain('features/foo/sections/Bar.tsx');
    expect(r.stderr).toContain('useShellConfig import');
  });

  it('does NOT trigger on useShellConfig import in a page (non-section path)', () => {
    const root = stage({
      'features/foo/Page.tsx':
        `import { useShellConfig } from '../../hooks/useShellConfig';\nexport default function P() { return null; }`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(0);
  });

  it('does NOT trigger when useShellConfig is mentioned only in a comment in a section', () => {
    const root = stage({
      'features/foo/sections/Bar.tsx':
        `// Reference: useShellConfig hook is at ../hooks\nexport default function B() { return null; }`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(0);
  });

  it('does NOT trigger when useShellConfig is mentioned only in a string in a section', () => {
    const root = stage({
      'features/foo/sections/Bar.tsx':
        `const help = "do not use useShellConfig here";\nexport default function B() { return null; }`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(0);
  });

  it('catches useShellConfig inside DEEPLY NESTED section paths', () => {
    // Verifier minor finding: original tests used a flat
    // features/foo/sections/Bar.tsx fixture; need to verify the
    // SECTION_PATH_PATTERN handles nested subdirectories like
    // features/overview/sections/_shared/Helper.tsx.
    const root = stage({
      'features/foo/sections/_shared/Deep.tsx':
        `import { useShellConfig } from '../../../../hooks/useShellConfig';\nexport default function D() { return null; }`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(1);
    expect(r.stderr).toContain('features/foo/sections/_shared/Deep.tsx');
  });
});

// ── Both guards together ──────────────────────────────────────────

describe('check-role-drift: combined', () => {
  it('reports BOTH guard failures in one run', () => {
    const root = stage({
      'pages/Bad.tsx': `if (role === 'fleet') {}`,
      'features/foo/sections/Bad.tsx':
        `import { useShellConfig } from '../../../hooks/useShellConfig';\nexport default function B() { return null; }`,
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(1);
    expect(r.stderr).toContain('Role-literal drift check failed');
    expect(r.stderr).toContain('Section-contract drift check failed');
  });
});
