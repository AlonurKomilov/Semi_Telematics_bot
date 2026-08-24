/**
 * HRShell — chrome for the HR persona.
 *
 * The frame is AppShell. What belongs to HR is nothing yet — the natural hero chips (open coaching, driver-doc
 * expiries, CSA inspections this week) are product work that has not
 * been designed, and this
 * file existing separately is what lets a HR-only change land
 * without touching the other five shells. That seam is its whole job —
 * everything else was duplication, and AppShell holds it now.
 */
import AppShell from './AppShell';

export default function HRShell() {
  return <AppShell />;
}
