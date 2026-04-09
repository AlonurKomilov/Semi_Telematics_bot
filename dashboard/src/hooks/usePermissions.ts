import { useAuth } from '../context/AuthContext';
import type { Permissions } from '../types';

interface UsePermissionsReturn {
  has: (flag: string) => boolean;
  hasAny: (...flags: string[]) => boolean;
  role: string | undefined;
  permissions: Permissions;
}

export function usePermissions(): UsePermissionsReturn {
  const { user } = useAuth();
  const perms = (user?.permissions || {}) as Permissions;
  const has = (flag: string) => !!perms[flag];
  const hasAny = (...flags: string[]) => flags.some((f) => !!perms[f]);
  return { has, hasAny, role: user?.role, permissions: perms };
}
