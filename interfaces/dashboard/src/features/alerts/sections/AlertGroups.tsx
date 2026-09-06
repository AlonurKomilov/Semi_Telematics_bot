/**
 * The board read as SITUATIONS rather than deliveries.
 *
 * Every fire writes its own alert_history row on purpose — the subkey
 * carries a timestamp so each delivered message keeps a unique id, and a
 * dispatcher quoting "Alert #13066" finds exactly that message. Nothing
 * here changes that. This reads the same rows a second way.
 *
 * What per-delivery rows cost the person reading them: 12,970 rows for
 * 1,015 real situations on this account, one truck contributing 354 for
 * a single kind of event. A queue nobody can finish stops being a queue,
 * which is why 85% of it was never acknowledged — not carelessness, an
 * unusable pile. Clearing one truck's following-distance history meant
 * 354 clicks.
 *
 * A SECTION, not a toggle inside the queue. The queue is 747 lines and
 * eight surfaces read its endpoint; this reads its own. Someone who
 * wants it adds it from the page gear and keeps the queue exactly as it
 * was, and turning it off is one click rather than a deploy.
 */
import { useCallback, useEffect, useState } from 'react';
import { toast } from '../../../lib/toast';
import { Layers } from 'lucide-react';

import { apiJSON } from '@/api/client';
import { Card } from '@/components/ui/card';
import DataGrid from '@/components/datagrid';
import type { AnyColumn } from '@/types';
import { Button } from '@/components/ui/button';
import { CardSkeleton, SectionHeader } from '@/components/shell';
import StatusBadge from '@/components/StatusBadge';
import { useTimezone } from '@/hooks/useTimezone';
import { formatRelative } from '@/utils/datetime';

interface AlertGroup {
  alert_type: string;
  vehicle_id: string;
  vehicle_name: string;
  subtype: string;
  deliveries: number;
  occurrences: number;
  first_seen: string;
  last_seen: string;
  unacked: number;
  severity: 'critical' | 'warning' | 'info';
  group_key: string;
}

const WINDOW_DAYS = 7;

export default function AlertGroups() {
  const tz = useTimezone();
  const [groups, setGroups] = useState<AlertGroup[]>([]);
  const [deliveries, setDeliveries] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setFailed(false);
    try {
      const r = await apiJSON<{ groups: AlertGroup[]; deliveries: number }>(
        `/alerts/grouped?days=${WINDOW_DAYS}`);
      setGroups(r.groups || []);
      setDeliveries(r.deliveries || 0);
    } catch {
      setFailed(true);
    } finally {
      setLoaded(true);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const ack = async (g: AlertGroup) => {
    if (busy) return;
    setBusy(g.group_key);
    try {
      // The server resolves which ids this clears from the group's
      // identity — the client never sends a list, so "acknowledge this
      // group" cannot become "acknowledge these ids".
      const r = await apiJSON<{ claimed: number }>('/alerts/grouped/work', {
        method: 'POST',
        body: {
          alert_type: g.alert_type, vehicle_id: g.vehicle_id,
          subtype: g.subtype, days: WINDOW_DAYS,
        },
      });
      toast.success(`You’re on it — ${r.claimed} alert${r.claimed === 1 ? '' : 's'} claimed`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not claim these');
    } finally {
      setBusy(null);
    }
  };

  const columns: AnyColumn[] = [
    {
      key: 'severity', label: 'Severity', sortable: true,
      filterable: true, filterMode: 'select',
      render: (_v, row) => <StatusBadge status={(row as unknown as AlertGroup).severity} />,
      csvValue: (row) => (row as unknown as AlertGroup).severity,
    },
    { key: 'vehicle_name', label: 'Vehicle', sortable: true, filterable: true },
    {
      key: 'alert_type', label: 'Feature', sortable: true,
      filterable: true, filterMode: 'select',
    },
    {
      // The subtype is a KEY for most types (SPN520640,
      // followingDistance) and a whole sentence for a few, so it is a
      // filterable column rather than a chip: on this account it is the
      // difference between one truck's four distinct fault codes.
      key: 'subtype', label: 'Type', sortable: true,
      filterable: true, filterMode: 'select',
    },
    {
      key: 'deliveries', label: 'Alerts', sortable: true, aggregable: true,
      filterable: true, filterMode: 'range',
      render: (_v, row) => {
        const g = row as unknown as AlertGroup;
        return (
          <span className="tabular-nums">
            {g.deliveries}
            {g.unacked > 0 && g.unacked !== g.deliveries && (
              <span className="text-muted-foreground"> · {g.unacked} unclaimed</span>
            )}
          </span>
        );
      },
    },
    {
      key: 'last_seen', label: 'Last seen', sortable: true,
      render: (_v, row) => formatRelative(
        (row as unknown as AlertGroup).last_seen, { timeZone: tz }),
    },
    {
      key: 'ack', label: '', sortable: false,
      render: (_v, row) => {
        const g = row as unknown as AlertGroup;
        if (g.unacked === 0) return <span className="text-muted-foreground">—</span>;
        return (
          <Button size="sm" variant="outline"
                  onClick={() => { void ack(g); }}
                  aria-disabled={busy === g.group_key}
                  aria-busy={busy === g.group_key}>
            Work on these
          </Button>
        );
      },
      csvValue: (row) => String((row as unknown as AlertGroup).unacked),
    },
  ];

  const heading = (
    <SectionHeader size="card" icon={<Layers className="size-4" />} className="mb-1">
      Grouped by situation
    </SectionHeader>
  );

  if (!loaded) {
    return <Card render={<section />}>{heading}<CardSkeleton /></Card>;
  }
  if (failed) {
    return (
      <Card render={<section />}>
        {heading}
        <p className="text-xs text-muted-foreground">
          Couldn’t load the grouped view.{' '}
          <button type="button" onClick={() => void load()}
                  className="text-primary hover:underline min-h-tap">
            Try again
          </button>
        </p>
      </Card>
    );
  }

  return (
    <Card render={<section />}>
      {heading}
      <p className="text-xs text-muted-foreground mb-3">
        {groups.length === 0
          ? `Nothing in the last ${WINDOW_DAYS} days.`
          : `${groups.length} situation${groups.length === 1 ? '' : 's'} `
            + `behind ${deliveries} alert${deliveries === 1 ? '' : 's'} in the `
            + `last ${WINDOW_DAYS} days. Working on one puts your name on `
            + `every alert in it.`}
      </p>

      {groups.length > 0 && (
        <DataGrid
          tableId="alert-groups"
          columns={columns}
          data={groups as unknown as Record<string, unknown>[]}
          searchKey={['vehicle_name', 'alert_type', 'subtype']}
          searchPlaceholder="Search vehicle, type or code…"
        />
      )}
    </Card>
  );
}
