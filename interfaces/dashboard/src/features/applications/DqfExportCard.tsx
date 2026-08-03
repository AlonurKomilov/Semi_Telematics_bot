import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ShieldCheck, Eye, Loader2, TriangleAlert } from 'lucide-react';

import { toast } from 'sonner';

import { apiJSON } from '../../api/client';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { InfoTip } from '../../components/tooltip';

/**
 * The DQF export passphrase — account-scope config (docs/architecture/config.md).
 *
 * Every application's documents are written into the carrier's own cloud
 * storage alongside a self-contained driver qualification file, so the
 * record still means something if this platform is not around.  The
 * applicant's SSN is required in that file by 49 CFR 391.21(b)(2), but the
 * same folder gets shared with brokers and office staff — so it is split
 * into its own password-protected PDF and everything else stays readable.
 *
 * This card sets that password.  Until it is set the export is complete
 * EXCEPT the SSN file, which is the safe default: no unprotected SSN ever
 * lands in someone's Drive because nobody made a choice.
 *
 * Gated on ``can_manage_config_all`` server-side; the affordance mirrors
 * that check rather than assuming.
 */

interface DqfConfig {
  configured: boolean;
  ssn_included: boolean;
}

export default function DqfExportCard({ canManage }: { canManage: boolean }) {
  const qc = useQueryClient();
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [revealed, setRevealed] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['applications', 'dqf-config'],
    queryFn: () => apiJSON<DqfConfig>('/applications/dqf-config'),
  });

  async function save() {
    setBusy(true);
    try {
      await apiJSON('/applications/dqf-config', {
        method: 'PUT', body: { passphrase: value },
      });
      setValue('');
      setRevealed('');
      qc.invalidateQueries({ queryKey: ['applications', 'dqf-config'] });
      toast.success('Passphrase saved — new exports will include the protected SSN file');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not save the passphrase');
    } finally {
      setBusy(false);
    }
  }

  // A deliberate action, not something the page does on load — and the
  // server records it in the activity trail, so a reveal is visible to
  // the account afterwards.
  async function reveal() {
    setBusy(true);
    try {
      const r = await apiJSON<{ passphrase: string }>(
        '/applications/dqf-config/reveal', { method: 'POST' },
      );
      setRevealed(r.passphrase);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not retrieve the passphrase');
    } finally {
      setBusy(false);
    }
  }

  if (isLoading || !data) return null;

  return (
    <section className="bg-card border border-border rounded-lg p-4 space-y-3">
      <h2 className="text-base font-semibold flex items-center gap-2">
        <ShieldCheck size={16} className="text-muted-foreground" />
        DQF export
        <InfoTip label="Every application is exported to your own cloud storage as a self-contained driver qualification file, so your records still work if 4truck is unavailable. This passphrase protects the one file that holds the applicant's Social Security Number." />
      </h2>

      {data.configured ? (
        <p className="text-xs text-muted-foreground">
          Exported files include a password-protected{' '}
          <span className="font-mono">ssn-protected.pdf</span>. Everything else in
          the folder stays readable, so a qualification file can be shared without
          exposing the number.
        </p>
      ) : (
        <div className="flex items-start gap-2">
          <TriangleAlert size={14} className="text-warn shrink-0 mt-0.5" />
          <p className="text-xs text-muted-foreground">
            No passphrase set, so exported qualification files{' '}
            <span className="text-foreground font-medium">do not include the
            applicant's Social Security Number</span> — which 49 CFR 391.21(b)(2)
            requires on the employment application. Set one to complete them.
          </p>
        </div>
      )}

      {canManage && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              type="password"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={data.configured ? 'Replace passphrase…' : 'Choose a passphrase'}
              className="max-w-xs"
              autoComplete="new-password"
            />
            <Button size="sm" onClick={save} disabled={busy || value.trim().length < 8}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : data.configured ? 'Replace' : 'Set passphrase'}
            </Button>
            {data.configured && !revealed && (
              <Button size="sm" variant="outline" onClick={reveal} disabled={busy}>
                <Eye size={14} /> Show current
              </Button>
            )}
          </div>

          {revealed && (
            <div className="rounded-lg border border-border bg-muted p-3 space-y-1">
              <p className="text-xs text-muted-foreground">Current passphrase</p>
              <p className="font-mono text-sm text-foreground break-all">{revealed}</p>
            </div>
          )}

          {/* The one thing that decides whether any of this works. A
              passphrase only we hold is worthless in exactly the scenario
              the export exists for. */}
          <p className="text-xs text-muted-foreground">
            <span className="text-foreground font-medium">Keep a copy outside
            4truck.</span>{' '}
            Replacing the passphrase does not re-protect files already exported —
            those still open only with the passphrase they were written with. If
            4truck is ever unavailable, a passphrase you have not stored elsewhere
            cannot be recovered.
          </p>
        </>
      )}
    </section>
  );
}
