/**
 * DispatchShell — chrome for the Dispatch persona.
 *
 * The frame is AppShell. What belongs to Dispatch is the topbar hero (load and run context), and this
 * file existing separately is what lets a Dispatch-only change land
 * without touching the other five shells. That seam is its whole job —
 * everything else was duplication, and AppShell holds it now.
 */
import AppShell from './AppShell';
import DispatchHero from './heroes/DispatchHero';

export default function DispatchShell() {
  return <AppShell hero={<DispatchHero />} />;
}
