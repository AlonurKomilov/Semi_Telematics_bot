/**
 * AccountingShell — chrome for the Accounting persona.
 *
 * The frame is AppShell. What belongs to Accounting is nothing yet — no hero has been designed for it, and this
 * file existing separately is what lets a Accounting-only change land
 * without touching the other five shells. That seam is its whole job —
 * everything else was duplication, and AppShell holds it now.
 */
import AppShell from './AppShell';

export default function AccountingShell() {
  return <AppShell />;
}
