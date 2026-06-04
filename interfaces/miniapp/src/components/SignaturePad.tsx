import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { haptics } from '../hooks/useTelegram';

/**
 * Finger-drawn e-signature pad rendered as a modal over the PTI
 * wizard.  Used as the final gate before ``POST /submit`` so the
 * server-side ``MissingSignature`` check never trips on a real user.
 *
 * Implementation notes
 * ────────────────────
 * * Drawing happens on a single `<canvas>` sized to the visual
 *   viewport.  We listen to pointer events (covers mouse + touch +
 *   stylus uniformly) so the same component works in the Telegram
 *   WebView and in a desktop browser test harness.
 * * Lines are anti-aliased via ``lineCap=round`` + ``lineJoin=round``
 *   + ~2.5px width so signatures look like ink rather than pixels.
 * * On confirm we serialize via ``canvas.toDataURL('image/png')`` —
 *   matches the strict ``data:image/png;base64,…`` prefix the API
 *   validates on.
 * * The "blank canvas" detector: we track whether any stroke has
 *   started; an empty canvas serializes to a non-trivial data URL
 *   but ``didDraw`` is the source of truth so we can disable the
 *   confirm button cheaply.
 */

interface Props {
  /** Localized label shown above the pad ("Sign to certify …"). */
  prompt: string;
  /** Called with the data-URL PNG when the user confirms. */
  onConfirm: (dataUrl: string) => Promise<void> | void;
  onCancel: () => void;
  /** Optional disabled flag while the parent is mid-upload. */
  saving?: boolean;
}

export function SignaturePad({ prompt, onConfirm, onCancel, saving = false }: Props) {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const drawingRef = useRef(false);
  const lastPtRef = useRef<{ x: number; y: number } | null>(null);
  const [didDraw, setDidDraw] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Resize the canvas to fit its CSS box.  We do this whenever the
  // window resizes or the modal opens; without it, the bitmap stays
  // tiny relative to the styled box and strokes look pixelated.
  const sizeCanvas = useCallback(() => {
    const c = canvasRef.current;
    if (!c) return;
    const rect = c.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    c.width  = Math.floor(rect.width * dpr);
    c.height = Math.floor(rect.height * dpr);
    const ctx = c.getContext('2d');
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = '#0a0a0a';
    ctx.lineWidth = 2.5;
    // Tint the background so the saved PNG isn't pure transparent
    // (some PDF viewers render a transparent PNG as black).
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, rect.width, rect.height);
  }, []);

  useEffect(() => {
    sizeCanvas();
    const onResize = () => sizeCanvas();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [sizeCanvas]);

  const getCanvasPoint = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    const c = canvasRef.current;
    if (!c) return { x: 0, y: 0 };
    const rect = c.getBoundingClientRect();
    return { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
  };

  const onPointerDown = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    if (saving || submitting) return;
    drawingRef.current = true;
    lastPtRef.current = getCanvasPoint(ev);
    ev.currentTarget.setPointerCapture(ev.pointerId);
  };

  const onPointerMove = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    if (!drawingRef.current) return;
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext('2d');
    if (!ctx) return;
    const pt = getCanvasPoint(ev);
    const last = lastPtRef.current;
    if (last) {
      ctx.beginPath();
      ctx.moveTo(last.x, last.y);
      ctx.lineTo(pt.x, pt.y);
      ctx.stroke();
    }
    lastPtRef.current = pt;
    if (!didDraw) setDidDraw(true);
  };

  const onPointerUp = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    drawingRef.current = false;
    lastPtRef.current = null;
    try { ev.currentTarget.releasePointerCapture(ev.pointerId); }
    catch { /* may have been released already */ }
  };

  const clear = () => {
    haptics.selection();
    setDidDraw(false);
    sizeCanvas();   // wipes + re-tints the background
  };

  const confirm = async () => {
    if (!didDraw || submitting || saving) return;
    const c = canvasRef.current;
    if (!c) return;
    setSubmitting(true);
    haptics.medium();
    try {
      const dataUrl = c.toDataURL('image/png');
      await onConfirm(dataUrl);
    } finally {
      setSubmitting(false);
    }
  };

  // Block background scroll while open — without this, finger draws
  // on the canvas also scroll the wizard underneath.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, []);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={prompt}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0, 0, 0, 0.55)',
        zIndex: 900,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        padding: 16,
      }}
    >
      <div
        style={{
          background: 'var(--tg-theme-bg-color, #fff)',
          color: 'var(--tg-theme-text-color, #111)',
          borderRadius: 14,
          padding: 16,
          width: '100%',
          maxWidth: 460,
          boxShadow: '0 12px 40px rgba(0,0,0,0.4)',
        }}
      >
        <p style={{ fontSize: 'var(--st-text-base)', fontWeight: 600, margin: '0 0 10px' }}>
          {prompt}
        </p>
        <div
          style={{
            border: '1.5px dashed var(--tg-theme-hint-color, #c7c7c7)',
            borderRadius: 10,
            background: '#fff',
            // Aspect ratio matches a typical legal-pad signature line.
            aspectRatio: '5 / 2',
            touchAction: 'none',
            overflow: 'hidden',
          }}
        >
          <canvas
            ref={canvasRef}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            style={{
              width: '100%',
              height: '100%',
              display: 'block',
              cursor: 'crosshair',
            }}
          />
        </div>
        <p style={{ fontSize: 'var(--st-text-xs)', color: 'var(--tg-theme-hint-color, #888)', margin: '8px 0 12px' }}>
          {t('pti.signature_hint')}
        </p>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            type="button"
            onClick={clear}
            disabled={!didDraw || submitting}
            style={{
              flex: 1,
              padding: '10px 12px',
              borderRadius: 10,
              border: '1px solid var(--tg-theme-hint-color, #c7c7c7)',
              background: 'transparent',
              color: 'inherit',
              fontSize: 'var(--st-text-base)',
              cursor: didDraw ? 'pointer' : 'not-allowed',
              opacity: didDraw ? 1 : 0.5,
            }}
          >
            {t('pti.signature_clear')}
          </button>
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            style={{
              flex: 1,
              padding: '10px 12px',
              borderRadius: 10,
              border: '1px solid var(--tg-theme-hint-color, #c7c7c7)',
              background: 'transparent',
              color: 'inherit',
              fontSize: 'var(--st-text-base)',
            }}
          >
            {t('pti.signature_cancel')}
          </button>
          <button
            type="button"
            onClick={confirm}
            disabled={!didDraw || submitting || saving}
            style={{
              flex: 1.4,
              padding: '10px 12px',
              borderRadius: 10,
              border: 'none',
              background: didDraw
                ? 'var(--tg-theme-button-color, #0a84ff)'
                : 'var(--tg-theme-hint-color, #c7c7c7)',
              color: 'var(--tg-theme-button-text-color, #fff)',
              fontSize: 'var(--st-text-base)',
              fontWeight: 600,
              cursor: didDraw && !submitting ? 'pointer' : 'not-allowed',
            }}
          >
            {submitting || saving ? t('pti.signature_saving') : t('pti.signature_confirm')}
          </button>
        </div>
      </div>
    </div>
  );
}
