/**
 * Drivers → Onboarding — the recruiting hand-off.
 *
 * A sub-feature of the Drivers family: the recruiter approves an
 * applicant, and whoever administers drivers finishes the hire here.
 * Hiring mints a USER, which is roster work, so it carries its own grant
 * (`can_onboard_drivers`) rather than riding either the recruiting
 * dashboard or roster admin — a Fleet manager administers drivers but
 * does not hire.
 *
 * Backend: features/drivers/onboarding/router.py
 */
import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { UserPlus, Copy, Check } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { Button } from '../../components/ui/button';
import { InfoTip } from '../../components/tooltip';
import { toneClasses } from '../../lib/status';
import { formatAgoShort } from '../../utils/datetime';
import { useTimezone } from '../../hooks/useTimezone';

interface Applicant {
  id: number;
  reference: string;
  first_name: string;
  last_name: string;
  email: string;
  reviewed_at?: string;
}

export default function OnboardingQueue() {
  const qc = useQueryClient();
  const tz = useTimezone();
  const [busyId, setBusyId] = useState<number | null>(null);
  const [invite, setInvite] = useState<{ id: number; link: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState('');

  const { data } = useQuery<{ applicants: Applicant[] }>({
    queryKey: ['driver-onboarding-queue'],
    queryFn: () => apiJSON('/drivers/onboarding/queue'),
  });
  const queue = data?.applicants ?? [];

  // Nothing waiting is the normal state — an empty well would be noise on
  // a page whose main job is the roster.
  if (!queue.length && !invite) return null;

  const hire = async (a: Applicant) => {
    setError('');
    setBusyId(a.id);
    try {
      const r = await apiJSON<{ invite_link: string }>(
        `/drivers/onboarding/${a.id}/convert`, { method: 'POST' },
      );
      setInvite({ id: a.id, link: r.invite_link });
      qc.invalidateQueries({ queryKey: ['driver-onboarding-queue'] });
      qc.invalidateQueries({ queryKey: ['drivers'] });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not complete the hire');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="mb-4 rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-1.5 mb-1">
        <UserPlus className="text-primary shrink-0 size-4" aria-hidden />
        <span className="text-base font-semibold text-foreground">Onboarding</span>
        <span className={`text-2xs px-1.5 py-0.5 rounded-md ${toneClasses('warn')}`}>
          {queue.length} waiting
        </span>
        <InfoTip size={12} label="Applicants the recruiter approved. Hiring creates their driver invite — the last step of recruiting, done by whoever administers drivers." />
      </div>

      {invite && (
        <div className={`mb-3 rounded-md p-3 text-sm ${toneClasses('ok')}`}>
          <p className="font-medium">Invite created — share this link with the driver:</p>
          <div className="flex items-center gap-2 mt-1">
            <code className="text-xs break-all">{invite.link}</code>
            <Button
              size="sm"
              variant="outline"
              className="gap-1 shrink-0"
              onClick={() => {
                navigator.clipboard?.writeText(invite.link);
                setCopied(true);
                setTimeout(() => setCopied(false), 2000);
              }}
            >
              {copied ? <Check /> : <Copy />} {copied ? 'Copied' : 'Copy'}
            </Button>
          </div>
        </div>
      )}

      {error && <p className="text-danger text-sm mb-2">{error}</p>}

      <ul className="space-y-1.5">
        {queue.map((a) => (
          <li key={a.id} className="flex items-center justify-between gap-3 text-sm">
            <div className="min-w-0">
              <span className="font-medium">
                {`${a.first_name} ${a.last_name}`.trim() || a.reference}
              </span>
              <span className="text-2xs text-muted-foreground ml-2">
                {a.email}
                {a.reviewed_at && ` · approved ${formatAgoShort(a.reviewed_at, { timeZone: tz })}`}
              </span>
            </div>
            <Button
              size="sm"
              className="gap-1.5 shrink-0"
              disabled={busyId === a.id}
              onClick={() => hire(a)}
            >
              <UserPlus aria-hidden /> Hire
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
