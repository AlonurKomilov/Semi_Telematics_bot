import { useEffect } from 'react';

import { useAuth } from '../context/AuthContext';
import { usePreference } from './usePreference';
import { attachBackend, detachBackend } from './store';
import { createRemoteBackend } from './remote';

/**
 * Bridges the preferences store to the account (Phase 2).
 *
 * Renders nothing — it exists to run one effect in a place that can see
 * auth state.  Mount it INSIDE ``AuthProvider`` (see main.tsx).
 *
 * Why a component and not module-level code: syncing may only start once
 * we know WHO the user is.  Attaching earlier would either 401 or, worse,
 * pull the previous user's preferences into this session.
 *
 * Detaching on logout is equally load-bearing: without it, the next
 * user's writes would be pushed to the previous user's rows.
 */
export default function PreferencesSync() {
  const { user, loading } = useAuth();
  const { value: syncEnabled } = usePreference('prefs.syncEnabled');

  useEffect(() => {
    if (loading) return;                 // don't decide mid-boot
    if (!user || !syncEnabled) {
      detachBackend();                   // logged out, or local-only mode
      return;
    }
    let cancelled = false;
    const backend = createRemoteBackend();
    // attachBackend also does the ONE bulk read and adopts the account's
    // values for 'synced' keys.
    attachBackend(backend).catch(() => { /* offline — local values stand */ });
    return () => {
      cancelled = true;
      void cancelled;
      detachBackend();
    };
  }, [user, loading, syncEnabled]);

  return null;
}
