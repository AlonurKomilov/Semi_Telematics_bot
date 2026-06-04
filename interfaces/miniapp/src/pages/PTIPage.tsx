import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Placeholder, Spinner } from '@telegram-apps/telegram-ui';
import { Icon28ClockOutline } from '@vkontakte/icons';
import { apiJSON } from '../api/client';
import { haptics } from '../hooks/useTelegram';
import type { PTIInspection } from '../types';
import { PTIWizard } from '../components/PTIWizard';

/**
 * Driver-facing PTI hub.
 *
 * Single endpoint drives the page:
 *   GET /api/inspections/me/current → { inspection: PTIInspection | null }
 *
 * The render branches on ``inspection.status`` and ``review_status``:
 *   - null                              → no PTI due
 *   - 'scheduled'                       → start button
 *   - 'in_progress' / 'revision_required' → wizard
 *   - 'submitted'                       → awaiting-review summary
 *   - 'reviewed' + review_status        → outcome card
 *
 * Submissions and item updates land back through the same endpoint
 * (re-fetched after every mutation in the wizard).
 */


function formatDue(due: string | null): string {
  if (!due) return '';
  const d = new Date(due);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString(undefined, {
    weekday: 'short',
    month:   'short',
    day:     'numeric',
  });
}


export function PTIPage({ active }: { active: boolean }) {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [inspection, setInspection] = useState<PTIInspection | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await apiJSON<{ inspection: PTIInspection | null }>(
        '/api/inspections/me/current',
      );
      setInspection(data.inspection);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('pti.load_failed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (active) {
      load();
    }
  }, [active, load]);

  // ── Loading ────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="centered" style={{ paddingTop: 64 }}>
        <Spinner size="m" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="centered" style={{ padding: 24 }}>
        <Placeholder
          header={t('pti.load_failed')}
          description={error}
          action={
            <button type="button" className="btn" onClick={() => { haptics.selection(); load(); }}>
              {t('common.retry')}
            </button>
          }
        />
      </div>
    );
  }

  // ── No inspection due ──────────────────────────────────────────
  if (!inspection) {
    return (
      <div className="centered" style={{ padding: 24 }}>
        <Placeholder
          header={t('pti.empty_title')}
          description={t('pti.empty_description')}
        >
          <Icon28ClockOutline width={48} height={48} />
        </Placeholder>
      </div>
    );
  }

  // ── Submitted → awaiting fleet review ─────────────────────────
  if (inspection.status === 'submitted') {
    return (
      <div style={{ padding: 16 }}>
        <div className="pti-status-card">
          <div className="pti-status-card__chip pti-status-card__chip--info">
            ⏳ {t('pti.status_awaiting_review')}
          </div>
          <div className="pti-status-card__title">
            {t('pti.submitted_for_vehicle', { vehicle: inspection.vehicle_name })}
          </div>
          <div className="pti-status-card__sub">
            {t('pti.submitted_at', {
              when: inspection.submitted_at
                ? new Date(inspection.submitted_at).toLocaleString()
                : '—',
            })}
          </div>
          {inspection.defects_count > 0 && (
            <div className="pti-status-card__sub">
              {t('pti.defects_reported', { count: inspection.defects_count })}
              {inspection.has_oos_defect ? ` · 🛑 ${t('pti.oos_flag')}` : ''}
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── Reviewed → outcome card ───────────────────────────────────
  if (inspection.status === 'reviewed') {
    const rs = inspection.review_status;
    const chipClass =
      rs === 'approved'      ? 'pti-status-card__chip--success' :
      rs === 'needs_service' ? 'pti-status-card__chip--warn' :
                               'pti-status-card__chip--danger';
    const emoji =
      rs === 'approved'      ? '✅' :
      rs === 'needs_service' ? '🔧' :
                               '🛑';
    return (
      <div style={{ padding: 16 }}>
        <div className="pti-status-card">
          <div className={`pti-status-card__chip ${chipClass}`}>
            {emoji} {t(`pti.review_status.${rs || 'reviewed'}`)}
          </div>
          <div className="pti-status-card__title">
            {t('pti.submitted_for_vehicle', { vehicle: inspection.vehicle_name })}
          </div>
          {inspection.review_notes && (
            <div className="pti-status-card__notes">
              {t('pti.fleet_notes')}: {inspection.review_notes}
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── Revision required → wizard with the same row, plus banner ─
  if (inspection.status === 'revision_required') {
    return (
      <div style={{ padding: 12 }}>
        <div className="pti-banner pti-banner--warn">
          ↻ {t('pti.revision_banner')}
          {inspection.review_notes && (
            <div className="pti-banner__notes">{inspection.review_notes}</div>
          )}
        </div>
        <PTIWizard inspection={inspection} onUpdated={load} onSubmitted={load} />
      </div>
    );
  }

  // ── Scheduled → start screen ──────────────────────────────────
  if (inspection.status === 'scheduled') {
    const dueLabel = formatDue(inspection.due_by);
    return (
      <div style={{ padding: 16 }}>
        <div className="pti-start">
          <div className="pti-start__title">
            {t('pti.weekly_for_vehicle', { vehicle: inspection.vehicle_name })}
          </div>
          {dueLabel && (
            <div className="pti-start__due">
              📅 {t('pti.due_by_label')}: <strong>{dueLabel}</strong>
            </div>
          )}
          <div className="pti-start__count">
            {t('pti.items_count', { count: inspection.items?.length ?? 0 })}
          </div>
          <button
            type="button"
            className="pti-start__btn"
            onClick={() => {
              haptics.medium();
              // The wizard auto-flips status to in_progress on the
              // first item touch — no explicit "start" API call.
              // Force a re-render by manually setting status here.
              setInspection({ ...inspection, status: 'in_progress' });
            }}
          >
            ▶ {t('pti.start_inspection')}
          </button>
        </div>
      </div>
    );
  }

  // ── In progress → wizard ──────────────────────────────────────
  return (
    <div style={{ padding: 12 }}>
      <PTIWizard inspection={inspection} onUpdated={load} onSubmitted={load} />
    </div>
  );
}
