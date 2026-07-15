import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { useWorkOrderBridge } from '../work-orders/useWorkOrderBridge';
import { toast } from 'sonner';
import { X, BellRing, MapPin, FileDown } from 'lucide-react';
import { apiJSON, apiFetch } from '../../api/client';
import { toneClasses, toneText } from '../../lib/status';
import { ErrorState } from '../../components/shell';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate, formatTime } from '../../utils/datetime';
import type {
  PTIInspectionDetail,
  PTIInspectionItem,
} from '../../types';
import { MediaGallery } from './MediaGallery';
import { StatusTimeline } from './StatusTimeline';
import { SignaturesPanel } from './SignaturesPanel';
import { AIReviewRollup } from './AIReviewRollup';
import { parseVerdict, isFlagged, VERDICT_EMOJI, verdictClasses } from './aiVerdict';

interface Props {
  inspectionId: number;
  onClose: () => void;
  onReviewed: () => void;
  /** Called after a successful resend so the parent can refresh the list. */
  onResent?: () => void;
}

const ITEM_STATUS_EMOJI: Record<string, string> = {
  pending: '○', ok: '✅', minor: '🟡', major: '🟠', oos: '🛑', na: '➖',
};

const ITEM_STATUS_LABEL: Record<string, string> = {
  pending: 'Pending', ok: 'OK', minor: 'Minor', major: 'Major', oos: 'OOS', na: 'N/A',
};

const REVIEW_OPTIONS: Array<{ value: 'approved' | 'needs_service' | 'rejected' | 'revision_required'; label: string; }> = [
  { value: 'approved',          label: 'Approve' },
  { value: 'needs_service',     label: 'Needs Service' },
  { value: 'rejected',          label: 'Reject' },
  { value: 'revision_required', label: 'Revision Required' },
];


function _formatTs(iso: string | null, tz?: string): string {
  return formatDate(iso, { timeZone: tz });
}


// Defect statuses (mirror of DEFECT_ITEM_STATUSES in
// features/inspections/templates.py) — the three item states that
// warrant a repair: 'oos' (out-of-service) is severe, 'minor'/'major'
// are drivable defects.  'ok' / 'na' / 'pending' are NOT defects.
const DEFECT_STATUSES = new Set(['minor', 'major', 'oos']);

export function InspectionDetail({ inspectionId, onClose, onReviewed, onResent }: Props) {
  const { t } = useTranslation();
  const { has } = useViewPermissions();
  const canCreateWorkOrder = has('can_maintenance_all');
  const { createFrom, bridgeDialog } = useWorkOrderBridge();
  const qc = useQueryClient();
  const tz = useTimezone();
  const [tab, setTab] = useState<'items' | 'media'>('items');
  const [reviewStatus, setReviewStatus] = useState<'approved' | 'needs_service' | 'rejected' | 'revision_required'>('approved');
  const [reviewNotes, setReviewNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [resending, setResending] = useState(false);
  const [downloading, setDownloading] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ['pti-inspection', inspectionId],
    queryFn: () => apiJSON<PTIInspectionDetail>(`/inspections/${inspectionId}`),
  });

  const submitReview = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await apiJSON(`/inspections/${inspectionId}/review`, {
        method: 'POST',
        body: { review_status: reviewStatus, review_notes: reviewNotes },
      });
      toast.success(t('inspections.review_saved'));
      onReviewed();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('inspections.review_failed'));
    } finally {
      setSubmitting(false);
    }
  };

  const resendReminder = async () => {
    if (resending) return;
    setResending(true);
    try {
      const res = await apiJSON<{ sent: boolean; throttled_until: string | null }>(
        `/inspections/${inspectionId}/remind`,
        { method: 'POST' },
      );
      if (res.sent) {
        toast.success(t('inspections.remind_sent'));
        onResent?.();
      } else if (res.throttled_until) {
        // Cooldown — surface when it'll unlock so the fleet doesn't
        // keep tapping.
        const when = formatTime(res.throttled_until, { timeZone: tz });
        toast.warning(t('inspections.remind_throttled', { time: when }));
      } else {
        toast.error(t('inspections.remind_failed'));
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('inspections.remind_failed'));
    } finally {
      setResending(false);
    }
  };

  // Download the single-file PDF report.  The browser's native
  // download can't carry our Bearer token, so we fetch the blob with
  // auth, then trigger a synthetic <a download> click (same pattern as
  // the media gallery).
  const downloadReport = async () => {
    if (downloading) return;
    setDownloading(true);
    try {
      const res = await apiFetch(`/inspections/${inspectionId}/report.pdf`);
      if (!res.ok) throw new Error(`Report failed (${res.status})`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `PTI_${data?.vehicle_name ?? 'report'}_${inspectionId}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('inspections.report_failed'));
    } finally {
      setDownloading(false);
    }
  };

  const ins = data;
  const items: PTIInspectionItem[] = ins?.items ?? [];
  const canReview = ins?.status === 'submitted';
  const alreadyReviewed = ins?.status === 'reviewed' && ins?.review_status;

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex justify-end" onClick={onClose}>
      <div
        className="w-[560px] max-w-full bg-card border-l border-border overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="sticky top-0 bg-card border-b border-border px-5 py-3 flex items-center justify-between z-10">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold truncate">
              {ins ? (
                <>Vehicle {ins.vehicle_name}
                  <span className="text-muted-foreground font-normal"> · {ins.inspection_type}</span>
                </>
              ) : 'Inspection'}
            </h2>
            {ins?.submitted_at && (
              <p className="text-xs text-muted-foreground">
                {ins.driver_name
                  ? `Submitted by ${ins.driver_name} · ${_formatTs(ins.submitted_at, tz)}`
                  : `Submitted ${_formatTs(ins.submitted_at, tz)}`}
              </p>
            )}
          </div>
          <div className="flex items-center gap-1 flex-shrink-0">
            {ins && (
              <button
                onClick={downloadReport}
                disabled={downloading}
                title={t('inspections.download_report')}
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-md border border-border hover:bg-muted disabled:opacity-50"
              >
                <FileDown size={14} />
                {downloading ? t('common.loading') : t('inspections.download_report')}
              </button>
            )}
            <button
              onClick={onClose}
              aria-label="Close"
              className="text-muted-foreground hover:text-foreground p-1"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {isLoading && !data && (
          <div className="p-5 text-sm text-muted-foreground">Loading…</div>
        )}

        {error && (
          <div className="p-5">
            <ErrorState message={error instanceof Error ? error.message : 'Load failed'} />
          </div>
        )}

        {ins && (
          <>
            {/* Status timeline — Assigned → Submitted → Reviewed.
                Lives above the summary stats so the reviewer sees the
                lifecycle context before drilling into defect counts. */}
            <StatusTimeline inspection={ins} />

            {/* AI vision roll-up — summarizes the per-photo verdicts
                the driver's Mini App captured + highlights any photo
                the AI flagged on an item the driver marked OK. */}
            <AIReviewRollup inspection={ins} />

            {/* Summary */}
            <div className="px-5 py-4 grid grid-cols-3 gap-3 border-b border-border">
              <div className="bg-muted/40 rounded p-3">
                <p className="text-xs text-muted-foreground">Defects</p>
                <p className="text-xl font-bold tabular-nums">
                  {ins.defects_count}
                  {ins.has_oos_defect ? (
                    <span className={`ml-2 text-xs font-medium px-1.5 py-0.5 rounded-md border ${toneClasses('danger')}`}>
                      OOS
                    </span>
                  ) : null}
                </p>
              </div>
              <div className="bg-muted/40 rounded p-3">
                <p className="text-xs text-muted-foreground">Items</p>
                <p className="text-xl font-bold tabular-nums">{items.length}</p>
              </div>
              <div className="bg-muted/40 rounded p-3">
                <p className="text-xs text-muted-foreground">Media</p>
                <p className="text-xl font-bold tabular-nums">{ins.media?.length ?? 0}</p>
              </div>
            </div>

            {/* Captured location — where the inspection happened.
                'vehicle' = telematics position, 'device' = driver's
                phone GPS fallback. */}
            {ins.location_lat != null && ins.location_lon != null && (
              <div className="px-5 py-2.5 border-b border-border flex items-center gap-2 text-xs">
                <MapPin size={14} className="text-muted-foreground flex-shrink-0" />
                <a
                  href={`https://www.google.com/maps/search/?api=1&query=${ins.location_lat},${ins.location_lon}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary hover:underline font-mono"
                >
                  {ins.location_lat.toFixed(5)}, {ins.location_lon.toFixed(5)}
                </a>
                <span className="text-muted-foreground">
                  · {ins.location_source === 'vehicle'
                    ? t('inspections.location_vehicle')
                    : t('inspections.location_device')}
                </span>
              </div>
            )}

            {/* Tabs */}
            <div className="px-5 pt-3 border-b border-border">
              <div className="inline-flex gap-1 mb-1">
                {(['items', 'media'] as const).map(k => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => setTab(k)}
                    className={`px-3 py-1.5 text-sm rounded-md ${tab === k ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'}`}
                  >
                    {t(`inspections.tab_${k}`)}
                  </button>
                ))}
              </div>
            </div>

            {/* Body */}
            <div className="p-5 space-y-3">
              {tab === 'items' && (
                <ul className="space-y-2">
                  {items.map(it => {
                    const itemMedia = ins.media?.filter(m => m.item_id === it.id) ?? [];
                    // Surface the strongest AI verdict among this item's
                    // photos (flagged outranks ok/unclear).
                    const verdicts = itemMedia
                      .map(parseVerdict)
                      .filter((v): v is NonNullable<typeof v> => v != null);
                    const topVerdict =
                      verdicts.find(isFlagged) ?? verdicts[0] ?? null;
                    return (
                      <li
                        key={it.id}
                        className="border border-border rounded-md p-3"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <p className="text-sm font-medium">{it.label}</p>
                            <p className="text-xs text-muted-foreground capitalize">{it.category}</p>
                          </div>
                          <span className="text-xs whitespace-nowrap">
                            {ITEM_STATUS_EMOJI[it.status] ?? '○'} {ITEM_STATUS_LABEL[it.status] ?? it.status}
                          </span>
                        </div>
                        {it.notes && (
                          <p className="text-xs mt-2 bg-muted/40 rounded px-2 py-1.5">{it.notes}</p>
                        )}
                        {topVerdict && (
                          <div className={`text-xs mt-2 rounded border px-2 py-1.5 ${verdictClasses(topVerdict.verdict)}`}>
                            <span className="font-medium">
                              {VERDICT_EMOJI[topVerdict.verdict]} {t('inspections.ai.item_prefix')}
                            </span>
                            {topVerdict.summary ? ` ${topVerdict.summary}` : ''}
                          </div>
                        )}
                        {it.requires_media === 1 && (
                          <p className="text-xs text-muted-foreground mt-1.5">
                            📸 {itemMedia.length} {itemMedia.length === 1 ? 'photo/video' : 'photos/videos'}
                          </p>
                        )}
                        {/* Bridge: a defect item can spawn a pre-filled
                            work order (vehicle + the defect as the 3C
                            complaint).  Only on actual defects, and only
                            for users who can create work orders. */}
                        {canCreateWorkOrder && DEFECT_STATUSES.has(it.status) && (
                          <button
                            onClick={() => createFrom({
                              vehicle_name: ins.vehicle_name ?? '',
                              complaint: [
                                `${it.category}: ${it.label}`,
                                it.notes ? `— ${it.notes}` : '',
                              ].filter(Boolean).join(' '),
                              // Out-of-service = can't legally run =
                              // emergency; other defects are unplanned
                              // but drivable.
                              repair_priority: it.status === 'oos' ? 'emergency' : 'non_scheduled',
                            })}
                            className="mt-2 inline-flex items-center px-2.5 py-1 text-xs rounded-lg border border-border hover:bg-accent text-foreground font-medium transition-colors"
                          >
                            + Work order
                          </button>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}

              {tab === 'media' && (
                <MediaGallery
                  inspection={ins}
                />
              )}
            </div>

            {/* Signatures — driver always (captured at submit time),
                reviewer optional (co-sign at review time when an
                account's compliance flow needs a second human). */}
            <SignaturesPanel
              inspection={ins}
              onSigned={() => qc.invalidateQueries({ queryKey: ['pti-inspection', inspectionId] })}
            />

            {/* Review panel */}
            <div className="sticky bottom-0 bg-card border-t border-border p-5 space-y-3">
              {alreadyReviewed ? (
                <div className="text-sm">
                  <p className="font-medium">
                    Reviewed as{' '}
                    <span className={toneText(
                      ins.review_status === 'approved'      ? 'ok'     :
                      ins.review_status === 'rejected'      ? 'danger' :
                                                              'warn'   // needs_service / revision_required
                    )}>
                      {REVIEW_OPTIONS.find(o => o.value === ins.review_status)?.label ?? ins.review_status}
                    </span>
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    {ins.reviewed_by_name
                      ? `By ${ins.reviewed_by_name} on ${_formatTs(ins.reviewed_at, tz)}`
                      : `On ${_formatTs(ins.reviewed_at, tz)}`}
                  </p>
                  {ins.review_notes && (
                    <p className="text-sm mt-2 bg-muted/40 rounded px-3 py-2">{ins.review_notes}</p>
                  )}
                </div>
              ) : canReview ? (
                <>
                  <div className="grid grid-cols-2 gap-2">
                    {REVIEW_OPTIONS.map(opt => (
                      <button
                        key={opt.value}
                        type="button"
                        onClick={() => setReviewStatus(opt.value)}
                        className={`px-3 py-2 rounded-md text-sm border ${
                          reviewStatus === opt.value
                            ? 'bg-primary text-primary-foreground border-primary'
                            : 'border-border hover:bg-muted'
                        }`}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                  <textarea
                    value={reviewNotes}
                    onChange={e => setReviewNotes(e.target.value)}
                    placeholder={t('inspections.review_notes_placeholder')}
                    rows={2}
                    className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm"
                  />
                  <button
                    type="button"
                    onClick={submitReview}
                    disabled={submitting}
                    className="w-full py-2 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded text-sm font-medium text-primary-foreground transition"
                  >
                    {submitting ? t('common.saving') : t('inspections.submit_review')}
                  </button>
                </>
              ) : (
                <div className="space-y-2">
                  <p className="text-sm text-muted-foreground">
                    {t('inspections.cannot_review', { status: ins.status })}
                  </p>
                  {/* Resend reminder — shown only on open inspections
                      the driver can still act on.  Throttled server-
                      side to once per hour per inspection. */}
                  {['scheduled', 'in_progress', 'revision_required'].includes(ins.status) && (
                    <button
                      type="button"
                      onClick={resendReminder}
                      disabled={resending}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted disabled:opacity-50"
                    >
                      <BellRing size={14} />
                      {resending ? t('inspections.remind_sending') : t('inspections.remind_btn')}
                    </button>
                  )}
                </div>
              )}
            </div>
          </>
        )}
      </div>
      {/* Portaled to body by base-ui, so tree placement is cosmetic —
          the "work order already open" confirm from the bridge. */}
      {bridgeDialog}
    </div>
  );
}
