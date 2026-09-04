import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon24CheckCircleOutline } from '@vkontakte/icons';
import { apiJSON } from '../api/client';
import type { PTIInspection } from '../types';
import { haptics } from '../hooks/useTelegram';

/**
 * Floating "PTI due" chip rendered on the driver's map landing.
 *
 * Shows when the driver has an open inspection (scheduled, in-progress,
 * or returned for revision).  Tap routes to the PTI tab so the driver
 * doesn't have to hunt for it in the bottom-nav.  Hidden completely
 * for drivers without the ``can_view_inspections`` permission and for
 * drivers with no current inspection.
 */

interface Props {
  userPerms: Record<string, boolean>;
  onTap: () => void;
}

function _relativeDay(iso: string | null): { label: string; overdue: boolean } {
  if (!iso) return { label: '', overdue: false };
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return { label: '', overdue: false };
  const now = new Date();
  const dayMs = 24 * 60 * 60 * 1000;
  const diffDays = Math.floor((d.getTime() - now.getTime()) / dayMs);
  if (diffDays < 0) return { label: '', overdue: true };
  if (diffDays === 0) return { label: 'today', overdue: false };
  if (diffDays === 1) return { label: 'tomorrow', overdue: false };
  if (diffDays < 7)   return { label: d.toLocaleDateString(undefined, { weekday: 'long' }), overdue: false };
  return { label: d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }), overdue: false };
}

export function PTIChip({ userPerms, onTap }: Props) {
  const { t } = useTranslation();
  const [ins, setIns] = useState<PTIInspection | null>(null);
  const [loaded, setLoaded] = useState(false);

  const enabled = !!userPerms.can_view_inspections;

  useEffect(() => {
    if (!enabled) {
      setLoaded(true);
      return;
    }
    let cancelled = false;
    apiJSON<{ inspection: PTIInspection | null }>('/api/inspections/me/current')
      .then(r => { if (!cancelled) setIns(r.inspection); })
      .catch(() => { /* non-fatal — chip just stays hidden */ })
      .finally(() => { if (!cancelled) setLoaded(true); });
    return () => { cancelled = true; };
  }, [enabled]);

  if (!enabled || !loaded || !ins) return null;
  // Hide once submitted or reviewed — those states are terminal for
  // the driver; the chip is only useful while there's work to do.
  if (ins.status === 'submitted' || ins.status === 'reviewed') return null;

  const rel = _relativeDay(ins.due_by);
  const itemCount = ins.items?.length ?? 0;

  let label: string;
  let tone: 'normal' | 'overdue' | 'revision' = 'normal';
  if (ins.status === 'revision_required') {
    label = t('pti.chip_revision');
    tone = 'revision';
  } else if (rel.overdue) {
    label = t('pti.chip_overdue');
    tone = 'overdue';
  } else if (ins.status === 'in_progress') {
    label = t('pti.chip_in_progress');
  } else {
    label = t('pti.chip_due', { when: rel.label || '—' });
  }

  // Tone → background colour (uses existing CSS vars).  Default is
  // brand-blue, overdue is red, revision is orange.
  const bg =
    tone === 'overdue'  ? 'var(--st-red)'
    : tone === 'revision' ? 'var(--st-orange)'
    :                       'var(--tg-theme-button-color, var(--st-blue))';

  return (
    <button
      type="button"
      onClick={() => { haptics.selection(); onTap(); }}
      className="pti-chip"
      style={{
        position: 'absolute',
        left: '50%',
        transform: 'translateX(-50%)',
        // Sit above the map-status-card.  Card base 14px + card height
        // ~92px + gap 8px = 114px.  Mini app has bottom safe-area
        // padding handled at the page root, so the absolute offset is
        // in app-space.
        bottom: 'calc(14px + 92px + 8px)',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        padding: '8px 14px',
        borderRadius: 999,
        border: 'none',
        background: bg,
        color: '#fff',
        fontSize: 'var(--st-text-md)',
        fontWeight: 600,
        boxShadow: '0 4px 12px rgba(0,0,0,0.18)',
        cursor: 'pointer',
        zIndex: 600,
        maxWidth: '92%',
      }}
      aria-label={label}
    >
      <Icon24CheckCircleOutline width={16} height={16} />
      <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {label}
        {itemCount > 0 && (
          <span style={{ opacity: 0.85, fontWeight: 500 }}>
            {' · '}{t('pti.chip_items', { count: itemCount })}
          </span>
        )}
      </span>
    </button>
  );
}
