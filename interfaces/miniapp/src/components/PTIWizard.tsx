import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { haptics } from '../hooks/useTelegram';
import { apiJSON } from '../api/client';
import type { PTIInspection, PTIItem, PTIMedia } from '../types';
import { MediaCapture } from './MediaCapture';
import { SignaturePad } from './SignaturePad';
import { PhotoAnnotator } from './PhotoAnnotator';

/**
 * Step-by-step checklist walkthrough for an in-progress PTI.
 *
 * Per item:
 *   * Status buttons (OK · Minor · Major · OOS · N/A)
 *   * Notes textarea
 *   * Add Photo / Add Video (via ``<MediaCapture>``)
 *   * Auto-advance on status select (configurable via env)
 *
 * Submit gated until every required item has a non-pending status
 * AND every ``requires_media`` required item has at least one photo
 * or video attached.  The same rules are enforced server-side in
 * ``capabilities/pti/service.submit_inspection`` — the client-side
 * check is for UX (greys out the button + explains why) rather than
 * security.
 */

const STATUS_BUTTONS: Array<{
  key: 'ok' | 'minor' | 'major' | 'oos' | 'na';
  labelKey: string;
  emoji: string;
}> = [
  { key: 'ok',    labelKey: 'pti.status.ok',    emoji: '✅' },
  { key: 'minor', labelKey: 'pti.status.minor', emoji: '🟡' },
  { key: 'major', labelKey: 'pti.status.major', emoji: '🟠' },
  { key: 'oos',   labelKey: 'pti.status.oos',   emoji: '🛑' },
  { key: 'na',    labelKey: 'pti.status.na',    emoji: '➖' },
];

interface AIVerdict {
  verdict: 'ok' | 'possible_issue' | 'likely_defect' | 'unclear';
  confidence?: string;
  summary?: string;
}

/** Parse the JSON verdict stored on a media row.  Returns null when
 *  the photo hasn't been AI-checked or the payload is unreadable. */
function parseVerdict(m: PTIMedia): AIVerdict | null {
  if (!m.ai_review_result) return null;
  try {
    const v = JSON.parse(m.ai_review_result) as AIVerdict;
    return v && v.verdict ? v : null;
  } catch {
    return null;
  }
}

/** Visual treatment per verdict: emoji + colour for the badge. */
const VERDICT_STYLE: Record<AIVerdict['verdict'], { emoji: string; bg: string }> = {
  ok:             { emoji: '✓', bg: 'var(--st-green)' },
  possible_issue: { emoji: '⚠', bg: 'var(--st-orange)' },
  likely_defect:  { emoji: '✕', bg: 'var(--st-red)' },
  unclear:        { emoji: '?', bg: 'var(--st-grey)' },
};

interface Props {
  inspection: PTIInspection;
  onUpdated: () => Promise<void> | void;
  onSubmitted: () => Promise<void> | void;
}

export function PTIWizard({ inspection, onUpdated, onSubmitted }: Props) {
  const { t } = useTranslation();
  // Memo items + media so they stabilise across renders when the
  // inspection object changes by-reference but content is equivalent
  // (avoids triggering the dependent useMemos for free).
  const items: PTIItem[] = useMemo(
    () => inspection.items || [], [inspection.items],
  );
  const media: PTIMedia[] = useMemo(
    () => inspection.media || [], [inspection.media],
  );
  const [index, setIndex] = useState(0);
  const [savingItem, setSavingItem] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // Signature pad opens when the user hits Submit and all gates pass.
  // The pad's onConfirm chains ``/signature`` then ``/submit``.
  const [showSignature, setShowSignature] = useState(false);
  // PhotoAnnotator target — when set, opens the annotator over a
  // specific media row.  null means closed.
  const [annotating, setAnnotating] = useState<PTIMedia | null>(null);
  // Media IDs with an AI vision check currently in flight, so the
  // thumbnail can show a "checking…" badge until the verdict lands.
  const [aiChecking, setAiChecking] = useState<Set<number>>(new Set());
  const [notes, setNotes] = useState<Record<number, string>>(() =>
    Object.fromEntries((inspection.items || []).map(i => [i.id, i.notes || ''])),
  );

  // Group media by item_id once per render — looked up frequently while
  // rendering the active item's media chips.
  const mediaByItem = useMemo(() => {
    const out: Record<number, PTIMedia[]> = {};
    for (const m of media) {
      if (m.item_id != null) {
        (out[m.item_id] ??= []).push(m);
      }
    }
    return out;
  }, [media]);

  // Completion check for the submit gate.  Mirrors the server-side
  // rules in capabilities/pti/service.py so the button only enables
  // when a submit would actually succeed:
  //   * check    → non-pending status (+ photo if requires_media)
  //   * photo    → at least one photo
  //   * document → at least one document upload
  const submitReady = useMemo(() => {
    const photoCount = (id: number) =>
      (mediaByItem[id] ?? []).filter(m => m.media_type !== 'document').length;
    const docCount = (id: number) =>
      (mediaByItem[id] ?? []).filter(m => m.media_type === 'document').length;

    for (const it of items) {
      if (!it.required) continue;
      const type = it.item_type || 'check';
      if (type === 'document') {
        if (docCount(it.id) === 0) {
          return { ok: false, reason: 'missing_document' as const, key: it.item_key };
        }
        continue;
      }
      if (type === 'photo') {
        if (photoCount(it.id) === 0) {
          return { ok: false, reason: 'missing_media' as const, key: it.item_key };
        }
        continue;
      }
      // 'check'
      if ((it.status || 'pending') === 'pending') {
        return { ok: false, reason: 'pending_items' as const, key: it.item_key };
      }
      if (it.requires_media && it.status !== 'na' && photoCount(it.id) === 0) {
        return { ok: false, reason: 'missing_media' as const, key: it.item_key };
      }
    }
    return { ok: true } as const;
  }, [items, mediaByItem]);

  const current = items[index];
  if (!current) {
    return (
      <div style={{ padding: 16 }}>
        {t('pti.no_items')}
      </div>
    );
  }

  const setStatus = async (key: typeof STATUS_BUTTONS[number]['key']) => {
    if (savingItem) return;
    setSavingItem(true);
    haptics.selection();
    try {
      await apiJSON(`/api/inspections/${inspection.id}/items/${current.item_key}`, {
        method: 'PATCH',
        body: { status: key, notes: notes[current.id] ?? '' },
      });
      await onUpdated();
      // Auto-advance to the next pending required item.
      const nextIdx = items.findIndex((it, i) =>
        i > index && it.required && (it.status === 'pending' || it.id === current.id),
      );
      if (nextIdx >= 0 && nextIdx < items.length) {
        setIndex(nextIdx);
      } else if (index + 1 < items.length) {
        setIndex(index + 1);
      }
    } catch {
      haptics.error();
    } finally {
      setSavingItem(false);
    }
  };

  const saveNotes = async () => {
    try {
      await apiJSON(`/api/inspections/${inspection.id}/items/${current.item_key}`, {
        method: 'PATCH',
        body: { notes: notes[current.id] ?? '' },
      });
      await onUpdated();
    } catch {
      /* leave the textarea content as-is; user can retry */
    }
  };

  // After a photo uploads, refetch (so the new thumbnail appears),
  // then fire the per-photo AI vision check.  The check runs server-
  // side and stores its verdict on the media row; we refetch again
  // when it returns so the verdict badge renders.  All best-effort —
  // a failed/disabled AI check never blocks the inspection flow.
  const handleUploaded = async (uploaded?: { id: number; media_type: string }) => {
    await onUpdated();
    if (!uploaded || uploaded.media_type !== 'photo') return;
    const mediaId = uploaded.id;
    setAiChecking(prev => new Set(prev).add(mediaId));
    try {
      await apiJSON(`/api/inspections/${inspection.id}/media/${mediaId}/ai-check`, {
        method: 'POST',
      });
      await onUpdated();   // pull the stored verdict onto the thumbnail
    } catch {
      /* AI check is advisory — swallow so the upload UX stays clean */
    } finally {
      setAiChecking(prev => {
        const next = new Set(prev);
        next.delete(mediaId);
        return next;
      });
    }
  };

  // "Submit" tap → open the signature pad.  Actual POSTs happen in
  // ``finalizeSubmit`` after the driver e-signs; that's what satisfies
  // the server-side ``MissingSignature`` gate.
  const onSubmitTap = () => {
    if (!submitReady.ok || submitting) return;
    setSubmitError(null);
    setShowSignature(true);
  };

  const finalizeSubmit = async (signatureDataUrl: string) => {
    setSubmitting(true);
    setSubmitError(null);
    haptics.medium();
    try {
      // Two-step: attach the signature first so submit's server-side
      // ``MissingSignature`` check passes.  Both endpoints live on the
      // same inspection row so a partial failure (signature succeeds,
      // submit doesn't) leaves the row in a recoverable state — the
      // user can retry submit without re-signing.
      await apiJSON(`/api/inspections/${inspection.id}/signature`, {
        method: 'POST',
        body: { role: 'driver', signature_data: signatureDataUrl },
      });
      // Best-effort device-GPS fallback.  The server prefers the
      // vehicle's telematics position; this only gets used when the
      // truck has no recent fix.  Non-blocking + short timeout so a
      // denied/slow permission never stalls the submit.
      const device = await _tryDeviceLocation();
      await apiJSON(`/api/inspections/${inspection.id}/submit`, {
        method: 'POST',
        body: device ? { device_lat: device.lat, device_lon: device.lon } : {},
      });
      haptics.success();
      setShowSignature(false);
      await onSubmitted();
    } catch (e) {
      const msg = e instanceof Error ? e.message : t('pti.submit_failed');
      setSubmitError(msg);
      haptics.error();
    } finally {
      setSubmitting(false);
    }
  };

  const itemMedia = mediaByItem[current.id] ?? [];
  const itemType = current.item_type || 'check';
  // 'check' items show status buttons; photo/document items don't —
  // their completion is the capture itself.
  const showStatusButtons = itemType === 'check';
  // Show the photo capture area for: a photo item (always), or a
  // check item the template flagged as photo-required.
  const showPhotoCapture = itemType === 'photo'
    || (itemType === 'check' && current.requires_media === 1);
  const isLast = index === items.length - 1;
  const refImageUrl = current.reference_image_url
    ? `/api/inspections/template-ref/${current.reference_image_url}`
    : null;

  return (
    <div className="pti-wizard">
      {/* Progress strip */}
      <div className="pti-wizard__progress">
        <div className="pti-wizard__progress-text">
          {t('pti.item_count', { current: index + 1, total: items.length })}
        </div>
        <div className="pti-wizard__progress-bar">
          <div
            className="pti-wizard__progress-fill"
            style={{ width: `${((index + 1) / items.length) * 100}%` }}
          />
        </div>
      </div>

      {/* Current item card */}
      <div className="pti-wizard__card">
        <div className="pti-wizard__category">{current.category}</div>
        <div className="pti-wizard__label">
          {current.label}
          {current.required ? <span className="pti-wizard__required"> *</span> : null}
        </div>

        {/* Reference example — what a correct capture looks like.
            Shown for any item the fleet attached one to. */}
        {refImageUrl && (
          <div className="pti-wizard__ref">
            <div className="pti-wizard__ref-label">📌 {t('pti.reference_label')}</div>
            <img className="pti-wizard__ref-img" src={refImageUrl} alt={t('pti.reference_label')} />
          </div>
        )}

        {showStatusButtons && (
          <div className="pti-wizard__statuses">
            {STATUS_BUTTONS.map(b => (
              <button
                key={b.key}
                type="button"
                className={
                  `pti-wizard__status pti-wizard__status--${b.key}` +
                  (current.status === b.key ? ' is-active' : '')
                }
                onClick={() => setStatus(b.key)}
                disabled={savingItem}
              >
                <span aria-hidden>{b.emoji}</span>
                {t(b.labelKey)}
              </button>
            ))}
          </div>
        )}

        <textarea
          className="pti-wizard__notes"
          rows={2}
          placeholder={t('pti.notes_placeholder')}
          value={notes[current.id] ?? ''}
          onChange={e => setNotes({ ...notes, [current.id]: e.target.value })}
          onBlur={saveNotes}
        />

        {/* Document upload slot (annual report, registration, …). */}
        {itemType === 'document' && (
          <div className="pti-wizard__media">
            <div className="pti-wizard__media-label">
              📄 {t('pti.document_required')}
              {itemMedia.filter(m => m.media_type === 'document').length > 0 && (
                <span className="pti-wizard__media-count">
                  · {t('pti.media_count', { count: itemMedia.filter(m => m.media_type === 'document').length })}
                </span>
              )}
            </div>
            <MediaCapture
              inspectionId={inspection.id}
              itemKey={current.item_key}
              onUploaded={handleUploaded}
            />
          </div>
        )}

        {showPhotoCapture && (
          <div className="pti-wizard__media">
            <div className="pti-wizard__media-label">
              📸 {t('pti.media_required')}
              {itemMedia.length > 0 && (
                <span className="pti-wizard__media-count">
                  · {t('pti.media_count', { count: itemMedia.length })}
                </span>
              )}
            </div>
            <MediaCapture
              inspectionId={inspection.id}
              itemKey={current.item_key}
              onUploaded={handleUploaded}
            />

            {/* Existing-photo strip: tap to annotate.  Photos already
                marked up display a small badge so the driver knows
                they don't need to redo it.  Videos are excluded —
                the annotator only supports stills. */}
            {itemMedia.filter(m => m.media_type === 'photo').length > 0 && (
              <div style={{
                marginTop: 8,
                display: 'flex',
                gap: 6,
                overflowX: 'auto',
                paddingBottom: 4,
              }}>
                {itemMedia
                  .filter(m => m.media_type === 'photo')
                  .map(m => (
                    <button
                      key={m.id}
                      type="button"
                      onClick={() => { haptics.selection(); setAnnotating(m); }}
                      style={{
                        position: 'relative',
                        flex: '0 0 auto',
                        width: 76,
                        height: 76,
                        borderRadius: 8,
                        border: '1px solid var(--tg-theme-hint-color, var(--st-grey))',
                        background: 'var(--tg-theme-secondary-bg-color)',
                        padding: 0,
                        overflow: 'hidden',
                        cursor: 'pointer',
                      }}
                      aria-label={t('pti.annotate_button')}
                    >
                      <img
                        src={`/api/inspections/${inspection.id}/media/${m.id}`}
                        alt=""
                        style={{
                          width: '100%',
                          height: '100%',
                          objectFit: 'cover',
                          display: 'block',
                        }}
                      />
                      {m.annotated_at && (
                        <span style={{
                          position: 'absolute',
                          top: 3,
                          right: 3,
                          background: 'var(--st-red)',
                          color: '#fff',
                          fontSize: 'var(--st-text-2xs)',
                          fontWeight: 700,
                          padding: '1px 4px',
                          borderRadius: 4,
                          letterSpacing: 0.3,
                        }}>
                          ✎
                        </span>
                      )}
                      {/* Storage state badge (top-left).  Driver sees
                          where the file lives right now: 💾 local /
                          🔄 syncing / ⚠ stuck.  'remote' is the healthy
                          default and gets no badge — keeps the strip
                          uncluttered. */}
                      {(() => {
                        const s = (m.storage_state || 'remote').toLowerCase();
                        if (s === 'remote') return null;
                        const cfg: Record<string, { glyph: string; bg: string }> = {
                          local:   { glyph: '💾', bg: 'var(--st-grey)' },
                          syncing: { glyph: '🔄', bg: 'var(--st-blue)' },
                          stuck:   { glyph: '⚠',  bg: 'var(--st-red)' },
                        };
                        const c = cfg[s];
                        if (!c) return null;
                        return (
                          <span style={{
                            position: 'absolute',
                            top: 3, left: 3,
                            background: c.bg, color: '#fff',
                            fontSize: 'var(--st-text-2xs)', fontWeight: 700,
                            padding: '1px 4px',
                            borderRadius: 4,
                            letterSpacing: 0.3,
                          }}>
                            {c.glyph}
                          </span>
                        );
                      })()}
                      {/* AI verdict badge (bottom-left).  Shows a
                          spinner-ish dot while the check is in flight,
                          then the verdict emoji once it lands. */}
                      {aiChecking.has(m.id) ? (
                        <span style={aiBadgeStyle('var(--st-blue)')}>🔍</span>
                      ) : (() => {
                        const v = parseVerdict(m);
                        if (!v) return null;
                        const s = VERDICT_STYLE[v.verdict];
                        return <span style={aiBadgeStyle(s.bg)}>{s.emoji}</span>;
                      })()}
                    </button>
                  ))}
              </div>
            )}

            {/* AI verdict summary — surfaces the wording for the
                most recently-checked photo on this item so the driver
                sees WHY the badge is amber/red, not just the colour. */}
            {(() => {
              if (aiChecking.size > 0) {
                return (
                  <p className="pti-wizard__ai-note" style={{ marginTop: 6, fontSize: 'var(--st-text-sm)', opacity: 0.8 }}>
                    🔍 {t('pti.ai_checking')}
                  </p>
                );
              }
              const checked = itemMedia
                .filter(m => m.media_type === 'photo')
                .map(parseVerdict)
                .filter((v): v is AIVerdict => v != null);
              const flagged = checked.find(v => v.verdict === 'possible_issue' || v.verdict === 'likely_defect');
              if (flagged) {
                return (
                  <p className="pti-wizard__ai-note" style={{ marginTop: 6, fontSize: 'var(--st-text-sm)', color: 'var(--st-orange)' }}>
                    ⚠ {flagged.summary || t('pti.ai_possible_issue')}
                  </p>
                );
              }
              if (checked.length > 0 && checked.every(v => v.verdict === 'ok')) {
                return (
                  <p className="pti-wizard__ai-note" style={{ marginTop: 6, fontSize: 'var(--st-text-sm)', color: 'var(--st-green)' }}>
                    ✓ {t('pti.ai_looks_ok')}
                  </p>
                );
              }
              return null;
            })()}
          </div>
        )}
      </div>

      {/* Navigation */}
      <div className="pti-wizard__nav">
        <button
          type="button"
          className="pti-wizard__nav-btn"
          onClick={() => { haptics.selection(); setIndex(Math.max(0, index - 1)); }}
          disabled={index === 0}
        >
          ← {t('pti.back')}
        </button>
        <button
          type="button"
          className="pti-wizard__nav-btn"
          onClick={() => { haptics.selection(); setIndex(Math.min(items.length - 1, index + 1)); }}
          disabled={isLast}
        >
          {t('pti.next')} →
        </button>
      </div>

      {/* Submit gate */}
      <div className="pti-wizard__submit">
        {!submitReady.ok && (
          <p className="pti-wizard__submit-hint">
            {submitReady.reason === 'pending_items'
              ? t('pti.submit_hint_pending', { item: submitReady.key })
              : submitReady.reason === 'missing_document'
              ? t('pti.submit_hint_missing_document', { item: submitReady.key })
              : t('pti.submit_hint_missing_media', { item: submitReady.key })}
          </p>
        )}
        <button
          type="button"
          className="pti-wizard__submit-btn"
          onClick={onSubmitTap}
          disabled={!submitReady.ok || submitting}
        >
          {submitting ? t('pti.submitting') : t('pti.submit_inspection')}
        </button>
        {submitError && (
          <p className="pti-wizard__submit-error" role="alert">{submitError}</p>
        )}
      </div>

      {showSignature && (
        <SignaturePad
          prompt={t('pti.signature_prompt')}
          saving={submitting}
          onCancel={() => { if (!submitting) setShowSignature(false); }}
          onConfirm={finalizeSubmit}
        />
      )}

      {annotating && (
        <PhotoAnnotator
          inspectionId={inspection.id}
          media={annotating}
          onCancel={() => setAnnotating(null)}
          onSaved={async () => {
            setAnnotating(null);
            await onUpdated();
          }}
        />
      )}
    </div>
  );
}


/** Best-effort device geolocation with a short timeout.  Resolves to
 *  null on denial / unavailable / timeout — the caller treats GPS as
 *  optional (the server falls back to vehicle telematics anyway). */
function _tryDeviceLocation(): Promise<{ lat: number; lon: number } | null> {
  return new Promise((resolve) => {
    if (typeof navigator === 'undefined' || !navigator.geolocation) {
      resolve(null);
      return;
    }
    let settled = false;
    const done = (v: { lat: number; lon: number } | null) => {
      if (!settled) { settled = true; resolve(v); }
    };
    // Hard cap in case the browser never fires either callback.
    setTimeout(() => done(null), 6000);
    navigator.geolocation.getCurrentPosition(
      (pos) => done({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
      () => done(null),
      { enableHighAccuracy: false, timeout: 5000, maximumAge: 60000 },
    );
  });
}


/** Small circular AI-verdict badge anchored to a thumbnail corner. */
function aiBadgeStyle(bg: string): React.CSSProperties {
  return {
    position: 'absolute',
    bottom: 3,
    left: 3,
    background: bg,
    color: '#fff',
    fontSize: 'var(--st-text-2xs)',
    fontWeight: 700,
    width: 18,
    height: 18,
    borderRadius: 9,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    lineHeight: 1,
  };
}
