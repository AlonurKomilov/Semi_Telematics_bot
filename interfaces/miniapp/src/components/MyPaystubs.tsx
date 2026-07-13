/**
 * MyPaystubs — driver self-service paystub history card.
 *
 * Fetches /driver-pay/me; renders a compact card with the most recent
 * paystub (period, status, base, bonuses, total) plus a collapsed
 * history of prior periods.  Hides itself silently if the API returns
 * 403 (permission denied) or 404, so the same component can render on
 * any miniapp page without explicit permission gating in the parent.
 */

import { useEffect, useState } from 'react';
import { Section, Cell, Placeholder, Spinner } from '@telegram-apps/telegram-ui';
import { apiFetch } from '../api/client';

interface BreakdownEntry {
  rule_id: number;
  name: string;
  kind: string;
  amount_cents: number;
  detail?: string;
}

interface PaystubItem {
  id: number;
  driver_id: string;
  driver_name: string;
  period_start: string;
  period_end: string;
  status: string;
  base_pay_cents: number;
  bonus_total_cents: number;
  total_cents: number;
  breakdown: BreakdownEntry[];
}

interface MeResponse {
  driver_id: string | null;
  items: PaystubItem[];
}

const fmtCents = (c: number | null | undefined) =>
  c == null ? '$0.00' :
    `$${(c / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function MyPaystubs() {
  const [data, setData] = useState<MeResponse | null>(null);
  const [hidden, setHidden] = useState(false);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiFetch('/api/driver-pay/me?limit=12')
      .then(async (res) => {
        if (cancelled) return;
        if (res.status === 403 || res.status === 404) {
          setHidden(true);
          return;
        }
        if (!res.ok) {
          setHidden(true);
          return;
        }
        const j = await res.json() as MeResponse;
        setData(j);
      })
      .catch(() => setHidden(true))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (hidden) return null;
  if (loading) {
    return (
      <Section header="My Paystubs">
        <Cell><Spinner size="m" /></Cell>
      </Section>
    );
  }
  if (!data || !data.items.length) {
    return (
      <Section header="My Paystubs">
        <Placeholder description="No paystubs yet" />
      </Section>
    );
  }

  const latest = data.items[0];
  const rest = data.items.slice(1);

  return (
    <Section header="My Paystubs">
      <Cell
        subtitle={`${latest.period_start} → ${latest.period_end} · ${latest.status.toUpperCase()}`}
        after={<strong>{fmtCents(latest.total_cents)}</strong>}
      >
        Base {fmtCents(latest.base_pay_cents)} · Bonuses {fmtCents(latest.bonus_total_cents)}
      </Cell>
      {(latest.breakdown || []).map((b, i) => (
        <Cell
          key={i}
          subtitle={b.detail || b.kind}
          after={fmtCents(b.amount_cents)}
        >
          {b.name}
        </Cell>
      ))}
      {rest.length > 0 && (
        <Cell onClick={() => setExpanded((x) => !x)}>
          {expanded ? 'Hide history' : `Show ${rest.length} earlier paystub${rest.length > 1 ? 's' : ''}`}
        </Cell>
      )}
      {expanded && rest.map((it) => (
        <Cell
          key={it.id}
          subtitle={`${it.period_start} → ${it.period_end} · ${it.status}`}
          after={fmtCents(it.total_cents)}
        >
          Paystub
        </Cell>
      ))}
    </Section>
  );
}

export default MyPaystubs;
