import { useEffect, useState } from 'react';
import { apiJSON } from '../api/client';

/**
 * Recipient-facing invite links MUST use the APEX origin from
 * /auth/config's ``signup_base_url`` — Login.tsx (which parses
 * ``/signup/<code>``) lives ONLY on the apex, so a link built from
 * ``window.location.origin`` on a persona subdomain (dash./app./…)
 * 404s the unauthenticated recipient.  Mirrors the rule documented in
 * features/settings/Invites.tsx; extracted so invite surfaces can't drift.
 */
export function buildSignupInviteUrl(code: string, signupBase: string): string {
  const base = signupBase || window.location.origin;
  return `${base}/signup/${code}`;
}

/** The apex signup origin from /auth/config ('' during the brief load
 *  window — buildSignupInviteUrl falls back to the current origin). */
export function useSignupBase(): string {
  const [signupBase, setSignupBase] = useState('');
  useEffect(() => {
    apiJSON<{ signup_base_url?: string }>('/auth/config')
      .then((d) => { if (d.signup_base_url) setSignupBase(d.signup_base_url); })
      .catch(() => { /* fall back to current origin */ });
  }, []);
  return signupBase;
}
