/**
 * Alerts → Triggers.  Both halves of one question in one place: the
 * thresholds you set, and what they have caught.
 *
 * This lives under Alerts rather than under notification preferences, and
 * the distinction is the reason: the notifications page decides WHERE a
 * notice reaches you — email, push, the bot — while a fired trigger is an
 * alert RECORD, and records are read here, in the board's own column
 * vocabulary.
 *
 * What it deliberately does NOT do is add to the board.  The board is the
 * account's shared queue and one person's threshold is not the account's
 * news, so a firing writes only the personal notice this tab reads back.
 * Nobody else's count moves when you set one.
 */
import { useCallback, useEffect, useState } from 'react';
import { BellRing } from 'lucide-react';
import { apiJSON } from '@/api/client';
import { PageHeader, CardSkeleton } from '@/components/shell';
import EmptyState from '@/components/shell/EmptyState';
import DataGrid from '@/components/datagrid';
import type { AnyColumn } from '@/types';
import { useTimezone } from '@/hooks/useTimezone';
import { formatDate } from '@/utils/datetime';
import { AlertsTabs } from './AlertsTabs';
import { useInboxActions } from './useInbox';
import AlertTriggersSection from './AlertTriggersSection';

/** One firing, already shaped as a firing by ``/alerts/triggers/fired``
 *  — the API reads the notice so this file never parses a sentence. */
interface Fired {
  id: number;
  trigger_id: number | null;
  vehicle: string;
  metric: string;
  metric_label: string;
  unit: string;
  threshold: number | null;
  value: number | null;
  says: string;
  severity: string;
  created_at: string;
  read: boolean;
}

export default function MyTriggers() {
  const tz = useTimezone();
  // The bell's own writer, not a second one: a firing marked read here
  // has to stop counting in the bell too, and two paths to one read-state
  // is how a badge ends up disagreeing with the list under it.
  const { markRead } = useInboxActions();
  const [fired, setFired] = useState<Fired[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    setFailed(false);
    try {
      const r = await apiJSON<{ fired: Fired[] }>('/alerts/triggers/fired?limit=100');
      setFired(r.fired || []);
    } catch {
      setFailed(true);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // Reading a firing here is reading it, full stop.  A Status column that
  // says "New" on a row you are looking at, and can only be changed from
  // a different surface, is a status the page cannot honour.
  const open = (row: Fired) => {
    if (row.read) return;
    setFired((cur) => cur.map((f) => (f.id === row.id ? { ...f, read: true } : f)));
    void markRead(row.id);
  };

  const columns: AnyColumn[] = [
    {
      key: 'id', label: 'Alert', sortable: true,
      render: (_v, row) => (
        <span className="text-muted-foreground tabular-nums">
          #{(row as unknown as Fired).id}
        </span>
      ),
    },
    { key: 'vehicle', label: 'Vehicle', sortable: true, filterable: true },
    {
      // The FEATURE is the trigger, not the vehicle: this column answers
      // "what KIND of thing spoke", and on this tab the answer is always
      // "something I set".  It is present anyway so the row reads with
      // the same grammar as a board row.
      key: 'feature', label: 'Feature', sortable: false,
      render: () => <span>Trigger</span>,
      csvValue: () => 'Trigger',
    },
    {
      key: 'metric_label', label: 'Type', sortable: true, filterable: true,
      filterMode: 'select',
    },
    {
      key: 'says', label: 'Description', sortable: false,
      render: (_v, row) => {
        const f = row as unknown as Fired;
        return (
          <span>
            {f.says}
            {f.value !== null && f.value !== undefined && (
              <span className="text-muted-foreground"> — now {f.value}{f.unit}</span>
            )}
          </span>
        );
      },
      csvValue: (row) => {
        const f = row as unknown as Fired;
        return f.value === null || f.value === undefined
          ? f.says : `${f.says} — now ${f.value}${f.unit}`;
      },
    },
    {
      key: 'created_at', label: 'Time', sortable: true,
      render: (_v, row) => formatDate((row as unknown as Fired).created_at, { timeZone: tz }),
    },
    {
      key: 'read', label: 'Status', sortable: true,
      render: (_v, row) => (
        (row as unknown as Fired).read
          ? <span className="text-muted-foreground">Seen</span>
          : <span className="text-foreground font-medium">New</span>
      ),
      csvValue: (row) => ((row as unknown as Fired).read ? 'Seen' : 'New'),
    },
  ];

  return (
    <div>
      <AlertsTabs />
      <PageHeader
        icon={BellRing}
        title="My triggers"
        description={
          'Thresholds you set, and what they have caught. These are yours: '
          + 'they never post to the shared Alerts board, so nobody else has '
          + 'to triage them.'
        }
      />

      <AlertTriggersSection onChanged={() => { void load(); }} />

      {/* No heading of its own: nowhere in this app does a SectionHeader
          sit above an un-wrapped DataGrid — a titled grid goes INSIDE a
          card (ServiceTaskDetail) and a page's main grid is named by its
          PageHeader (Parking, Applications).  This is the second, so the
          page title and the description above name it. */}
      <div className="mt-6">
        {!loaded ? (
          <CardSkeleton />
        ) : failed ? (
          <EmptyState
            icon={BellRing}
            title="Couldn’t load what your triggers caught"
            description="The list is unavailable right now — your triggers themselves are unaffected and still running."
          />
        ) : fired.length === 0 ? (
          <EmptyState
            icon={BellRing}
            title="Nothing caught yet"
            description={
              'A trigger fires on the CROSSING — a truck already past your '
              + 'number when you saved it stays quiet, and the next one to '
              + 'cross is the one you hear about.'
            }
          />
        ) : (
          <DataGrid
            columns={columns}
            data={fired as unknown as Record<string, unknown>[]}
            tableId="alert-triggers-fired"
            onRowClick={(row) => open(row as unknown as Fired)}
            searchKey={['vehicle', 'says', 'metric_label']}
            searchPlaceholder="Search what your triggers caught…"
          />
        )}
      </div>
    </div>
  );
}
