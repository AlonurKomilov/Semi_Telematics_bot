/**
 * DefaultShell — chrome for the Owner / Admin persona.
 *
 * The frame is AppShell. What belongs to Owner / Admin is nothing yet: Owner and Admin see every group in the sidebar and no
 * cross-cutting hero, because they are not looking at one slice of the
 * business, and this
 * file existing separately is what lets a Owner / Admin-only change land
 * without touching the other five shells. That seam is its whole job —
 * everything else was duplication, and AppShell holds it now.
 */
import AppShell from './AppShell';

export default function DefaultShell() {
  return <AppShell />;
}
