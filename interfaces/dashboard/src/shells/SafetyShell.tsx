/**
 * SafetyShell — chrome for the Safety persona.
 *
 * The frame is AppShell. What belongs to Safety is the topbar hero (events and compliance), and this
 * file existing separately is what lets a Safety-only change land
 * without touching the other five shells. That seam is its whole job —
 * everything else was duplication, and AppShell holds it now.
 */
import AppShell from './AppShell';
import SafetyHero from './heroes/SafetyHero';

export default function SafetyShell() {
  return <AppShell hero={<SafetyHero />} />;
}
