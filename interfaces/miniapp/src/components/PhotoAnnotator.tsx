import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { apiFetch, apiJSON } from '../api/client';
import { haptics } from '../hooks/useTelegram';
import type { PTIMedia } from '../types';

/**
 * Defect photo annotator — full-screen modal with the original photo
 * as a static background plus a transparent canvas overlay the driver
 * draws red markers on.  On save we composite both layers into a
 * single PNG and POST it to ``/annotate``; the server replaces the
 * canonical media blob so DOT audit always sees one canonical image.
 *
 * Why "bake into image" instead of storing strokes as JSON:
 *   * One file = one canonical artifact, harder to lose / desync.
 *   * Re-rendering elsewhere (PDF export, audit packet) needs no
 *     special viewer — any PNG renderer works.
 *   * Storage cost is comparable; the underlying photo dominates.
 *
 * Strokes are kept in component state as paths-of-points so Undo can
 * pop the last stroke without re-rasterizing.  Saving rasterizes the
 * paths plus the background image to an off-screen canvas at the
 * photo's native resolution (not the displayed size), preserving any
 * detail the driver wanted to preserve.
 */

interface Stroke {
  points: Array<{ x: number; y: number }>;
}

interface Props {
  inspectionId: number;
  media: PTIMedia;
  onSaved: () => Promise<void> | void;
  onCancel: () => void;
}

const STROKE_COLOR = '#ff3b30';   // iOS red — high contrast on most photos
const STROKE_WIDTH_RATIO = 0.004; // 0.4% of the larger photo dimension

export function PhotoAnnotator({ inspectionId, media, onSaved, onCancel }: Props) {
  const { t } = useTranslation();
  const imgRef = useRef<HTMLImageElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Source photo, fetched once via apiFetch so the JWT is attached.
  // We use an object-URL so the <img> + the off-screen save canvas
  // share the same in-memory bitmap (no second network round-trip).
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [photoLoaded, setPhotoLoaded] = useState(false);
  const [photoError, setPhotoError] = useState(false);

  const [strokes, setStrokes] = useState<Stroke[]>([]);
  const drawingRef = useRef<Stroke | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // ── Photo fetch ──────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    apiFetch(`/api/inspections/${inspectionId}/media/${media.id}`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`Photo fetch failed (${res.status})`);
        const blob = await res.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setPhotoUrl(objectUrl);
      })
      .catch(() => { if (!cancelled) setPhotoError(true); });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [inspectionId, media.id]);

  // ── Canvas sizing + render ───────────────────────────────────────
  // Sync the overlay canvas to the displayed photo size and redraw all
  // strokes on every state change.  Strokes are stored in
  // *displayed-canvas* pixel coordinates; the save step rescales to
  // the photo's native resolution.
  const repaint = useCallback(() => {
    const c = canvasRef.current;
    const img = imgRef.current;
    if (!c || !img || !photoLoaded) return;
    const rect = img.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.floor(rect.width * dpr)) {
      c.width = Math.floor(rect.width * dpr);
      c.height = Math.floor(rect.height * dpr);
    }
    c.style.width  = `${rect.width}px`;
    c.style.height = `${rect.height}px`;
    const ctx = c.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = STROKE_COLOR;
    const lineWidth = Math.max(
      3,
      Math.max(rect.width, rect.height) * STROKE_WIDTH_RATIO * 2,
    );
    ctx.lineWidth = lineWidth;
    for (const s of strokes) {
      if (s.points.length < 2) continue;
      ctx.beginPath();
      ctx.moveTo(s.points[0].x, s.points[0].y);
      for (let i = 1; i < s.points.length; i++) {
        ctx.lineTo(s.points[i].x, s.points[i].y);
      }
      ctx.stroke();
    }
  }, [strokes, photoLoaded]);

  useEffect(() => {
    repaint();
    const onResize = () => repaint();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [repaint]);

  // Block background scroll while open.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, []);

  // ── Drawing handlers ─────────────────────────────────────────────
  const pointFromEvent = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    const c = canvasRef.current;
    if (!c) return { x: 0, y: 0 };
    const rect = c.getBoundingClientRect();
    return { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
  };

  const onDown = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    if (saving) return;
    drawingRef.current = { points: [pointFromEvent(ev)] };
    ev.currentTarget.setPointerCapture(ev.pointerId);
    // Provisional repaint — show the dot the moment the finger lands.
    setStrokes(prev => [...prev, drawingRef.current!]);
  };

  const onMove = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    const s = drawingRef.current;
    if (!s) return;
    s.points.push(pointFromEvent(ev));
    repaint();
  };

  const onUp = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    drawingRef.current = null;
    try { ev.currentTarget.releasePointerCapture(ev.pointerId); }
    catch { /* already released */ }
  };

  const undo = () => {
    if (!strokes.length) return;
    haptics.selection();
    setStrokes(prev => prev.slice(0, -1));
  };

  const clearAll = () => {
    if (!strokes.length) return;
    haptics.selection();
    setStrokes([]);
  };

  // ── Save ─────────────────────────────────────────────────────────
  const save = async () => {
    if (saving || !strokes.length || !photoLoaded) return;
    setSaving(true);
    setSaveError(null);
    haptics.medium();
    try {
      const img = imgRef.current;
      const canvas = canvasRef.current;
      if (!img || !canvas) throw new Error('canvas/img missing');
      // Compose at the photo's NATIVE resolution so the stored PNG
      // doesn't lose detail compared to the original.  Scale stroke
      // coordinates from the displayed-canvas size to the natural
      // size.
      const out = document.createElement('canvas');
      out.width  = img.naturalWidth;
      out.height = img.naturalHeight;
      const ctx = out.getContext('2d');
      if (!ctx) throw new Error('ctx unavailable');
      ctx.drawImage(img, 0, 0, out.width, out.height);

      const displayRect = img.getBoundingClientRect();
      const sx = out.width  / displayRect.width;
      const sy = out.height / displayRect.height;

      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.strokeStyle = STROKE_COLOR;
      const baseLineWidth = Math.max(out.width, out.height) * STROKE_WIDTH_RATIO * 2;
      ctx.lineWidth = baseLineWidth;
      for (const s of strokes) {
        if (s.points.length < 2) continue;
        ctx.beginPath();
        ctx.moveTo(s.points[0].x * sx, s.points[0].y * sy);
        for (let i = 1; i < s.points.length; i++) {
          ctx.lineTo(s.points[i].x * sx, s.points[i].y * sy);
        }
        ctx.stroke();
      }

      const dataUrl = out.toDataURL('image/png');
      await apiJSON(
        `/api/inspections/${inspectionId}/media/${media.id}/annotate`,
        { method: 'POST', body: { image_data: dataUrl } },
      );
      haptics.success();
      await onSaved();
    } catch (e) {
      const msg = e instanceof Error ? e.message : t('pti.annotate_save_failed');
      setSaveError(msg);
      haptics.error();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t('pti.annotate_title')}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0, 0, 0, 0.92)',
        zIndex: 950,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <div style={{
        padding: '10px 14px',
        background: 'rgba(0,0,0,0.6)',
        color: '#fff',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        fontSize: 'var(--st-text-base)',
        flex: '0 0 auto',
      }}>
        <span style={{ fontWeight: 600 }}>{t('pti.annotate_title')}</span>
        <button
          type="button"
          onClick={() => { if (!saving) onCancel(); }}
          disabled={saving}
          style={{
            background: 'transparent',
            border: 'none',
            color: '#fff',
            fontSize: 'var(--st-text-xl)',
            cursor: 'pointer',
            opacity: saving ? 0.4 : 1,
          }}
          aria-label={t('pti.annotate_close')}
        >
          ×
        </button>
      </div>

      {/* Photo + overlay canvas — vertically centred, contained to
          the viewport so the toolbar always stays in reach. */}
      <div style={{
        flex: '1 1 auto',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 12,
        overflow: 'hidden',
        position: 'relative',
      }}>
        {!photoUrl && !photoError && (
          <p style={{ color: '#fff', fontSize: 'var(--st-text-md)' }}>{t('pti.annotate_loading')}</p>
        )}
        {photoError && (
          <p style={{ color: 'var(--st-red)', fontSize: 'var(--st-text-md)' }}>{t('pti.annotate_load_failed')}</p>
        )}
        {photoUrl && (
          <div style={{ position: 'relative', maxWidth: '100%', maxHeight: '100%' }}>
            <img
              ref={imgRef}
              src={photoUrl}
              alt=""
              onLoad={() => { setPhotoLoaded(true); requestAnimationFrame(repaint); }}
              style={{
                display: 'block',
                maxWidth: '100%',
                maxHeight: 'calc(100vh - 180px)',
                objectFit: 'contain',
                pointerEvents: 'none',  // canvas captures the input
              }}
            />
            <canvas
              ref={canvasRef}
              onPointerDown={onDown}
              onPointerMove={onMove}
              onPointerUp={onUp}
              onPointerCancel={onUp}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                touchAction: 'none',
                cursor: 'crosshair',
              }}
            />
          </div>
        )}
      </div>

      {/* Toolbar — fixed at the bottom so a long-stroke gesture
          doesn't drift onto a button by accident. */}
      <div style={{
        padding: '10px 12px 14px',
        background: 'rgba(0,0,0,0.65)',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        flex: '0 0 auto',
      }}>
        {saveError && (
          <p role="alert" style={{ color: 'var(--st-red)', fontSize: 'var(--st-text-sm)', margin: 0 }}>{saveError}</p>
        )}
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            type="button"
            onClick={undo}
            disabled={!strokes.length || saving}
            style={toolbarBtnStyle(!strokes.length || saving)}
          >
            ↶ {t('pti.annotate_undo')}
          </button>
          <button
            type="button"
            onClick={clearAll}
            disabled={!strokes.length || saving}
            style={toolbarBtnStyle(!strokes.length || saving)}
          >
            {t('pti.annotate_clear')}
          </button>
          <button
            type="button"
            onClick={save}
            disabled={!strokes.length || saving || !photoLoaded}
            style={{
              flex: 1.4,
              padding: '10px 12px',
              borderRadius: 10,
              border: 'none',
              background: !strokes.length || saving || !photoLoaded
                ? 'rgba(255,255,255,0.25)'
                : 'var(--tg-theme-button-color, #0a84ff)',
              color: 'var(--tg-theme-button-text-color, #fff)',
              fontSize: 'var(--st-text-base)',
              fontWeight: 600,
            }}
          >
            {saving ? t('pti.annotate_saving') : t('pti.annotate_save')}
          </button>
        </div>
      </div>
    </div>
  );
}


function toolbarBtnStyle(disabled: boolean): React.CSSProperties {
  return {
    flex: 1,
    padding: '10px 12px',
    borderRadius: 10,
    border: '1px solid rgba(255,255,255,0.3)',
    background: 'transparent',
    color: '#fff',
    fontSize: 'var(--st-text-md)',
    opacity: disabled ? 0.45 : 1,
  };
}
