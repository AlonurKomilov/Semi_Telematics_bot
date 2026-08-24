// One carrier's reference profile.  Recruiters see it read-only; a recruiting
// MANAGER (recruiter + is_manager, i.e. can_manage_carrier_directory) can edit
// every section (name/contact, the free-text application process, and the
// pre-qual / presentation / recruiter-only label→value sheets) or delete the
// carrier.  Info-only (v1).
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Pencil, Trash2, Plus, X, ExternalLink, Link2, Copy, Mail, Lock, Check, Ban, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { apiJSON } from '../../api/client';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';
import DataGrid from '../../components/datagrid';
import type { AnyColumn } from '../../types';
import { useRoleView } from '../../context/RoleViewContext';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDay } from '../../utils/datetime';
import { toneClasses, toneText } from '../../lib/status';
import { APEX_DOMAIN } from '../../lib/safeReturnTo';
import { SECTIONS, mergeRows, safeHref } from './fields';
import type { CarrierContent, FieldRow } from './fields';
import FieldValueInput from './FieldValueInput';
import { Card } from '@/components/ui/card';

interface Profile {
  id: number; name: string; website: string; video_url: string;
  experience_summary: string; content: CarrierContent;
  // Carrier self-fill invite state.  intake_token is present for managers
  // only (it's the edit credential the public form writes with).
  intake_token?: string;
  intake_expires_at?: string | null;
  intake_email?: string;
  intake_submitted_at?: string;
  intake_review_pending?: number | boolean;
  /** Set only on a confirmed relay hand-off — `intake_email` alone just
   *  records the address a manager typed. */
  intake_email_sent_at?: string;
  /** Per-link sender name the carrier sees; '' inherits the account's
   *  public name, and if that's blank too the page names no one. */
  intake_display_name?: string;
}

const intakeUrlOf = (token: string) => `https://apply.${APEX_DOMAIN}/carrier/${token}`;

const EXPIRY_ITEMS = [
  { value: '7', label: '7 days' },
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
];

function intakeActive(p: Profile): boolean {
  return Boolean(
    p.intake_token && p.intake_expires_at &&
    new Date(p.intake_expires_at).getTime() > Date.now(),
  );
}

/** A link that was really sent and then ran out — distinct from never
 *  having invited this carrier at all.  Revoke NULLs `intake_expires_at`,
 *  so a revoked link reads as never-invited here, which is correct: the
 *  manager withdrew it deliberately. */
function intakeLapsed(p: Profile): boolean {
  return Boolean(
    p.intake_expires_at &&
    new Date(p.intake_expires_at).getTime() <= Date.now() &&
    !(p.intake_submitted_at || '').trim(),
  );
}

interface Draft {
  name: string; website: string; video_url: string; experience_summary: string;
  application_process: string;
  rows: Record<string, FieldRow[]>;   // section key → rows (template merged)
}

const ROW_SECTIONS = SECTIONS.filter((s) => s.kind === 'rows');
// Named in the review banner so a manager knows the carrier could not
// have touched the internal section, and needn't re-read it.
const PUBLIC_ROW_TITLES = SECTIONS.filter((s) => s.kind === 'rows' && !s.internal)
  .map((s) => s.title).join(' and ');
const INTERNAL_TITLE = SECTIONS.find((s) => s.internal)?.title ?? 'the internal section';
const templateOf = (key: string) => SECTIONS.find((s) => s.key === key)?.fields ?? [];

function buildDraft(p: Profile): Draft {
  const rows: Record<string, FieldRow[]> = {};
  for (const s of ROW_SECTIONS) {
    const stored = (p.content[s.key] as FieldRow[]) || [];
    rows[s.key] = mergeRows(s.fields, stored);
  }
  return {
    name: p.name, website: p.website, video_url: p.video_url,
    experience_summary: p.experience_summary,
    application_process: (p.content.application_process as string) || '',
    rows,
  };
}

const VALUE_COLS: AnyColumn[] = [
  { key: 'label', label: 'Field', render: (v) => <span className="font-medium text-foreground">{String(v)}</span> },
  { key: 'value', label: 'Value', render: (v) => <span className="whitespace-pre-wrap text-muted-foreground">{String(v)}</span> },
];

/** Manager-only "invite the carrier to fill this in" panel.  One active
 *  tokenized link per carrier; minting again rotates the token (the old
 *  emailed link dies), revoke kills it outright. */
function InvitePanel({ profile, reload }: { profile: Profile; reload: () => Promise<void> }) {
  const tz = useTimezone();
  const active = intakeActive(profile);
  const lapsed = !active && intakeLapsed(profile);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [email, setEmail] = useState('');
  const [days, setDays] = useState(30);
  const [senderName, setSenderName] = useState('');
  const [accountName, setAccountName] = useState('');
  // Blank is a REAL value here (show nobody), so it can't double as
  // "not loaded yet" — a failed fetch must not let the UI assert
  // "no company name" while the account actually has one set.
  const [accountNameLoaded, setAccountNameLoaded] = useState(false);
  const [emailErr, setEmailErr] = useState('');
  const [busy, setBusy] = useState(false);

  // What a blank override resolves to.  Needed by the collapsed status
  // line as well as the forms, so it loads once on mount.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await apiJSON<{ account_display_name: string }>(
          '/carrier-directory/sender-identity',
        );
        if (alive) { setAccountName(r.account_display_name || ''); setAccountNameLoaded(true); }
      } catch { /* stays unloaded — the hint hides rather than lying */ }
    })();
    return () => { alive = false; };
  }, []);

  // Only claim what blank does once we actually know.
  const blankMeans = !accountNameLoaded
    ? ''
    : accountName
      ? `Leave blank to use "${accountName}"`
      : 'Leave blank to show no company name at all';

  /** Opens the mint form. Seeds from the CURRENT link so replacing one
   *  doesn't silently blank the sender name and contact the manager set
   *  earlier — set_carrier_intake rewrites every intake column, so an
   *  empty form here meant an erase. */
  const startCreate = () => {
    setSenderName(profile.intake_display_name || '');
    setEmail(profile.intake_email || '');
    setOpen(true);
  };

  const create = async () => {
    setBusy(true);
    try {
      const r = await apiJSON<{ url: string; emailed: boolean; email_requested: boolean }>(
        `/carrier-directory/carriers/${profile.id}/intake-link`,
        {
          method: 'POST',
          body: {
            expires_in_days: days,
            email: email.trim(),
            display_name: senderName.trim(),
          },
        },
      );
      try { await navigator.clipboard.writeText(r.url); } catch { /* clipboard blocked */ }
      // Three outcomes, three messages. Collapsing "no email asked for"
      // and "the send failed" into one cheerful toast left managers
      // believing a carrier had been contacted who never was.
      if (r.emailed) {
        toast.success(`Fill link emailed to ${email.trim()} — also copied`);
      } else if (r.email_requested) {
        toast.warning(
          `Link created and copied, but the email to ${email.trim()} could NOT be sent — send the link yourself.`,
        );
      } else {
        toast.success('Fill link created and copied');
      }
      setOpen(false); setEmail(''); setSenderName('');
      await reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not create the fill link');
    } finally {
      setBusy(false);
    }
  };

  const startEdit = () => {
    setSenderName(profile.intake_display_name || '');
    setEmail(profile.intake_email || '');
    setEditing(true);
  };

  /** Edits the live link in place — the token is untouched, so a link
   *  the carrier already has keeps working. */
  const saveEdit = async () => {
    setBusy(true);
    try {
      await apiJSON(`/carrier-directory/carriers/${profile.id}/intake-link`, {
        method: 'PATCH',
        body: { display_name: senderName.trim(), email: email.trim() },
      });
      toast.success('Fill link updated — the link you sent still works');
      setEditing(false); setSenderName(''); setEmail('');
      await reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not update the link');
    } finally {
      setBusy(false);
    }
  };

  const revoke = async () => {
    const who = profile.intake_email
      ? `The fill link you sent to ${profile.intake_email} will stop working.`
      : 'The fill link you shared will stop working.';
    if (!confirm(`Revoke this fill link?\n\n${who}\nAnything the carrier already submitted is kept.`)) return;
    setBusy(true);
    try {
      await apiJSON(`/carrier-directory/carriers/${profile.id}/intake-link`, { method: 'DELETE' });
      toast.success('Fill link revoked');
      await reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not revoke the link');
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    if (!profile.intake_token) return;
    try {
      await navigator.clipboard.writeText(intakeUrlOf(profile.intake_token));
      toast.success('Fill link copied');
    } catch {
      toast.error('Could not copy — clipboard is blocked');
    }
  };

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm">
          <Link2 className="text-muted-foreground size-4" />
          {active ? (
            /* Three FIXED slots — state · expiry · recipient — so two
               carriers can be compared down the page. Slots used to appear
               and disappear with whatever data happened to exist, which
               made every carrier a different shape. An absent value now
               says so instead of vanishing. */
            <span className="text-foreground">
              Fill link active
              <span className="text-muted-foreground">
                {' · expires '}{formatDay(profile.intake_expires_at, { timeZone: tz })}
                {' · '}
                {!profile.intake_email
                  ? 'no contact email yet'
                  : profile.intake_email_sent_at
                    ? `emailed ${profile.intake_email}`
                    : `${profile.intake_email} (not emailed)`}
                {/* Silent until resolved — see accountNameLoaded. */}
                {accountNameLoaded || profile.intake_display_name
                  ? ` · shown as ${profile.intake_display_name || accountName || 'no company name'}`
                  : ''}
              </span>
            </span>
          ) : lapsed ? (
            /* An expired link used to render exactly like never-invited, so
               a manager could not tell "they ignored us" from "we never
               asked" — the two call for opposite next moves. */
            <span className="text-foreground">
              Fill link lapsed
              <span className="text-muted-foreground">
                {' · expired '}{formatDay(profile.intake_expires_at, { timeZone: tz })}
                {' · '}
                {profile.intake_email ? `was sent to ${profile.intake_email}` : 'no contact email'}
                {' · never filled in'}
              </span>
            </span>
          ) : (
            <span className="text-muted-foreground">
              No fill link yet — send this carrier a private link and they fill in their own sheet.
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {active && (
            <>
              {/* Copy is the action that moves the job forward and by far
                  the most frequent, so it carries the weight. Edit link is
                  occasional; Revoke destroys a URL already in someone's
                  inbox and must not look like its benign neighbours. */}
              <Button size="sm" onClick={copy}><Copy /> Copy link</Button>
              {/* "Edit link" not "Edit" — the page-level Edit button sits
                  a few pixels away and edits a different object. */}
              {!editing && (
                <Button size="sm" variant="ghost" onClick={startEdit}>
                  <Pencil /> Edit link
                </Button>
              )}
              <span className="mx-1 h-5 w-px bg-border" aria-hidden />
              <Button size="sm" variant="ghost" onClick={revoke} disabled={busy}
                className={toneText('danger')}>
                <Ban /> Revoke
              </Button>
            </>
          )}
          {!open && !editing && (
            /* Refresh, not an envelope: replacing rotates the token. The
               envelope belongs to the paths that actually offer to email. */
            <Button size="sm" variant={active ? 'ghost' : 'default'} onClick={startCreate}>
              {active ? <RefreshCw /> : <Mail />}
              {active ? 'Replace link' : lapsed ? 'Send a new link' : 'Invite carrier'}
            </Button>
          )}
        </div>
      </div>
      {(open || editing) && (
        /* A grid, not items-end flex: the public-name field carries a
           persistent hint and its sibling doesn't, so bottom-aligning the
           two columns pushed the email input a hint's-height lower than
           its partner. Commit actions get their own row rather than
           sharing one with an input. */
        <div className="mt-3 flex flex-col gap-3 border-t border-border pt-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_auto]">
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Public name (optional)
            </span>
            <Input value={senderName} onChange={(e) => setSenderName(e.target.value)}
              maxLength={120} placeholder="Shown to the carrier as…" />
            {/* Persistent, not a placeholder: this sentence states a
                PRIVACY consequence, and a placeholder disappears the
                moment the manager starts typing. */}
            {blankMeans && (
              <span className="text-xs text-muted-foreground">{blankMeans}</span>
            )}
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Carrier contact email (optional)
            </span>
            {/* Placeholder is the EXAMPLE only — the sentence lives in the
                helper line, where it can't be truncated mid-word by a
                narrow input. */}
            <Input value={email} type="email" placeholder="recruiting@carrier.com"
              onChange={(e) => { setEmail(e.target.value); if (emailErr) setEmailErr(''); }}
              onBlur={() => setEmailErr(
                email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())
                  ? "That doesn't look like an email address — the carrier won't receive the link."
                  : '',
              )} />
            <span className={`text-xs ${emailErr ? toneText('danger') : 'text-muted-foreground'}`}>
              {emailErr || 'Leave blank to just copy the link and send it yourself.'}
            </span>
          </label>
          {!editing && (
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Expires in</span>
              <Select value={String(days)} onValueChange={(v) => setDays(Number(v))} items={EXPIRY_ITEMS}>
                <SelectTrigger aria-label="Expires in"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {EXPIRY_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </label>
          )}
          </div>
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={editing ? saveEdit : create} disabled={busy || Boolean(emailErr)}>
              {busy
                ? (editing ? 'Saving…' : 'Creating…')
                : editing ? 'Save changes' : active ? 'Replace link' : 'Create link'}
            </Button>
            <Button size="sm" variant="ghost" disabled={busy}
              onClick={() => { setOpen(false); setEditing(false); setSenderName(''); setEmail(''); setEmailErr(''); }}>
              Cancel
            </Button>
          </div>
          <p className="w-full text-xs text-muted-foreground">
            {editing
              ? 'Saving keeps the same link — whatever you already sent the carrier still works.'
              : active
                ? 'Creating a new link replaces the current one — the previously sent link stops working. Use Edit to change these without breaking it.'
                : 'The carrier never sees your registered company name.'}
          </p>
        </div>
      )}
    </Card>
  );
}

export default function CarrierProfile() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const { viewHasAny } = useRoleView();
  const canEdit = viewHasAny('can_manage_carrier_directory');
  const tz = useTimezone();

  const [profile, setProfile] = useState<Profile | null>(null);
  const [err, setErr] = useState('');
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [reviewing, setReviewing] = useState(false);

  const load = useCallback(async () => {
    try {
      const p = await apiJSON<Profile>(`/carrier-directory/carriers/${id}`);
      setProfile(p);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to load carrier');
    }
  }, [id]);
  useEffect(() => { load(); }, [load]);

  const startEdit = () => { if (profile) { setDraft(buildDraft(profile)); setEditing(true); } };

  // A draft that differs from what's stored.  Guards both exits below —
  // this editor holds ~73 rows, so a stray Cancel or Back used to be able
  // to bin an afternoon's typing with no prompt at all.
  // A counter bumped by every mutator, NOT a deep compare. The previous
  // version ran two full JSON.stringify passes plus a buildDraft (three
  // mergeRows over ~73 rows) on EVERY KEYSTROKE, because `draft` is a new
  // object each time — the single most expensive thing in a form this
  // size. Over-reporting "dirty" after an undo-by-hand is a fair trade for
  // not rebuilding the draft on every character.
  const [dirtyCount, setDirtyCount] = useState(0);
  const dirty = editing && dirtyCount > 0;
  const touch = useCallback(() => setDirtyCount((n) => n + 1), []);

  // Name the size of the loss, not just its existence.
  const discardPrompt = () =>
    `Discard ${dirtyCount} unsaved change${dirtyCount === 1 ? '' : 's'} to this carrier?`;

  const cancelEdit = () => {
    if (dirty && !confirm(discardPrompt())) return;
    setEditing(false); setDraft(null); setDirtyCount(0);
  };

  const leavePage = () => {
    if (dirty && !confirm(discardPrompt())) return;
    nav('/workforce/carrier-directory');
  };

  // The tab-close / reload half of the same guard — React Router can't see
  // those, so the browser prompt is the only thing standing there.
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  type TextField = 'name' | 'website' | 'video_url' | 'experience_summary' | 'application_process';
  const setField = (k: TextField, v: string) => { touch(); setDraft((d) => (d ? { ...d, [k]: v } : d)); };
  const setRow = (sec: string, idx: number, patch: Partial<FieldRow>) => {
    touch();
    setDraft((d) => (d ? { ...d, rows: { ...d.rows, [sec]: d.rows[sec].map((r, i) => (i === idx ? { ...r, ...patch } : r)) } } : d));
  };
  const addRow = (sec: string) => {
    touch();
    setDraft((d) => (d ? { ...d, rows: { ...d.rows, [sec]: [...d.rows[sec], { label: '', value: '' }] } } : d));
  };
  const removeRow = (sec: string, idx: number) => {
    touch();
    setDraft((d) => (d ? { ...d, rows: { ...d.rows, [sec]: d.rows[sec].filter((_, i) => i !== idx) } } : d));
  };

  // Declared above its consumers (`remove` counts these) so the reader
  // never has to reason about closure timing.
  const filledView = useMemo(() => {
    const out: Record<string, FieldRow[]> = {};
    if (profile) {
      for (const s of ROW_SECTIONS) {
        const stored = (profile.content[s.key] as FieldRow[]) || [];
        out[s.key] = mergeRows(s.fields, stored).filter((r) => r.value.trim());
      }
    }
    return out;
  }, [profile]);

  const save = async () => {
    if (!draft) return;
    if (!draft.name.trim()) { toast.error('Carrier name is required'); return; }
    setSaving(true);
    try {
      const content: CarrierContent = { application_process: draft.application_process };
      for (const s of ROW_SECTIONS) {
        // Persist only rows that carry a value (label + value); blanks are
        // re-seeded from the template on the next edit.
        content[s.key] = draft.rows[s.key].filter((r) => r.value.trim() && r.label.trim());
      }
      const updated = await apiJSON<Profile>(`/carrier-directory/carriers/${id}`, {
        method: 'PATCH',
        body: {
          name: draft.name.trim(), website: draft.website.trim(),
          video_url: draft.video_url.trim(), experience_summary: draft.experience_summary.trim(),
          content,
        },
      });
      setProfile(updated);
      setEditing(false); setDraft(null); setDirtyCount(0);
      toast.success('Carrier saved');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const markReviewed = async () => {
    setReviewing(true);
    try {
      await apiJSON(`/carrier-directory/carriers/${id}/mark-reviewed`, { method: 'POST' });
      await load();
      toast.success('Marked reviewed');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not mark reviewed');
    } finally {
      setReviewing(false);
    }
  };

  const remove = async () => {
    // Name what actually dies — including the live fill link, which the old
    // wording never mentioned even though the carrier's open tab 404s.
    const filled = ROW_SECTIONS.reduce((n, s) => n + (filledView[s.key]?.length ?? 0), 0);
    const lines = [
      `Delete "${profile?.name ?? 'this carrier'}" from the directory?`,
      '',
      `This permanently removes ${filled} filled field${filled === 1 ? '' : 's'}, the application-process notes and the internal recruiter notes.`,
    ];
    if (profile && intakeActive(profile)) {
      lines.push('The carrier\'s fill-in link stops working immediately.');
    }
    lines.push('', 'This cannot be undone.');
    if (!confirm(lines.join('\n'))) return;
    try {
      await apiJSON(`/carrier-directory/carriers/${id}`, { method: 'DELETE' });
      toast.success('Carrier deleted');
      nav('/workforce/carrier-directory');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  if (err) return <div className="p-8 text-sm text-muted-foreground">{err}</div>;
  if (!profile) return <div className="p-8 text-sm text-muted-foreground">Loading…</div>;

  // "Untouched" = no section rows and no process notes. The identity
  // fields don't count: a carrier always has at least a name.
  const isBlank = ROW_SECTIONS.every((s) => !(filledView[s.key]?.length))
    && !String(profile.content.application_process || '').trim();

  const sectionTitle = 'text-lg font-semibold text-foreground';
  const capsLabel = 'text-xs font-medium uppercase tracking-wide text-muted-foreground';

  return (
    <div className="flex flex-col gap-6">
      {/* Sticky while editing: Save used to live only here, so anyone
          working in Orientation or the internal section had ~73 rows to
          scroll back through to commit. z-30 is the sticky tier. */}
      <div className={`flex items-center justify-between gap-3 ${
        editing ? 'sticky top-0 z-30 -mx-1 border-b border-border bg-background px-1 py-2' : ''
      }`}>
        <button type="button" onClick={leavePage}
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground py-0.5 -my-0.5 min-h-tap">
          <ArrowLeft className="size-4" /> Carrier Directory
        </button>
        {canEdit && (editing ? (
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">
              {dirtyCount > 0
                ? `${dirtyCount} unsaved change${dirtyCount === 1 ? '' : 's'}`
                : 'No changes yet'}
            </span>
            <Button size="sm" variant="ghost" onClick={cancelEdit} disabled={saving}>Cancel</Button>
            <Button size="sm" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={remove}><Trash2 /> Delete</Button>
            <Button size="sm" onClick={startEdit}><Pencil /> Edit</Button>
          </div>
        ))}
      </div>

      {/* Header / identity */}
      {editing && draft ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1">
            <span className={capsLabel}>Carrier name *</span>
            <Input value={draft.name} onChange={(e) => setField('name', e.target.value)} />
          </label>
          <label className="flex flex-col gap-1">
            <span className={capsLabel}>Website</span>
            <Input value={draft.website} onChange={(e) => setField('website', e.target.value)} placeholder="https://…" />
          </label>
          <label className="flex flex-col gap-1">
            <span className={capsLabel}>Video overview URL</span>
            <Input value={draft.video_url} onChange={(e) => setField('video_url', e.target.value)} placeholder="https://…" />
          </label>
          <label className="flex flex-col gap-1">
            <span className={capsLabel}>Accepted experience levels</span>
            <Input value={draft.experience_summary} onChange={(e) => setField('experience_summary', e.target.value)}
              placeholder="e.g. 2 years verifiable OTR in the past 3 years" />
          </label>
        </div>
      ) : (
        <div>
          <h1 className="text-2xl font-bold text-foreground">{profile.name}</h1>
          {profile.experience_summary && (
            <p className="mt-1 text-sm text-muted-foreground">{profile.experience_summary}</p>
          )}
          {/* Both URLs are writable by an UNAUTHENTICATED external carrier
              through the intake form, so both go through safeHref — a
              `javascript:` value renders as no link at all rather than as
              stored XSS against the manager reviewing the submission. */}
          <div className="mt-2 flex flex-wrap gap-3 text-sm">
            {safeHref(profile.website) && (
              <a href={safeHref(profile.website)}
                target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline min-h-tap">
                <ExternalLink className="size-3.5" /> Website
              </a>
            )}
            {safeHref(profile.video_url) && (
              <a href={safeHref(profile.video_url)} target="_blank" rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-primary hover:underline min-h-tap">
                <ExternalLink className="size-3.5" /> Video overview
              </a>
            )}
          </div>
        </div>
      )}

      {/* Carrier self-fill: invite link management + review-pending flag */}
      {canEdit && !editing && <InvitePanel profile={profile} reload={load} />}
      {Boolean(profile.intake_review_pending) && (
        <div className={`flex flex-wrap items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm ${toneClasses('info')}`}>
          <span>
            {/* Names everything the public submit really writes — website,
                video and the experience line are carrier-supplied too, and
                they render as trusted links right above this banner. Only
                the internal section is out of their reach. */}
            Filled in by the carrier on {formatDay(profile.intake_submitted_at, { timeZone: tz })} —
            they can have changed the website, video and experience line above,
            {' '}{PUBLIC_ROW_TITLES}, and the submission notes. Not {INTERNAL_TITLE}.
          </span>
          {/* Finding it correct is the common outcome; without this the
              only way to clear the flag was a no-op save, which bumped
              updated_at and made the trail claim an edit that never
              happened. */}
          {canEdit && (
            <Button size="xs" variant="ghost" onClick={markReviewed} disabled={reviewing}>
              <Check /> {reviewing ? 'Marking…' : 'Mark reviewed'}
            </Button>
          )}
        </div>
      )}

      {/* An untouched carrier used to render four headings each followed
          by "Not provided." and no way forward — the only route in was a
          small Edit in the corner. Say what to do, in order, once. */}
      {!editing && isBlank && (
        <div className="rounded-lg border border-dashed border-border p-4">
          <p className="text-base font-semibold text-foreground">Nothing filled in yet</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Two ways to build this sheet — either works, and you can mix them.
          </p>
          <ol className="mt-3 flex list-decimal flex-col gap-1 pl-5 text-sm text-muted-foreground">
            <li>Send {profile.name} a fill link and let them answer in their own words.</li>
            <li>Fill it in yourself from what you already know.</li>
          </ol>
          {canEdit && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Button size="sm" onClick={startEdit}><Pencil /> Fill it in myself</Button>
              <span className="text-xs text-muted-foreground">
                …or use the fill link panel above to invite the carrier.
              </span>
            </div>
          )}
        </div>
      )}

      {/* Application Process (free text) */}
      <div className="flex flex-col gap-2">
        <p className={sectionTitle}>Application Process</p>
        {editing && draft ? (
          <Textarea rows={6} value={draft.application_process}
            onChange={(e) => setField('application_process', e.target.value)}
            placeholder="How a recruiter submits an application for this carrier, follow-up steps, approval + invoicing notes…" />
        ) : profile.content.application_process ? (
          <p className="whitespace-pre-wrap text-sm text-muted-foreground">{String(profile.content.application_process)}</p>
        ) : (
          <p className="text-sm text-muted-foreground/70">Not provided.</p>
        )}
      </div>

      {/* Label→value sheets */}
      {ROW_SECTIONS.map((s) => {
        const tpl = templateOf(s.key);
        const tplLen = tpl.length;
        const rows = draft?.rows[s.key] ?? [];
        // Progress is measured against the STANDARD schema only, so
        // "+ Add field" can neither inflate the denominator ("75 of 73")
        // nor make progress look worse by adding work. Custom rows are
        // reported alongside instead of inside the fraction.
        const done = editing
          ? rows.slice(0, tplLen).filter((r) => r.value.trim()).length
          : Math.min(filledView[s.key]?.length ?? 0, tplLen);
        const extra = editing
          ? rows.slice(tplLen).filter((r) => r.value.trim() && r.label.trim()).length
          : Math.max((filledView[s.key]?.length ?? 0) - tplLen, 0);
        return (
          <div key={s.key} className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <p className={sectionTitle}>{s.title}</p>
                {/* The backend wall around recruiter_only is real but was
                    invisible to the manager relying on it. */}
                {s.internal && (
                  <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-2xs font-medium ${toneClasses('neutral')}`}>
                    <Lock className="size-3" /> Internal
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                {/* A 73-field job with no visible progress reads as endless. */}
                <span className="text-xs text-muted-foreground">
                  {done} of {tplLen} filled{extra > 0 ? ` · +${extra} custom` : ''}
                </span>
                {editing && draft && (
                  <Button size="xs" variant="ghost" onClick={() => addRow(s.key)}><Plus /> Add field</Button>
                )}
              </div>
            </div>
            {s.blurb && <p className="text-xs text-muted-foreground">{s.blurb}</p>}
            {editing && draft ? (
              <div className="flex flex-col gap-2">
                {draft.rows[s.key].map((r, i) => {
                  const def = i < tplLen ? tpl[i] : undefined;
                  // Sub-heading whenever the group changes — the pay /
                  // routes / equipment runs are otherwise one 53-row wall.
                  const newGroup = def?.group && def.group !== tpl[i - 1]?.group;
                  return (
                    <div key={i} className="flex flex-col gap-2">
                      {newGroup && (
                        <p className="mt-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">{def!.group}</p>
                      )}
                      <div className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,14rem)_1fr_auto]">
                        {def ? (
                          <span className="flex flex-col justify-center text-sm text-foreground">
                            {def.label}
                            {def.hint && <span className="text-xs text-muted-foreground">{def.hint}</span>}
                          </span>
                        ) : (
                          <Input value={r.label} placeholder="Field name"
                            onChange={(e) => setRow(s.key, i, { label: e.target.value })} />
                        )}
                        <FieldValueInput def={def} value={r.value}
                          onChange={(v) => setRow(s.key, i, { value: v })} />
                        {!def ? (
                          <button type="button" onClick={() => removeRow(s.key, i)}
                            aria-label={`Remove ${r.label || 'field'}`}
                            className="inline-flex items-center justify-center text-muted-foreground hover:text-danger">
                            <X className="size-4" />
                          </button>
                        ) : <span />}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : filledView[s.key]?.length ? (
              <DataGrid columns={VALUE_COLS} data={filledView[s.key] as unknown as Record<string, unknown>[]}
                enableToolbar={false} enablePagination={false} />
            ) : isBlank ? null : (
              <p className="text-sm text-muted-foreground/70">Not provided.</p>
            )}
          </div>
        );
      })}
    </div>
  );
}
