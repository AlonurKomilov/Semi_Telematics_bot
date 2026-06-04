/**
 * Tests for PageLayoutHost — the Pattern B page renderer.
 *
 * Coverage:
 *  1. Renders the active persona's layout
 *  2. Falls back to ``owner`` when the persona has no entry
 *  3. Falls back to ``admin`` when ``owner`` is also missing
 *  4. Last-resort fallback to every section when none of persona/owner/admin matches
 *  5. Silently skips layout entries that reference unknown section ids
 *  6. SectionErrorBoundary isolates a thrown error to one section
 *  7. Section props are forwarded to each rendered section
 */
import { Suspense, lazy } from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { PageLayoutHost } from './PageLayoutHost';
import type { LayoutMap, SectionRegistry } from './types';

// Test environment doesn't auto-unmount between tests; call cleanup
// after each so getByTestId doesn't find elements from prior renders.
afterEach(cleanup);

// ── Test fixtures ─────────────────────────────────────────────────
//
// Each test fixture mocks useShellConfig differently — Vitest's
// vi.mock is hoisted, so we set the return value per-test via the
// mock object below.

const mockUseShellConfig = vi.fn();
vi.mock('../../hooks/useShellConfig', () => ({
  useShellConfig: () => mockUseShellConfig(),
}));

// Tiny eager sections — no real lazy chunks needed for these tests.
// lazy() wraps a function returning { default: Component }, which
// matches the SectionDef.Component type.
const makeSection = (label: string) =>
  lazy(async () => ({
    default: ({ greeting }: { greeting: string }) => (
      <div data-testid={`section-${label}`}>{greeting}-{label}</div>
    ),
  }));

const REGISTRY: SectionRegistry<{ greeting: string }> = {
  alpha: { Component: makeSection('alpha'), label: 'Alpha' },
  beta: { Component: makeSection('beta'), label: 'Beta' },
  gamma: { Component: makeSection('gamma'), label: 'Gamma' },
};

const LAYOUTS: LayoutMap = {
  fleet: ['alpha', 'beta'],
  owner: ['alpha', 'beta', 'gamma'],
};

function setPersona(persona: string) {
  mockUseShellConfig.mockReturnValue({ persona });
}

// ── Tests ─────────────────────────────────────────────────────────

describe('PageLayoutHost layout resolution', () => {
  it('renders the active persona layout in order', async () => {
    setPersona('fleet');
    render(
      <PageLayoutHost
        registry={REGISTRY}
        layouts={LAYOUTS}
        sectionProps={{ greeting: 'hi' }}
        sectionFallback={<div data-testid="loading" />}
      />,
    );
    expect(await screen.findByTestId('section-alpha')).toBeTruthy();
    expect(await screen.findByTestId('section-beta')).toBeTruthy();
    // Fleet layout omits gamma — must NOT render.
    expect(screen.queryByTestId('section-gamma')).toBeNull();
  });

  it('falls back to owner when persona has no entry', async () => {
    setPersona('safety'); // no entry in LAYOUTS
    render(
      <PageLayoutHost
        registry={REGISTRY}
        layouts={LAYOUTS}
        sectionProps={{ greeting: 'hi' }}
        sectionFallback={<div data-testid="loading" />}
      />,
    );
    // owner layout = [alpha, beta, gamma]
    expect(await screen.findByTestId('section-alpha')).toBeTruthy();
    expect(await screen.findByTestId('section-beta')).toBeTruthy();
    expect(await screen.findByTestId('section-gamma')).toBeTruthy();
  });

  it('falls back to admin when both persona and owner are missing', async () => {
    setPersona('hr');
    render(
      <PageLayoutHost
        registry={REGISTRY}
        layouts={{ admin: ['gamma'] }}
        sectionProps={{ greeting: 'hi' }}
        sectionFallback={<div data-testid="loading" />}
      />,
    );
    expect(await screen.findByTestId('section-gamma')).toBeTruthy();
    expect(screen.queryByTestId('section-alpha')).toBeNull();
    expect(screen.queryByTestId('section-beta')).toBeNull();
  });

  it('last-resort fallback: renders every registry section when none of persona/owner/admin matches', async () => {
    setPersona('hr');
    render(
      <PageLayoutHost
        registry={REGISTRY}
        layouts={{}}
        sectionProps={{ greeting: 'hi' }}
        sectionFallback={<div data-testid="loading" />}
      />,
    );
    expect(await screen.findByTestId('section-alpha')).toBeTruthy();
    expect(await screen.findByTestId('section-beta')).toBeTruthy();
    expect(await screen.findByTestId('section-gamma')).toBeTruthy();
  });

  it('silently skips layout entries that reference unknown section ids', async () => {
    setPersona('fleet');
    render(
      <PageLayoutHost
        registry={REGISTRY}
        layouts={{ fleet: ['alpha', 'nope', 'beta'] }}
        sectionProps={{ greeting: 'hi' }}
        sectionFallback={<div data-testid="loading" />}
      />,
    );
    expect(await screen.findByTestId('section-alpha')).toBeTruthy();
    expect(await screen.findByTestId('section-beta')).toBeTruthy();
    // No crash; unknown id is silently skipped.
  });

  it('forwards sectionProps to every rendered section', async () => {
    setPersona('fleet');
    render(
      <PageLayoutHost
        registry={REGISTRY}
        layouts={{ fleet: ['alpha'] }}
        sectionProps={{ greeting: 'hello' }}
        sectionFallback={<div data-testid="loading" />}
      />,
    );
    const node = await screen.findByTestId('section-alpha');
    expect(node.textContent).toBe('hello-alpha');
  });
});

describe('PageLayoutHost HR/Accounting fallback', () => {
  it('an HR persona with no layout entry falls back to the owner layout', async () => {
    setPersona('hr');
    render(
      <PageLayoutHost
        registry={REGISTRY}
        layouts={LAYOUTS}
        sectionProps={{ greeting: 'hi' }}
        sectionFallback={<div data-testid="loading" />}
      />,
    );
    // owner = [alpha, beta, gamma]
    expect(await screen.findByTestId('section-alpha')).toBeTruthy();
    expect(await screen.findByTestId('section-beta')).toBeTruthy();
    expect(await screen.findByTestId('section-gamma')).toBeTruthy();
  });

  it('an Accounting persona with no layout entry falls back to the owner layout', async () => {
    setPersona('accounting');
    render(
      <PageLayoutHost
        registry={REGISTRY}
        layouts={LAYOUTS}
        sectionProps={{ greeting: 'hi' }}
        sectionFallback={<div data-testid="loading" />}
      />,
    );
    expect(await screen.findByTestId('section-alpha')).toBeTruthy();
    expect(await screen.findByTestId('section-beta')).toBeTruthy();
    expect(await screen.findByTestId('section-gamma')).toBeTruthy();
  });
});

describe('PageLayoutHost SectionErrorBoundary isolation', () => {
  it('isolates a thrown error to the failing section — siblings still render', async () => {
    setPersona('fleet');
    // Suppress React's own error log for the rendered error during this test.
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    const Throwing = lazy(async () => ({
      default: () => {
        throw new Error('boom');
      },
    }));
    const registry: SectionRegistry<{ greeting: string }> = {
      alpha: { Component: makeSection('alpha'), label: 'Alpha' },
      throwing: { Component: Throwing, label: 'Throwing One' },
      gamma: { Component: makeSection('gamma'), label: 'Gamma' },
    };

    render(
      <Suspense fallback={null}>
        <PageLayoutHost
          registry={registry}
          layouts={{ fleet: ['alpha', 'throwing', 'gamma'] }}
          sectionProps={{ greeting: 'hi' }}
          sectionFallback={<div data-testid="loading" />}
        />
      </Suspense>,
    );

    // Siblings still mount.
    expect(await screen.findByTestId('section-alpha')).toBeTruthy();
    expect(await screen.findByTestId('section-gamma')).toBeTruthy();
    // Failing section surfaces its boundary fallback containing the label.
    expect(await screen.findByText(/Throwing One/)).toBeTruthy();
    expect(await screen.findByText(/boom/)).toBeTruthy();

    errorSpy.mockRestore();
  });

  it('isolates a lazy-chunk load failure (promise rejection) — siblings still render', async () => {
    // Verifier finding: original test covered only render-time
    // throws.  A real Vite chunk that 404s after a deploy throws
    // ASYNCHRONOUSLY during Suspense resolution.  React forwards
    // that to the nearest error boundary OUTSIDE Suspense — which
    // is exactly where SectionErrorBoundary sits in PageLayoutHost.
    // This test verifies that propagation chain.
    setPersona('fleet');
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    const ChunkFails = lazy(() =>
      Promise.reject(new Error('chunk 404')) as Promise<{
        default: React.ComponentType<{ greeting: string }>;
      }>,
    );
    const registry: SectionRegistry<{ greeting: string }> = {
      alpha: { Component: makeSection('alpha'), label: 'Alpha' },
      brokenChunk: { Component: ChunkFails, label: 'Broken Chunk' },
      gamma: { Component: makeSection('gamma'), label: 'Gamma' },
    };

    render(
      <PageLayoutHost
        registry={registry}
        layouts={{ fleet: ['alpha', 'brokenChunk', 'gamma'] }}
        sectionProps={{ greeting: 'hi' }}
        sectionFallback={<div data-testid="loading" />}
      />,
    );

    // Siblings still render — the rejected chunk's error is
    // contained to its own SectionErrorBoundary.
    expect(await screen.findByTestId('section-alpha')).toBeTruthy();
    expect(await screen.findByTestId('section-gamma')).toBeTruthy();
    // Failing chunk's boundary fallback shows the section label.
    expect(await screen.findByText(/Broken Chunk/)).toBeTruthy();

    errorSpy.mockRestore();
  });
});
