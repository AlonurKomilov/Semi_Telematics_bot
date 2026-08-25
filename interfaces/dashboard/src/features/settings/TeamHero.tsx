/**
 * TeamHero — feature-scoped topbar chips shown while the operator is
 * ON /team (see shells/heroes/ShellHero).  Team composition at a
 * glance:
 *
 *   Active · Pending sign-in (when > 0) · one chip per role in use
 *
 * "Pending sign-in" is the actionable signal (provisioned members who
 * still need onboarding follow-up) so it carries the info tone.  Role
 * chips are neutral and only render for roles that exist on the
 * account, keeping the strip short.  Reads the same react-query cache
 * as the page — counts can't drift.
 */
import HeroChip, { HERO_STRIP } from '../../shells/heroes/HeroChip';
import { ROLE_LABEL } from '../../components/RoleBadge';
import { useTeamMembersQuery, memberLifecycleOf } from '../../hooks/useTeamMembers';

export default function TeamHero() {
  const { data, isLoading, isError } = useTeamMembersQuery();
  if (isError) return <div className="flex-1 min-w-0" />;
  if (isLoading || !data) {
    return (
      <div className="flex-1 min-w-0 flex items-center px-2 gap-1.5">
        <span className="text-2xs text-muted-foreground/60">Loading team…</span>
      </div>
    );
  }
  const users = data.users;
  const active = users.filter(u => memberLifecycleOf(u) === 'active').length;
  const pending = users.filter(u => memberLifecycleOf(u) === 'pending').length;
  const roleCounts: Record<string, number> = {};
  for (const u of users) roleCounts[u.role] = (roleCounts[u.role] ?? 0) + 1;
  return (
    <div className={HERO_STRIP}>
      <HeroChip label="Active" value={active} tone="positive" />
      {pending > 0 && (
        <HeroChip
          label="Pending sign-in"
          value={pending}
          tone="info"
          title="Provisioned members who haven't signed in yet (no Telegram link or password)"
        />
      )}
      {Object.entries(ROLE_LABEL).map(([key, label]) =>
        (roleCounts[key] ?? 0) > 0 ? (
          <HeroChip key={key} label={label} value={roleCounts[key]} tone="neutral" />
        ) : null,
      )}
    </div>
  );
}
