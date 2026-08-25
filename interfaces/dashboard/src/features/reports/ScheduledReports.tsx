import { useEffect, useMemo, useState } from 'react';
import { Mail, MessageSquare, Pencil, Plus, Send, Trash2, X } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';
import { CardSkeleton, ErrorState } from '../../components/shell';
import type { ScheduledReport } from '../../types';
import { TIMEZONE_OPTIONS, timezoneLabelWithTime } from '../../utils/timezones';
import { useNow } from '../../hooks/useNow';
import { useAuth } from '../../context/AuthContext';
import { REPORTS, type ReportKey } from '../../data/reports';
import { Card } from '@/components/ui/card';

const REPORT_LABEL: Record<string, string> = Object.fromEntries(
  REPORTS.map((r) => [r.key, `${r.emoji} ${r.labelFull}`]),
);

const FREQUENCIES = ['daily', 'weekly', 'monthly'] as const;

const HOURS = Array.from({ length: 24 }, (_, i) => i);
const HOUR_ITEMS = HOURS.map((h) => ({ value: String(h), label: fmtHour(h) }));

type Channel = 'telegram' | 'email';

function fmtHour(h: number) {
  if (h === 0) return '12:00 AM';
  if (h === 12) return '12:00 PM';
  return h < 12 ? `${h}:00 AM` : `${h - 12}:00 PM`;
}

function parseChannels(raw: string | undefined): Channel[] {
  if (!raw) return ['telegram'];
  const out: Channel[] = [];
  for (const c of raw.split(',')) {
    const v = c.trim().toLowerCase();
    if ((v === 'telegram' || v === 'email') && !out.includes(v)) {
      out.push(v);
    }
  }
  return out.length ? out : ['telegram'];
}

function channelLabel(raw: string | undefined) {
  return parseChannels(raw).map((c) => (c === 'telegram' ? 'Telegram' : 'Email')).join(' + ');
}


// ── List view: per-schedule row ──────────────────────────────────

function ScheduleRow({
  row, onEdit, onDelete,
}: {
  row: ScheduledReport;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="flex items-center justify-between bg-muted rounded-lg p-3 border border-border">
      <div className="min-w-0">
        <div className="text-sm font-medium text-foreground">
          {REPORT_LABEL[row.report_type] || row.report_type}
        </div>
        <div className="text-xs text-muted-foreground mt-0.5">
          {row.frequency} at {fmtHour(row.send_hour)} ({row.timezone})
        </div>
        <div className="text-xs text-muted-foreground mt-0.5">
          {channelLabel(row.delivery_channels)}
        </div>
      </div>
      <div className="flex gap-2 ml-3">
        <button
          onClick={onEdit}
          className="p-2 text-muted-foreground hover:text-foreground transition-colors rounded min-h-tap"
          title="Edit"
        >
          <Pencil className="size-4" />
        </button>
        <button
          onClick={onDelete}
          className="p-2 text-destructive hover:text-destructive/80 transition-colors rounded min-h-tap"
          title="Stop"
        >
          <Trash2 className="size-4" />
        </button>
      </div>
    </div>
  );
}


// ── Editor: shared by Add + Edit flows ──────────────────────────

interface EditorState {
  reportType: string;
  frequency: string;
  sendHour: number;
  timezone: string;
  channels: Channel[];
  // Locks reportType when editing an existing row — schedule rows are
  // keyed on (user_id, report_type) so changing report_type would
  // collide with another row.  Add mode lets the user pick freely.
  reportTypeLocked: boolean;
}

function ScheduleEditor({
  initial, existingTypes, telegramEligible, emailEligible,
  emailHint, telegramHint, now, onCancel, onSave, saving,
}: {
  initial: EditorState;
  existingTypes: Set<string>;
  telegramEligible: boolean;
  emailEligible: boolean;
  emailHint: string;
  telegramHint: string;
  now: Date;
  onCancel: () => void;
  onSave: (s: EditorState) => void;
  saving: boolean;
}) {
  const [reportType, setReportType] = useState(initial.reportType);
  const [frequency, setFrequency] = useState(initial.frequency);
  const [sendHour, setSendHour] = useState(initial.sendHour);
  const [timezone, setTimezone] = useState(initial.timezone);
  const [channels, setChannels] = useState<Channel[]>(initial.channels);

  // Timezone labels carry a live "current time in zone" suffix, so rebuild
  // the item list whenever ``now`` ticks (keeps <SelectValue/> in sync).
  const tzItems = useMemo(
    () => TIMEZONE_OPTIONS.map((o) => ({ value: o.value, label: timezoneLabelWithTime(o.value, now) })),
    [now],
  );

  function toggleChannel(c: Channel) {
    setChannels((prev) => {
      if (prev.includes(c)) {
        if (prev.length === 1) return prev;
        return prev.filter((x) => x !== c);
      }
      return [...prev, c];
    });
  }

  // In Add mode, hide report types that already have a schedule —
  // saving a duplicate would silently overwrite the existing one (the
  // backend ON CONFLICT clause).  Edit mode keeps the current type
  // visible (it's the user's current row).
  const availableTypes = REPORTS.filter((r) => {
    if (initial.reportTypeLocked) return r.key === reportType;
    return !existingTypes.has(r.key) || r.key === reportType;
  });

  const submitDisabled = saving
    || (channels.includes('email') && !emailEligible)
    || (channels.includes('telegram') && !telegramEligible)
    || availableTypes.length === 0;

  return (
    <Card className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-foreground">
          {initial.reportTypeLocked ? 'Edit schedule' : 'New schedule'}
        </h3>
        <button onClick={onCancel} className="inline-flex items-center justify-center min-w-tap text-muted-foreground hover:text-foreground py-0.5 -my-0.5 min-h-tap">
          <X className="size-4" />
        </button>
      </div>

      <div>
        <label className="block text-sm text-muted-foreground mb-1">Report Type</label>
        <div className="grid grid-cols-2 gap-2">
          {availableTypes.map((r) => (
            <button
              key={r.key}
              type="button"
              onClick={() => setReportType(r.key as ReportKey)}
              disabled={initial.reportTypeLocked}
              className={`px-3 py-2 rounded-lg text-sm text-left transition-colors border ${
                reportType === r.key
                  ? 'bg-primary/15 border-primary text-primary'
                  : 'bg-muted border-border text-muted-foreground hover:text-foreground'
              } ${initial.reportTypeLocked ? 'opacity-70 cursor-not-allowed' : ''}`}
            >
              {r.emoji} {r.labelFull}
            </button>
          ))}
        </div>
      </div>

      <div>
        <label className="block text-sm text-muted-foreground mb-1">Frequency</label>
        <div className="flex gap-2">
          {FREQUENCIES.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFrequency(f)}
              className={`px-4 py-2 rounded-lg text-sm capitalize transition-colors border ${
                frequency === f
                  ? 'bg-primary/15 border-primary text-primary'
                  : 'bg-muted border-border text-muted-foreground hover:text-foreground'
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      <div className="flex gap-4">
        <div className="flex-1">
          <label className="block text-sm text-muted-foreground mb-1">Delivery Hour</label>
          <Select value={String(sendHour)} onValueChange={(v) => setSendHour(Number(v))} items={HOUR_ITEMS}>
            <SelectTrigger className="w-full" aria-label="Delivery Hour"><SelectValue /></SelectTrigger>
            <SelectContent>
              {HOUR_ITEMS.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="flex-1">
          <label className="block text-sm text-muted-foreground mb-1">Timezone</label>
          <Select value={timezone} onValueChange={(v) => setTimezone(v)} items={tzItems}>
            <SelectTrigger className="w-full" aria-label="Timezone"><SelectValue /></SelectTrigger>
            <SelectContent>
              {tzItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div>
        <label className="block text-sm text-muted-foreground mb-1">Deliver via</label>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => telegramEligible && toggleChannel('telegram')}
            disabled={!telegramEligible}
            className={`flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors border ${
              channels.includes('telegram')
                ? 'bg-primary/15 border-primary text-primary'
                : 'bg-muted border-border text-muted-foreground hover:text-foreground'
            } ${!telegramEligible ? 'opacity-40 cursor-not-allowed' : ''}`}
          >
            <MessageSquare className="size-4" />
            Telegram
          </button>
          <button
            type="button"
            onClick={() => emailEligible && toggleChannel('email')}
            disabled={!emailEligible}
            className={`flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors border ${
              channels.includes('email')
                ? 'bg-primary/15 border-primary text-primary'
                : 'bg-muted border-border text-muted-foreground hover:text-foreground'
            } ${!emailEligible ? 'opacity-40 cursor-not-allowed' : ''}`}
          >
            <Mail className="size-4" />
            Email
          </button>
        </div>
        {(telegramHint || emailHint) && (
          <p className="text-xs text-muted-foreground mt-1.5">
            {telegramHint && <span className="flex items-start gap-1.5"><Send className="size-3.5 shrink-0 mt-0.5" aria-hidden />{telegramHint}</span>}
            {emailHint && <span className="flex items-start gap-1.5"><Mail className="size-3.5 shrink-0 mt-0.5" aria-hidden />{emailHint}</span>}
          </p>
        )}
      </div>

      <div className="flex gap-2">
        <button
          onClick={onCancel}
          className="flex-1 py-2 rounded-lg bg-muted hover:bg-muted/80 text-muted-foreground text-sm transition-colors min-h-tap"
        >
          Cancel
        </button>
        <button
          onClick={() => onSave({ reportType, frequency, sendHour, timezone, channels, reportTypeLocked: initial.reportTypeLocked })}
          disabled={submitDisabled}
          className="flex-1 py-2 rounded-lg bg-primary hover:bg-primary/90 text-primary-foreground font-medium text-sm transition-colors disabled:opacity-50"
        >
          {saving ? 'Saving…' : initial.reportTypeLocked ? 'Update Schedule' : 'Save Schedule'}
        </button>
      </div>
    </Card>
  );
}


// ── Page ─────────────────────────────────────────────────────────

export default function ScheduledReports() {
  const { user } = useAuth();
  // Ticks every minute so the "current time in zone X" suffix in the
  // timezone picker stays fresh while the page is open.
  const now = useNow();
  const [rows, setRows] = useState<ScheduledReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  // Editor state: ``null`` = no editor open; otherwise the EditorState
  // (with ``reportTypeLocked`` distinguishing Add from Edit mode).
  const [editor, setEditor] = useState<EditorState | null>(null);

  const emailEligible = useMemo(
    () => Boolean(user?.email && user?.email_verified),
    [user?.email, user?.email_verified],
  );
  const emailHint = useMemo(() => {
    if (!user?.email) return 'Add an email to your profile to enable email delivery';
    if (!user?.email_verified) return 'Verify your email to enable email delivery';
    return '';
  }, [user?.email, user?.email_verified]);
  const telegramEligible = Boolean(user?.telegram_id);
  const telegramHint = telegramEligible
    ? ''
    : 'Link your Telegram account to enable Telegram delivery';

  const existingTypes = useMemo(
    () => new Set(rows.map((r) => r.report_type)),
    [rows],
  );

  // First available report type to pre-select when opening Add mode.
  const firstAvailableType = useMemo(() => {
    const first = REPORTS.find((r) => !existingTypes.has(r.key));
    return first?.key ?? 'faults';
  }, [existingTypes]);

  const canAddMore = REPORTS.some((r) => !existingTypes.has(r.key));

  async function load() {
    setLoading(true);
    try {
      const d = await apiJSON<{ scheduled_reports: ScheduledReport[] }>('/user/scheduled-reports');
      setRows(d.scheduled_reports || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  function openAdd() {
    setError(''); setSuccess('');
    setEditor({
      reportType: firstAvailableType,
      frequency: 'daily',
      sendHour: 7,
      timezone: user?.effective_timezone || 'America/New_York',
      channels: telegramEligible ? ['telegram'] : (emailEligible ? ['email'] : ['telegram']),
      reportTypeLocked: false,
    });
  }

  function openEdit(row: ScheduledReport) {
    setError(''); setSuccess('');
    setEditor({
      reportType: row.report_type,
      frequency: row.frequency,
      sendHour: row.send_hour,
      timezone: row.timezone,
      channels: parseChannels(row.delivery_channels),
      reportTypeLocked: true,
    });
  }

  async function handleSave(s: EditorState) {
    setSaving(true); setError(''); setSuccess('');
    try {
      const d = await apiJSON<{ scheduled_reports: ScheduledReport[] }>('/user/scheduled-reports', {
        method: 'PUT',
        body: {
          frequency: s.frequency,
          report_type: s.reportType,
          send_hour: s.sendHour,
          timezone: s.timezone,
          delivery_channels: s.channels,
        },
      });
      setRows(d.scheduled_reports || []);
      setSuccess('Schedule saved!');
      setEditor(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(row: ScheduledReport) {
    if (!confirm(`Stop the ${REPORT_LABEL[row.report_type] || row.report_type} schedule?`)) return;
    setError(''); setSuccess('');
    try {
      await apiJSON(`/user/scheduled-reports?report_type=${encodeURIComponent(row.report_type)}`, {
        method: 'DELETE',
      });
      setSuccess('Schedule stopped');
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed');
    }
  }

  if (loading) {
    return (
      <div className="max-w-xl">
        <CardSkeleton height="h-48" />
      </div>
    );
  }

  return (
    <div className="max-w-xl">
      {error && <div className="mb-3"><ErrorState message={error} /></div>}
      {success && <p className="text-ok text-sm mb-3">{success}</p>}

      <div className="space-y-2 mb-4">
        {rows.length === 0 && !editor && (
          <div className="bg-muted/50 border border-border rounded-lg p-6 text-center text-sm text-muted-foreground">
            No scheduled reports yet — add one to start receiving recurring deliveries.
          </div>
        )}
        {rows.map((row) => (
          <ScheduleRow
            key={`${row.report_type}-${row.id ?? row.report_type}`}
            row={row}
            onEdit={() => openEdit(row)}
            onDelete={() => handleDelete(row)}
          />
        ))}
      </div>

      {!editor && canAddMore && (
        <button
          onClick={openAdd}
          className="w-full py-3 rounded-lg bg-primary/10 hover:bg-primary/20 text-primary font-medium text-sm transition-colors border border-primary/30 flex items-center justify-center gap-2 min-h-tap"
        >
          <Plus className="size-4" />
          Add schedule
        </button>
      )}
      {!editor && !canAddMore && rows.length > 0 && (
        <p className="text-xs text-muted-foreground text-center mt-2">
          All report types are scheduled. Edit or stop one above to swap.
        </p>
      )}

      {editor && (
        <ScheduleEditor
          initial={editor}
          existingTypes={existingTypes}
          telegramEligible={telegramEligible}
          emailEligible={emailEligible}
          emailHint={emailHint}
          telegramHint={telegramHint}
          now={now}
          onCancel={() => setEditor(null)}
          onSave={handleSave}
          saving={saving}
        />
      )}
    </div>
  );
}
