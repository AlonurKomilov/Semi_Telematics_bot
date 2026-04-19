import { useCallback, useMemo } from 'react';
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
  const perms = useMemo(() => (user?.permissions || {}) as Permissions, [user]);
  const has = useCallback((flag: string) => !!perms[flag as keyof Permissions], [perms]);
  const hasAny = useCallback((...flags: string[]) => flags.some((f) => !!perms[f as keyof Permissions]), [perms]);
  return { has, hasAny, role: user?.role, permissions: perms };
}
