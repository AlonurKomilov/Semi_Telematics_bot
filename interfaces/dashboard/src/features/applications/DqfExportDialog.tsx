import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ShieldCheck, Eye, Loader2, TriangleAlert } from 'lucide-react';

import { toast } from 'sonner';

import { apiJSON } from '../../api/client';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '../../components/ui/dialog';

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
 * A DIALOG opened from the page header, not a card in the content flow.
 * This is account-scope config, and the house precedent for that tier is
 * KPI's Thresholds dialog — set once, then out of the way.  It is NOT the
 * page-sections gear: that tier arranges what a role SEES
 * (``page_layouts``, own-role) and its own docstring says it never
 * touches anything but arrangement.  A passphrase that decides what gets
 * written into shared storage is the other tier entirely.
 *
 * This dialog sets that password.  Until an administrator chooses one the
 * export falls back to a passphrase derived from the carrier's own
 * identifiers (owner decision — see dqf.default_passphrase), so files are
 * complete and recoverable from day one.  That default is weak by
 * construction and the card says so.
 *
 * Gated on ``can_manage_config_all`` server-side; the affordance mirrors
 * that check rather than assuming.
 */

/** One default per carrier brand — an account with several companies
 *  protects each brand's files with that brand's own identifiers, so a
 *  single value would misrepresent what opens what. */
interface RevealResponse {
  using_default: boolean;
  passphrase: string;
  defaults: { company: string; passphrase: string }[];
}

interface DqfConfig {
  configured: boolean;
  ssn_included: boolean;
  /** True when no administrator has chosen one and the account is
   *  falling back to the identifier-derived default. */
  using_default: boolean;
}

export default function DqfExportDialog({
  open, onOpenChange, canManage,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  canManage: boolean;
}) {
  const qc = useQueryClient();
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [revealed, setRevealed] = useState<RevealResponse | null>(null);

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
      setRevealed(null);
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
      const r = await apiJSON<RevealResponse>(
        '/applications/dqf-config/reveal', { method: 'POST' },
      );
      setRevealed(r);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not retrieve the passphrase');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldCheck size={16} className="text-muted-foreground" />
            DQF export
          </DialogTitle>
          <DialogDescription>
            Every application is exported to your own cloud storage as a
            self-contained driver qualification file, so your records still work
            if 4truck is unavailable. This passphrase protects the one file that
            holds the applicant&rsquo;s Social Security Number.
          </DialogDescription>
        </DialogHeader>

        {!isLoading && data && (
        <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Exported files include a password-protected{' '}
        <span className="font-mono">ssn-protected.pdf</span>. Everything else in
        the folder stays readable, so a qualification file can be shared without
        exposing the number.
      </p>

      {/* The default is derived from identifiers that are public (MC and
          USDOT are listed against the carrier name in FMCSA SAFER) and it
          sits under a folder named after the company. It stops a casual
          click, not someone trying — and an administrator deserves to be
          told that plainly rather than discovering it. */}
      {data.using_default && (
        <div className="flex items-start gap-2">
          <TriangleAlert size={14} className="text-warn shrink-0 mt-0.5" />
          <p className="text-xs text-muted-foreground">
            Using the default passphrase for your company. It keeps exports
            complete and recoverable, but it is derived from identifiers that
            are publicly listed, so it deters a casual click rather than a
            determined one.{' '}
            <span className="text-foreground font-medium">Set your own to
            protect these files properly.</span>
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
              placeholder={data.configured ? 'Replace passphrase…' : 'Set your own passphrase'}
              className="max-w-xs"
              autoComplete="new-password"
            />
            <Button size="sm" onClick={save} disabled={busy || value.trim().length < 8}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : data.configured ? 'Replace' : 'Set passphrase'}
            </Button>
            {!revealed && (
              <Button size="sm" variant="outline" onClick={reveal} disabled={busy}>
                <Eye size={14} /> Show current
              </Button>
            )}
          </div>

          {revealed && (
            <div className="rounded-lg border border-border bg-muted p-3 space-y-2">
              {revealed.using_default ? (
                <>
                  <p className="text-xs text-muted-foreground">
                    Default passphrase per carrier — files under each company open
                    with that company's own.
                  </p>
                  <ul className="space-y-1">
                    {revealed.defaults.map((d) => (
                      <li key={d.company} className="flex flex-wrap items-baseline gap-x-2">
                        <span className="text-xs text-muted-foreground">{d.company}</span>
                        <span className="font-mono text-sm text-foreground break-all">
                          {d.passphrase}
                        </span>
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <>
                  <p className="text-xs text-muted-foreground">Current passphrase</p>
                  <p className="font-mono text-sm text-foreground break-all">
                    {revealed.passphrase}
                  </p>
                </>
              )}
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
        </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
