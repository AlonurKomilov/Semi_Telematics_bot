import { Navigate } from 'react-router-dom';
import { useRoleView } from '../context/RoleViewContext';
import type { ReactNode } from 'react';

interface ProtectedRouteProps {
  permission: string | string[];
  children: ReactNode;
}

export default function ProtectedRoute({ permission, children }: ProtectedRouteProps) {
  // Gate on the ACTIVE VIEW's permission (viewHasAny), not the logged-in
  // user's own.  Without this, an Owner/Admin previewing another persona
  // could reach a route that persona can't (the sidebar already hides the
  // link via viewHasAny, but a typed URL / back-button would slip
  // through) — the preview would be unfaithful.  viewHasAny falls back to
  // the real user's permissions when not previewing, so regular users and
  // an owner on their own view are unaffected.  The backend still enforces
  // every endpoint independently.
  const { viewHasAny } = useRoleView();
  const flags = Array.isArray(permission) ? permission : [permission];
  if (!viewHasAny(...flags)) return <Navigate to="/" replace />;
  return children;
}
