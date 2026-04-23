import { useEffect, useState } from 'react';
import { Button, Cell, List, Placeholder, Section, Spinner } from '@telegram-apps/telegram-ui';
import { apiFetch, apiJSON } from '../api/client';
import type { Alert } from '../types';

export function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [acking, setAcking] = useState<Set<number>>(new Set());

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setError(false);
    try {
      const data = await apiJSON<{ alerts: Alert[] }>('/api/alerts/pending');
      setAlerts(data.alerts ?? []);
    } catch (e) {
      console.error('Failed to load alerts:', e);
      setError(true);
    } finally {
      setLoading(false);
    }
  }

  async function acknowledge(id: number) {
    setAcking(prev => new Set(prev).add(id));
    try {
      await apiFetch(`/api/alerts/${id}/acknowledge`, { method: 'POST' });
      // Optimistically remove from list
      setAlerts(prev => prev.filter(a => a.id !== id));
    } catch (e) {
      console.error('Failed to acknowledge alert:', e);
    } finally {
      setAcking(prev => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  // ── States ────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="centered">
        <Spinner size="l" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="centered">
        <Placeholder header="Failed to Load" description="Could not fetch pending alerts. Please try again.">
          ⚠️
        </Placeholder>
      </div>
    );
  }

  if (alerts.length === 0) {
    return (
      <div className="centered">
        <Placeholder header="All Clear" description="No pending alerts — your fleet is running smoothly.">
          ✅
        </Placeholder>
      </div>
    );
  }

  // ── Render ────────────────────────────────────────────────────────

  return (
    <div style={{ paddingBottom: 8 }}>
      <List>
        <Section header={`${alerts.length} pending alert${alerts.length !== 1 ? 's' : ''}`}>
          {alerts.map(alert => (
            <Cell
              key={alert.id}
              subtitle={alert.message || undefined}
              before={<span style={{ fontSize: 20 }}>🔔</span>}
              after={
                <Button
                  size="s"
                  mode="outline"
                  loading={acking.has(alert.id)}
                  disabled={acking.has(alert.id)}
                  onClick={e => {
                    e.stopPropagation();
                    acknowledge(alert.id);
                  }}
                >
                  Ack
                </Button>
              }
            >
              {alert.vehicle_name || alert.alert_key || 'Alert'}
            </Cell>
          ))}
        </Section>
      </List>
    </div>
  );
}
