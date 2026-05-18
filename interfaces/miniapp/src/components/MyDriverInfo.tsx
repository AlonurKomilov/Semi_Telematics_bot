/**
 * MyDriverInfo — driver self-view of CDL/medical card/training docs +
 * current vehicle assignment.
 *
 * Renders nothing when the API returns 403/404 (not a driver, or driver
 * record missing) so it composes safely into ProfilePage.  Drivers see
 * their own docs read-only; a "Request re-upload" button pings admins
 * via the bot (the actual upload happens admin-side per the MVP spec).
 */

import { useEffect, useState } from 'react';
import { Section, Cell } from '@telegram-apps/telegram-ui';
import { apiFetch, apiJSON } from '../api/client';
import { haptics } from '../hooks/useTelegram';

interface Doc {
  id: number;
  doc_type: string;
  file_name: string;
  expires_at: string | null;
  status: string;
}

interface Assignment {
  vehicle_name: string;
  is_primary: boolean;
  assigned_at: string;
}

interface MeResponse {
  profile: { user_id: number; display_name: string; cdl_number: string | null; cdl_expires: string | null };
  assignments: Assignment[];
  documents: Doc[];
}

const DOC_LABEL: Record<string, string> = {
  cdl:              'CDL',
  medical_card:     'Medical card',
  mvr:              'MVR',
  drug_screen:      'Drug screen',
  background_check: 'Background check',
  training_cert:    'Training',
  other:            'Document',
};

function daysUntil(iso: string | null): number | null {
  if (!iso) return null;
  const target = new Date(iso + 'T00:00:00');
  if (isNaN(target.getTime())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - today.getTime()) / 86_400_000);
}

function chipFor(iso: string | null): { text: string; color: string } {
  if (!iso) return { text: 'No expiry', color: 'var(--st-muted, #8a8a8e)' };
  const d = daysUntil(iso);
  if (d == null) return { text: iso, color: 'var(--st-muted, #8a8a8e)' };
  if (d < 0)    return { text: `Expired ${-d}d ago`, color: 'var(--st-red, #ff453a)' };
  if (d === 0)  return { text: 'Expires today',      color: 'var(--st-red, #ff453a)' };
  if (d <= 30)  return { text: `${d}d left`,         color: 'var(--st-red, #ff453a)' };
  if (d <= 90)  return { text: `${d}d left`,         color: 'var(--st-orange, #ff9f0a)' };
  return { text: iso, color: 'var(--st-muted, #8a8a8e)' };
}

export function MyDriverInfo({ onToast }: { onToast?: (text: string, kind?: 'success' | 'error' | 'info') => void }) {
  const [data, setData] = useState<MeResponse | null>(null);
  const [hidden, setHidden] = useState(false);
  const [loading, setLoading] = useState(true);
  const [requestingId, setRequestingId] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch('/api/drivers/me')
      .then(async (res) => {
        if (cancelled) return;
        // Hide silently when caller isn't a driver / no driver record —
        // this component composes into ProfilePage which is shared
        // across all roles.
        if (res.status === 403 || res.status === 404) {
          setHidden(true);
          return;
        }
        if (!res.ok) {
          setHidden(true);
          return;
        }
        setData(await res.json() as MeResponse);
      })
      .catch(() => setHidden(true))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (hidden) return null;
  if (loading || !data) return null;

  const primary = data.assignments.find((a) => a.is_primary) ?? data.assignments[0] ?? null;

  const requestReupload = async (docId: number) => {
    haptics.selection();
    setRequestingId(docId);
    try {
      const res = await apiJSON<{ status: string; admins?: number }>(
        `/api/drivers/me/documents/${docId}/request-reupload`,
        { method: 'POST' },
      );
      haptics.success();
      if (res.status === 'no_admins') {
        onToast?.('No admins reachable. Try the dashboard later.', 'info');
      } else {
        onToast?.(`Request sent to ${res.admins} admin${res.admins === 1 ? '' : 's'}.`);
      }
    } catch {
      haptics.error();
      onToast?.('Could not send request. Please try again.', 'error');
    } finally {
      setRequestingId(null);
    }
  };

  return (
    <>
      {/* ── My Vehicle ─────────────────────────────────────── */}
      <Section header="My Vehicle">
        {primary ? (
          <Cell
            subhead={`Assigned ${primary.assigned_at.slice(0, 10)}`}
          >
            Truck #{primary.vehicle_name}
            {primary.is_primary && (
              <span style={{
                marginLeft: 8,
                padding: '2px 6px',
                borderRadius: 4,
                fontSize: 10,
                background: 'var(--tg-theme-button-color, #0a84ff)',
                color: 'var(--tg-theme-button-text-color, #fff)',
              }}>
                Primary
              </span>
            )}
          </Cell>
        ) : (
          <Cell subhead="Ask your admin to assign a vehicle.">
            Not assigned
          </Cell>
        )}
      </Section>

      {/* ── My Documents ────────────────────────────────────── */}
      <Section
        header="My Documents"
        footer={
          data.documents.length === 0
            ? 'No documents on file yet. Your admin uploads CDL, medical card, and training certificates.'
            : 'Drivers can\'t upload directly — tap a doc to ask your admin for a renewal.'
        }
      >
        {data.documents.length === 0 ? null : data.documents.map((d) => {
          const chip = chipFor(d.expires_at);
          return (
            <Cell
              key={d.id}
              subhead={d.expires_at ? `Expires ${d.expires_at}` : 'No expiration date'}
              after={
                <button
                  onClick={() => requestReupload(d.id)}
                  disabled={requestingId === d.id}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: 'var(--tg-theme-link-color, #0a84ff)',
                    fontSize: 13,
                    cursor: 'pointer',
                    padding: '4px 6px',
                  }}
                >
                  {requestingId === d.id ? 'Sending…' : 'Renew'}
                </button>
              }
            >
              <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span>{DOC_LABEL[d.doc_type] ?? d.doc_type}</span>
                <span
                  style={{
                    fontSize: 11,
                    padding: '2px 6px',
                    borderRadius: 4,
                    border: `1px solid ${chip.color}`,
                    color: chip.color,
                  }}
                >
                  {chip.text}
                </span>
              </span>
            </Cell>
          );
        })}
      </Section>
    </>
  );
}
