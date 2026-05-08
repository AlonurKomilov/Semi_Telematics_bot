/**
 * MyCoaching — driver self-service coaching assignments card.
 *
 * Fetches /coaching/me?status=pending; renders pending coaching items
 * with an Acknowledge button per item.  Hides itself silently on
 * 403/404 so the same component can render on any page without
 * explicit permission gating in the parent.
 */

import { useEffect, useState } from 'react';
import { Section, Cell, Placeholder, Spinner, Button } from '@telegram-apps/telegram-ui';
import { apiFetch } from '../api/client';

interface CoachingAssignment {
  id: number;
  driver_id: string;
  rule_id: number | null;
  topic_key: string;
  severity: 'low' | 'medium' | 'high';
  reason: string;
  status: string;
  assigned_at: string;
}

const SEV_ICON: Record<string, string> = {
  low: '🟢',
  medium: '🟡',
  high: '🔴',
};

export function MyCoaching() {
  const [items, setItems] = useState<CoachingAssignment[] | null>(null);
  const [hidden, setHidden] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = () => {
    apiFetch('/api/coaching/me?status=pending')
      .then(async (res) => {
        if (res.status === 403 || res.status === 404) {
          setHidden(true);
          return;
        }
        if (!res.ok) {
          setHidden(true);
          return;
        }
        const j = await res.json() as CoachingAssignment[];
        setItems(Array.isArray(j) ? j : []);
      })
      .catch(() => setHidden(true))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const onAck = async (id: number) => {
    setBusyId(id);
    try {
      const res = await apiFetch(`/api/coaching/me/${id}/ack`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (res.ok) {
        setItems((prev) => (prev ? prev.filter((a) => a.id !== id) : prev));
      }
    } finally {
      setBusyId(null);
    }
  };

  if (hidden) return null;
  if (loading) {
    return (
      <Section header="My Coaching">
        <Cell><Spinner size="m" /></Cell>
      </Section>
    );
  }
  if (!items || items.length === 0) {
    return (
      <Section header="My Coaching">
        <Placeholder description="No pending coaching" />
      </Section>
    );
  }

  return (
    <Section header="My Coaching">
      {items.map((a) => (
        <Cell
          key={a.id}
          subtitle={a.reason || a.assigned_at}
          before={<span>{SEV_ICON[a.severity] || '🟡'}</span>}
          after={
            <Button
              size="s"
              mode="outline"
              loading={busyId === a.id}
              onClick={() => onAck(a.id)}
            >
              Acknowledge
            </Button>
          }
        >
          {a.topic_key}
        </Cell>
      ))}
    </Section>
  );
}

export default MyCoaching;
