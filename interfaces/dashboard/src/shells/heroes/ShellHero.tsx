/**
 * ShellHero — the topbar's middle zone, made route-aware.
 *
 * When the current route has a feature hero registered (see
 * featureHeroes.tsx) it wins the slot: the operator is looking AT
 * that feature, so its live counts are the most relevant context.
 * Otherwise the persona's cross-cutting hero (FleetHero /
 * DispatchHero / SafetyHero) renders via ``fallback`` — and shells
 * with no persona hero (DefaultShell, HR, Accounting) fall back to
 * the plain flex spacer so the topbar layout is unchanged.
 */
import type React from 'react';
import { useLocation } from 'react-router-dom';
import { matchFeatureHero } from './featureHeroes';

export default function ShellHero({ fallback }: { fallback?: React.ReactNode }) {
  const { pathname } = useLocation();
  const Hero = matchFeatureHero(pathname);
  if (Hero) return <Hero />;
  if (fallback) return <>{fallback}</>;
  return <div className="flex-1 min-w-0" />;
}
