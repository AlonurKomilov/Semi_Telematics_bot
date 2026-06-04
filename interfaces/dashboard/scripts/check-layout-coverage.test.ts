/**
 * Integration tests for check-layout-coverage.mjs.
 *
 * Stages a temporary dashboard root with synthetic features and runs
 * the audit script.  Covers: passing the canonical-persona check,
 * detecting unknown persona keys (ERROR exit code 1), reporting
 * missing-persona warnings (still exit 0 since runtime falls back),
 * and the no-features-yet bootstrap case.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, cpSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REAL_SCRIPT = resolve(__dirname, 'check-layout-coverage.mjs');

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

// Canonical Persona union — matches features/_lib/types.ts.  Stage
// uses this when building the _lib/types.ts fixture.
const PERSONA_LITERAL = `
export type Persona =
  | 'owner'
  | 'admin'
  | 'fleet'
  | 'dispatcher'
  | 'safety'
  | 'driver'
  | 'hr'
  | 'accounting';
`;

interface Stage {
  /** Optional override of the Persona type body (default = full canonical). */
  personaType?: string;
  /** Map of feature name → layouts.ts file body.  Pass null to OMIT layouts.ts. */
  features?: Record<string, string | null>;
}

function stage(spec: Stage = {}): string {
  const root = mkdtempSync(join(tmpdir(), 'layoutcov-test-'));
  tmpRoots.push(root);
  mkdirSync(join(root, 'scripts'), { recursive: true });
  cpSync(REAL_SCRIPT, join(root, 'scripts/check-layout-coverage.mjs'));
  const featuresDir = join(root, 'src/features');
  mkdirSync(join(featuresDir, '_lib'), { recursive: true });
  writeFileSync(
    join(featuresDir, '_lib/types.ts'),
    spec.personaType ?? PERSONA_LITERAL,
    'utf8',
  );
  if (spec.features) {
    for (const [name, body] of Object.entries(spec.features)) {
      const dir = join(featuresDir, name);
      mkdirSync(dir, { recursive: true });
      if (body !== null) {
        writeFileSync(join(dir, 'layouts.ts'), body, 'utf8');
      }
    }
  }
  return root;
}

function runScript(root: string): RunResult {
  const r = spawnSync(
    'node',
    [join(root, 'scripts/check-layout-coverage.mjs')],
    { encoding: 'utf8' },
  );
  return {
    stdout: r.stdout || '',
    stderr: r.stderr || '',
    exitCode: r.status ?? -1,
  };
}

describe('check-layout-coverage: bootstrap cases', () => {
  it('exits 0 with helpful message when no features/ dir exists yet', () => {
    const root = mkdtempSync(join(tmpdir(), 'layoutcov-test-'));
    tmpRoots.push(root);
    mkdirSync(join(root, 'scripts'), { recursive: true });
    cpSync(REAL_SCRIPT, join(root, 'scripts/check-layout-coverage.mjs'));
    // No src/features/ at all.
    const r = runScript(root);
    expect(r.exitCode).toBe(0);
    expect(r.stdout).toContain('Pattern B not bootstrapped yet');
  });

  it('exits 0 with "no layouts" message when features dir is empty', () => {
    const root = stage({ features: {} });
    const r = runScript(root);
    expect(r.exitCode).toBe(0);
    expect(r.stdout).toContain('No Pattern B feature layouts found yet');
  });

  it('exits 0 when feature exists but has no layouts.ts', () => {
    const root = stage({ features: { foo: null } });
    const r = runScript(root);
    expect(r.exitCode).toBe(0);
    expect(r.stdout).toContain('foo: no layouts.ts');
  });
});

describe('check-layout-coverage: clean cases', () => {
  it('reports ✓ when every canonical persona has a layout entry', () => {
    const root = stage({
      features: {
        foo: `export const FOO_LAYOUTS = {
  owner: ['a'],
  admin: ['a'],
  fleet: ['a'],
  dispatcher: ['a'],
  safety: ['a'],
  driver: ['a'],
  hr: ['a'],
  accounting: ['a'],
};`,
      },
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(0);
    expect(r.stdout).toContain('all 8 personas covered');
  });
});

describe('check-layout-coverage: warning cases (missing personas)', () => {
  it('warns (does not fail) when a persona is missing', () => {
    const root = stage({
      features: {
        foo: `export const FOO_LAYOUTS = {
  owner: ['a'],
  fleet: ['a'],
};`,
      },
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(0);
    expect(r.stdout).toContain(
      'missing layouts for [admin, dispatcher, safety, driver, hr, accounting]',
    );
    expect(r.stdout).toContain('OK with');
  });
});

describe('check-layout-coverage: error cases (unknown personas)', () => {
  it('fails when a layout uses an unknown persona key', () => {
    const root = stage({
      features: {
        foo: `export const FOO_LAYOUTS = {
  owner: ['a'],
  admin: ['a'],
  fleet: ['a'],
  dispatcher: ['a'],
  safety: ['a'],
  driver: ['a'],
  hr: ['a'],
  accounting: ['a'],
  bogus: ['a'],
};`,
      },
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(1);
    expect(r.stderr).toContain('unknown persona "bogus"');
    expect(r.stderr).toContain('FAIL');
  });
});

describe('check-layout-coverage: invariants', () => {
  it('fails when Persona type cannot be parsed', () => {
    const root = stage({
      personaType: `export const NOT_A_TYPE = 'oops';`,
      features: { foo: `export const F = { owner: ['a'] };` },
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(1);
    expect(r.stderr).toContain('could not find');
  });

  it('extracts Persona type without trailing semicolon (Prettier no-semi style)', () => {
    // Verifier finding: original regex required ``;`` to terminate
    // the Persona type body, which fails silently for projects with
    // ``semi: false`` Prettier config.  Fixed to accept EOF or
    // the start of the next top-level ``export`` declaration.
    const root = stage({
      personaType: `
export type Persona =
  | 'owner'
  | 'admin'
  | 'fleet'
  | 'dispatcher'
  | 'safety'
  | 'driver'
  | 'hr'
  | 'accounting'

export const SOMETHING_ELSE = 1
`,
      features: {
        foo: `export const F = {
  owner: ['a'],
  admin: ['a'],
  fleet: ['a'],
  dispatcher: ['a'],
  safety: ['a'],
  driver: ['a'],
  hr: ['a'],
  accounting: ['a'],
};`,
      },
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(0);
    expect(r.stdout).toContain('all 8 personas covered');
  });

  it('extracts Persona type when the file ends right after the union (no terminator)', () => {
    const root = stage({
      personaType: `export type Persona =\n  | 'owner'\n  | 'admin'`,
      features: {
        foo: `export const F = { owner: ['a'], admin: ['a'] };`,
      },
    });
    const r = runScript(root);
    expect(r.exitCode).toBe(0);
    expect(r.stdout).toContain('Canonical personas (2): owner, admin');
  });
});
