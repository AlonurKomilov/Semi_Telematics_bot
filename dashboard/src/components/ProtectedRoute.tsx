import { Navigate } from 'react-router-dom';
import { usePermissions } from '../hooks/usePermissions';
import type { ReactNode } from 'react';

interface ProtectedRouteProps {
  permission: string | string[];
  children: ReactNode;
}

export default function ProtectedRoute({ permission, children }: ProtectedRouteProps) {
  const { hasAny } = usePermissions();
  const flags = Array.isArray(permission) ? permission : [permission];
  if (!hasAny(...flags)) return <Navigate to="/" replace />;
  return children;
}
