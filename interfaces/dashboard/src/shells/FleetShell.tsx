/**
 * FleetShell — chrome for the Fleet persona.
 *
 * The frame is AppShell. What belongs to Fleet is the topbar hero (vehicle status at a glance), and this
 * file existing separately is what lets a Fleet-only change land
 * without touching the other five shells. That seam is its whole job —
 * everything else was duplication, and AppShell holds it now.
 */
import AppShell from './AppShell';
import FleetHero from './heroes/FleetHero';

export default function FleetShell() {
  return <AppShell hero={<FleetHero />} />;
}
