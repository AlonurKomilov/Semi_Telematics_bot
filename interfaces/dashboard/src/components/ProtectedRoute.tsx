import { Navigate } from 'react-router-dom';
import { useRoleView } from '../context/RoleViewContext';
import { CardSkeleton } from './shell';
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
  const { viewHasAny, viewPermsReady } = useRoleView();

  // "Not loaded yet" is NOT "not allowed".  On a preview view the role
  // permission sets arrive from a second request, and viewPerms stays
  // deliberately EMPTY until it settles (so the nav doesn't flash the
  // previewer's own full menu).  Redirecting during that window sent
  // every deep link, bookmark and hard refresh of a gated route to the
  // overview — and because the redirect used ``replace``, the original
  // URL was destroyed, so it never came back once permissions landed.
  // Wait for an authoritative answer instead.
  if (!viewPermsReady) return <CardSkeleton message="Checking access…" />;

  const flags = Array.isArray(permission) ? permission : [permission];
  if (!viewHasAny(...flags)) return <Navigate to="/" replace />;
  return children;
}
