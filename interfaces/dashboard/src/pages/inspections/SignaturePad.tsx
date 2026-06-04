import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

/**
 * Dashboard signature pad — pointer-driven canvas used by fleet
 * reviewers to co-sign a submitted inspection.  Mirrors the Mini App
 * pad's interaction model (same pointer events, same lineCap/Join,
 * same toDataURL('image/png') output) so signatures captured on
 * either surface look interchangeable and serialize identically.
 */

interface Props {
  prompt: string;
  onConfirm: (dataUrl: string) => Promise<void> | void;
  onCancel: () => void;
  saving?: boolean;
}

export function SignaturePad({ prompt, onConfirm, onCancel, saving = false }: Props) {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const drawingRef = useRef(false);
  const lastPtRef = useRef<{ x: number; y: number } | null>(null);
  const [didDraw, setDidDraw] = useState(false);
  const [submitting, setSubmitting] = useState(false);

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
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, rect.width, rect.height);
  }, []);

  useEffect(() => {
    sizeCanvas();
    const onResize = () => sizeCanvas();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [sizeCanvas]);

  const getPoint = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    const c = canvasRef.current;
    if (!c) return { x: 0, y: 0 };
    const rect = c.getBoundingClientRect();
    return { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
  };

  const onDown = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    if (saving || submitting) return;
    drawingRef.current = true;
    lastPtRef.current = getPoint(ev);
    ev.currentTarget.setPointerCapture(ev.pointerId);
  };

  const onMove = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    if (!drawingRef.current) return;
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext('2d');
    if (!ctx) return;
    const pt = getPoint(ev);
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

  const onUp = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    drawingRef.current = false;
    lastPtRef.current = null;
    try { ev.currentTarget.releasePointerCapture(ev.pointerId); }
    catch { /* already released */ }
  };

  const clear = () => {
    setDidDraw(false);
    sizeCanvas();
  };

  const confirm = async () => {
    if (!didDraw || submitting || saving) return;
    const c = canvasRef.current;
    if (!c) return;
    setSubmitting(true);
    try {
      await onConfirm(c.toDataURL('image/png'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="border border-border rounded-md p-3 space-y-2 bg-muted/30">
      <p className="text-xs font-medium">{prompt}</p>
      <div className="border border-dashed border-border rounded bg-white aspect-[5/2] overflow-hidden">
        <canvas
          ref={canvasRef}
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerCancel={onUp}
          className="w-full h-full block cursor-crosshair touch-none"
        />
      </div>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={clear}
          disabled={!didDraw || submitting}
          className="flex-1 px-2 py-1.5 text-xs border border-border rounded hover:bg-muted disabled:opacity-50"
        >
          {t('inspections.signature.clear')}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={submitting}
          className="flex-1 px-2 py-1.5 text-xs border border-border rounded hover:bg-muted disabled:opacity-50"
        >
          {t('inspections.signature.cancel')}
        </button>
        <button
          type="button"
          onClick={confirm}
          disabled={!didDraw || submitting || saving}
          className="flex-[1.4] px-2 py-1.5 text-xs rounded bg-primary text-primary-foreground disabled:opacity-50"
        >
          {submitting || saving ? t('inspections.signature.saving') : t('inspections.signature.confirm')}
        </button>
      </div>
    </div>
  );
}
