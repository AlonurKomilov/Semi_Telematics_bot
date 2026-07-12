import { useAuth } from '../context/AuthContext';
import { useRoleView } from '../context/RoleViewContext';
import type { Persona } from '../features/_lib/types';

/**
 * Single source of truth for role-aware UI decisions.
 *
 * Two distinct signals live here on purpose:
 *
 *   • Real role  (`realRole` / `is{Driver,Owner,Admin,OwnerOrAdmin}`)
 *     Sourced from the authenticated user.  Use for gates that must NOT
 *     change when an Owner/Admin previews a different persona — e.g. the
 *     Telegram-Bot section on Settings, the Owner-only edit affordance on
 *     Geofences, the DriverOverview branch.  A Driver previewing the
 *     dashboard never happens (Driver isn't a switchable role and uses
 *     the Mini App), but the distinction still matters for Owners/Admins
 *     who can flip into Fleet/Safety/Dispatch views.
 *
 *   • Active view (`activeView` / `is*View` / `briefingLabel` / `chatSubject`)
 *     Sourced from RoleViewContext.  Use for persona-tuned copy and
 *     widgets that SHOULD switch when previewing — an Owner inspecting
 *     "as Safety" should read "Safety Briefing", not "Operations
 *     Briefing".
 *
 * Anything that would otherwise be a `role === 'X'` literal in a page or
 * component belongs here.  The drift test in
 * `scripts/check-role-drift.mjs` enforces that — strays outside this
 * file (and a small allow-list) fail CI.
 */
export function useShellConfig() {
  const { user } = useAuth();
  const { activeView } = useRoleView();

  const realRole = user?.role;
  const isDriver = realRole === 'driver';
  const isOwner = realRole === 'owner';
  const isAdmin = realRole === 'admin';
  const isOwnerOrAdmin = isOwner || isAdmin;

  const isFleetView = activeView === 'fleet';
  const isDispatchView = activeView === 'dispatcher';
  const isSafetyView = activeView === 'safety';
  const isDriverView = activeView === 'driver';
  const isOwnerView = activeView === 'owner';
  const isAdminView = activeView === 'admin';
  const isHRView = activeView === 'hr';
  const isAccountingView = activeView === 'accounting';

  // Persona-tuned button label for the AI assistant's primary briefing.
  // Every persona has one — the briefing content itself is derived from the
  // role's effective permissions on the backend (BRIEFING_TOPICS), so this
  // is purely the display name.
  const briefingLabel =
    activeView === 'driver' ? 'My Truck Briefing' :
    activeView === 'dispatcher' ? 'Dispatch Briefing' :
    activeView === 'safety' ? 'Safety Briefing' :
    activeView === 'fleet' ? 'Fleet Briefing' :
    activeView === 'recruiter' ? 'Hiring Briefing' :
    activeView === 'hr' ? 'People Briefing' :
    activeView === 'accounting' ? 'Cost Briefing' :
    'Operations Briefing';

  // Persona-tuned noun used inside the chat placeholder / loading copy.
  const chatSubject =
    activeView === 'driver' ? 'your truck' :
    activeView === 'dispatcher' ? "today's dispatch" :
    activeView === 'safety' ? 'safety & coaching' :
    activeView === 'fleet' ? 'your fleet' :
    'your operation';

  // Persona-neutral collective noun used by AISummaryCard headlines —
  // "fleet" reads wrong for safety/dispatch, "vehicles" is neutral.
  const aiSubjectAll =
    activeView === 'dispatcher' ? 'trucks dispatched' :
    activeView === 'safety' ? 'trucks under watch' :
    'vehicles';

  // Hint shown when nothing critical is open; nudges the persona toward
  // their natural slack-time activity.
  const aiRestingHint =
    activeView === 'safety' ? 'Use this time to review scorecards or work the coaching backlog.' :
    activeView === 'dispatcher' ? 'Use this time to plan upcoming routes.' :
    'Use this time to review scorecards or coaching backlogs.';

  // ``persona`` is the canonical Persona-typed handle on the active
  // view — Pattern B layout maps key against this.  Role == persona 1:1
  // (a "manager" is a per-user tier on the base role, NOT a distinct
  // persona — see is_manager), so a direct cast is correct.
  const persona: Persona = (activeView as Persona) || 'owner';

  return {
    realRole,
    isDriver,
    isOwner,
    isAdmin,
    isOwnerOrAdmin,
    activeView,
    persona,
    isFleetView,
    isDispatchView,
    isSafetyView,
    isDriverView,
    isOwnerView,
    isAdminView,
    isHRView,
    isAccountingView,
    briefingLabel,
    chatSubject,
    aiSubjectAll,
    aiRestingHint,
  };
}
