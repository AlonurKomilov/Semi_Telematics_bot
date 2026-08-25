/**
 * RoleBadge — canonical role pill used everywhere a user's role is shown.
 *
 * Why a shared component (not local helpers per page): we had two
 * implementations — one on the Team Management Members tab, one on the
 * Invites tab — and they used different colours for the *same* role.
 * Admin was blue on Members and red on Invites; Driver was gray vs.
 * cyan; Safety was amber vs. orange.  That's exactly the "same column,
 * same render" rule design.md §4 calls out — a role's colour is its
 * identity, it cannot drift between pages.
 *
 * Implementation: maps each role to a semantic ``Tone`` (the four
 * status-token hues) via ``roleTone``.  Tone collisions (Owner /
 * Admin / Dispatcher all map to ``info``) are intentional — the
 * label disambiguates while the colour communicates the *category*
 * (management vs. operations vs. compliance vs. rank-and-file).
 * Adding a new role?  Update ``ROLE_LABEL`` + ``ROLE_TONE`` here
 * once and every page picks it up — never invent a colour at the
 * call-site (design.md §2 hard rule).
 */
import { type Tone } from '../lib/status';
import { Badge } from '@/components/ui/badge';

/** Display label for each role.  Single source of truth — both the
 *  Team Management members table and the Invites table read this. */
export const ROLE_LABEL: Record<string, string> = {
  owner:      'Owner',
  admin:      'Admin',
  fleet:      'Fleet',
  safety:     'Safety',
  dispatcher: 'Dispatcher',
  driver:     'Driver',
  hr:         'HR',
  accounting: 'Accounting',
  recruiter:  'Recruiter',
};

/** Operator-assignable roles, in display order — every role EXCEPT owner
 *  (owner transfers, it is never assigned or invited).  Single source for
 *  the Change-Role grid and the Invite dropdown, so adding a role to
 *  ROLE_LABEL above surfaces it in both pickers automatically instead of
 *  drifting per-file.  Target-role lists that also need 'all' / 'owner'
 *  (Working Hours) layer those on top of this. */
export const ASSIGNABLE_ROLES = [
  'admin', 'fleet', 'safety', 'dispatcher',
  'hr', 'accounting', 'recruiter', 'driver',
] as const;

/** Role → semantic tone.  Categories:
 *    info    = management / system tier (owner, admin, dispatcher)
 *    ok      = fleet operations (fleet)
 *    warn    = compliance / personnel focus (safety, hr, recruiter)
 *    neutral = rank-and-file / back-office (driver, accounting)
 *  Tone collisions are intentional — the label carries the precise
 *  role; the colour groups them by function so a fleet ops table reads
 *  as four legible categories rather than eight near-identical hues. */
const ROLE_TONE: Record<string, Tone> = {
  owner:      'info',
  admin:      'info',
  fleet:      'ok',
  safety:     'warn',
  dispatcher: 'info',
  driver:     'neutral',
  hr:         'warn',
  accounting: 'neutral',
  recruiter:  'warn',
};

/** Resolve a role string to its semantic tone.  Unknown roles fall
 *  back to ``neutral`` so a typo reads as "no signal" rather than as
 *  the wrong category. */
export function roleTone(role: string | null | undefined): Tone {
  if (!role) return 'neutral';
  return ROLE_TONE[role.toLowerCase().trim()] ?? 'neutral';
}

/** The canonical role pill.  Always identical wherever it's rendered.
 *
 *  ``label`` overrides the display text while keeping the role's tone —
 *  used to show the EFFECTIVE tier ("Full admin", "Co-owner",
 *  "Recruiter Manager") instead of the bare base role.  Omit it and the
 *  pill falls back to the plain role label. */
export default function RoleBadge({ role, label }: { role: string; label?: string }) {
  const text = label ?? (ROLE_LABEL[role.toLowerCase()] ?? role);
  return (
    <Badge tone={roleTone(role)}>
      {text}
    </Badge>
  );
}
