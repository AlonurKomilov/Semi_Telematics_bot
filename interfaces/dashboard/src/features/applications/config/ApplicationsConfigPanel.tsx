import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Eye, Loader2, TriangleAlert, RefreshCw, Wand2, X } from 'lucide-react';

import { toast } from 'sonner';

import { apiJSON } from '../../../api/client';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import { generatePassphrase } from '../passphrase';

/**
 * The DQF export passphrase — account-scope config (capabilities/config/docs/ARCHITECTURE.md).
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

export default function ApplicationsConfigPanel() {
  // No `canManage` prop: FeatureConfigGear renders this only for holders
  // of can_manage_config_all, so reaching it IS the permission. Passing a
  // flag that is always true invites someone to pass false later and get
  // a half-rendered panel instead of a closed door.
  const canManage = true;
  const qc = useQueryClient();
  const [value, setValue] = useState('');
  // True while `value` holds a machine SUGGESTION the admin has not yet
  // committed. It drives two things and nothing else: the field shows in
  // plain text (you cannot check a passphrase you cannot read, and you
  // are about to be responsible for keeping it), and the copy says it is
  // not saved yet. Any keystroke clears the flag — from then on it is
  // their own text.
  const [suggested, setSuggested] = useState(false);
  const [busy, setBusy] = useState(false);
  const [revealed, setRevealed] = useState<RevealResponse | null>(null);
  const [reexporting, setReexporting] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['applications', 'config'],
    queryFn: () => apiJSON<DqfConfig>('/applications/config'),
  });

  async function save() {
    setBusy(true);
    try {
      await apiJSON('/applications/config', {
        method: 'PUT', body: { passphrase: value },
      });
      setValue('');
      setSuggested(false);
      setRevealed(null);
      qc.invalidateQueries({ queryKey: ['applications', 'config'] });
      toast.success('Passphrase saved — emailed to the account owners, and new exports use it');
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
        '/applications/config/reveal', { method: 'POST' },
      );
      setRevealed(r);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not retrieve the passphrase');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
        <div>
          <p className="text-sm text-muted-foreground">
            Every application is exported to your own cloud storage as a
            self-contained driver qualification file, so your records still work
            if 4truck is unavailable. This passphrase protects the one file that
            holds the applicant&rsquo;s Social Security Number.
          </p>
        </div>

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
          <TriangleAlert className="text-warn shrink-0 mt-0.5 size-3.5" />
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
              type={suggested ? 'text' : 'password'}
              value={value}
              onChange={(e) => { setValue(e.target.value); setSuggested(false); }}
              placeholder={data.configured ? 'Replace passphrase…' : 'Set your own passphrase'}
              className={`max-w-xs${suggested ? ' font-mono' : ''}`}
              autoComplete="new-password"
            />
            {/* Fills the field. Does NOT save — see the note below the
                row. The account keeps the passphrase it already has until
                someone presses the button to the left of this one. */}
            <Button
              size="sm"
              variant="outline"
              onClick={() => { setValue(generatePassphrase()); setSuggested(true); }}
              disabled={busy}
            >
              <Wand2 /> Suggest strong
            </Button>
            {suggested && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => { setValue(''); setSuggested(false); }}
                disabled={busy}
              >
                <X /> Discard
              </Button>
            )}
            <Button size="sm" onClick={save} disabled={busy || value.trim().length < 8}>
              {busy ? <Loader2 className="animate-spin" /> : data.configured ? 'Replace' : 'Set passphrase'}
            </Button>
          </div>

          {suggested && (
            /* The whole point of the suggestion flow: it is inert until a
               human commits it. Nothing rotates on its own — the derived
               default is deterministic, so silently replacing it would
               change the password on files already in carriers' storage
               and lock out anyone holding the old one. */
            <p className="text-xs text-muted-foreground">
              Suggested, <span className="text-foreground font-medium">not
              saved yet</span>. Write it down or replace it with your own,
              then press {data.configured ? '“Replace”' : '“Set passphrase”'}.
              Saving emails it to the account owners so there is a copy
              outside 4truck.
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {!revealed && (
              <Button size="sm" variant="outline" onClick={reveal} disabled={busy}>
                <Eye /> Show current
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

          {/* Rotating protects FUTURE exports only. Without this button
              "set your own to protect these files properly" is a
              half-truth — everything already in the carrier's storage
              keeps the password it was written with. */}
          {/* Collapsed by default. A UX pass found the panel led with
              four paragraphs before the input; re-export is a follow-up
              someone does after a rotation, not part of setting a
              passphrase, so it no longer competes with the decision. */}
          <details className="rounded-lg border border-border p-3">
            <summary className="text-xs font-medium text-foreground cursor-pointer py-1 -my-1 min-h-tap">
              Re-protect files already exported
            </summary>
            <p className="mt-2 text-xs text-muted-foreground">
              Changing the passphrase does not re-protect files already in your
              storage. Re-export rewrites each application&rsquo;s protected SSN
              file with the current passphrase — one file per application; the
              qualification files and uploaded documents are not touched.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={reexporting}
                onClick={async () => {
                  setReexporting(true);
                  try {
                    const r = await apiJSON<{
                      scanned: number; written: number; failed: number;
                    }>('/applications/config/reexport', { method: 'POST' });
                    toast.success(
                      `Re-exported ${r.written} of ${r.scanned} applications`
                      + (r.failed ? ` — ${r.failed} failed` : ''),
                    );
                  } catch (e) {
                    toast.error(e instanceof Error ? e.message : 'Re-export failed');
                  } finally {
                    setReexporting(false);
                  }
                }}
              >
                {reexporting
                  ? <Loader2 className="animate-spin" />
                  : <RefreshCw />}
                <span className="ml-1.5">Re-export all</span>
              </Button>
              {/* Files travel. Rotation is not a recall, and someone will
                  assume it is unless told. */}
              <span className="text-xs text-muted-foreground">
                Cannot reach copies already downloaded or shared.
              </span>
            </div>
          </details>

          {/* The one thing that decides whether any of this works. A
              passphrase only we hold is worthless in exactly the scenario
              the export exists for. */}
          <p className="text-xs text-muted-foreground">
            <span className="text-foreground font-medium">Keep a copy outside
            4truck.</span>{' '}
            If 4truck is ever unavailable, a passphrase you have not stored
            elsewhere cannot be recovered — and support cannot release one you
            chose.
          </p>
        </>
      )}
        </div>
        )}
    </div>
  );
}
