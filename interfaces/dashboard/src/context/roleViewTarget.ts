/** Permission row identity only. Grant values always come from the server. */
export const ROLE_TIER_LABELS: Record<string, { senior: string; base: string }> = {
  owner: { senior: 'Primary', base: 'Co-owner' },
  admin: { senior: 'Full admin', base: 'Standard admin' },
  ...Object.fromEntries(['fleet', 'safety', 'dispatcher', 'hr', 'accounting', 'recruiter']
    .map(role => [role, { senior: 'Manager', base: 'Employee' }])),
};
export function permissionRow(role: string, senior: boolean): string {
  if (role === 'owner') return senior ? 'owner' : 'owner__co';
  return senior && ROLE_TIER_LABELS[role] ? `${role}__manager` : role;
}
export function isPermissionRow(role: string, key: unknown): key is string {
  return typeof key === 'string' && (key === permissionRow(role, false) || key === permissionRow(role, true));
}
export function ownPermissionRow(user: { role?: string; is_manager?: boolean; is_primary_owner?: boolean }): string {
  const role = user.role ?? '';
  return permissionRow(role, role === 'owner' ? !!user.is_primary_owner : !!user.is_manager);
}
export function tierLabel(role: string, key: string): string {
  const labels = ROLE_TIER_LABELS[role];
  return labels ? (key === permissionRow(role, true) ? labels.senior : labels.base) : '';
}
