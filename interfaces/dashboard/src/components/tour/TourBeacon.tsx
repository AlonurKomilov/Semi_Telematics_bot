/**
 * The knock before the conversation.
 *
 * A small pulsing spark pinned beside the tour's FIRST step's anchor —
 * an invitation, never an interruption.  It blocks nothing, blurs
 * nothing, counts nothing down, and ignoring it costs zero; the
 * full-screen intro (and its personalized "I noticed…" line) only
 * appears after the user chooses to press it.  Who speaks first is
 * what separates a delightful observation from a surveillance one —
 * so the system waits to be asked.
 *
 * Deliberately NO escalation: no badge counts, no growing glow, no
 * speed-up after being ignored.  The moment a beacon fights for
 * attention it becomes the popup it replaced.
 */
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Sparkles } from 'lucide-react';
import type { TourSpec } from './types';

interface Pos { top: number; left: number }

const find = (anchor: string): Element | null =>
  document.querySelector(`[data-tour="${anchor}"]`);

export default function TourBeacon({
  tour,
  onOpen,
}: {
  tour: TourSpec;
  onOpen: () => void;
}) {
  const { t } = useTranslation();
  const [pos, setPos] = useState<Pos | null>(null);
  const elRef = useRef<Element | null>(null);

  const anchor = tour.steps[0]?.anchor ?? '';

  useEffect(() => {
    if (!anchor) return;
    let raf = 0;
    const sync = () => {
      raf = 0;
      const el = elRef.current?.isConnected ? elRef.current : find(anchor);
      elRef.current = el;
      if (!el) { setPos(null); return; }
      const r = el.getBoundingClientRect();
      // A fixed element escapes every ancestor's overflow clipping —
      // so when the anchor scrolls out of view, or slides UNDER a
      // sticky header inside its own scroll region, the beacon must
      // hide itself or it floats over chrome, pointing at nothing.
      // Off-screen is geometry; occlusion is asked of the browser
      // directly (elementFromPoint at the anchor's centre must land
      // on the anchor, something inside it, or something it is
      // inside).  jsdom has no layout, so the probe is optional.
      const offscreen = r.bottom < 0 || r.right < 0
        || r.top > window.innerHeight || r.left > window.innerWidth;
      let occluded = false;
      if (!offscreen && typeof document.elementFromPoint === 'function') {
        const cx = Math.min(Math.max(r.left + r.width / 2, 0), window.innerWidth - 1);
        const cy = Math.min(Math.max(r.top + r.height / 2, 0), window.innerHeight - 1);
        const top = document.elementFromPoint(cx, cy);
        occluded = top != null && top !== el
          && !el.contains(top) && !top.contains(el);
      }
      if (offscreen || occluded) { setPos(null); return; }
      // Perched on the anchor's top-right shoulder.
      setPos({ top: r.top - 6, left: r.right - 6 });
    };
    const queue = () => { if (!raf) raf = requestAnimationFrame(sync); };
    sync();
    window.addEventListener('scroll', queue, true);
    window.addEventListener('resize', queue);
    const poll = setInterval(queue, 500);   // anchor may mount late or move
    return () => {
      window.removeEventListener('scroll', queue, true);
      window.removeEventListener('resize', queue);
      clearInterval(poll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [anchor]);

  if (!pos) return null;   // no anchor on screen — no invitation to make

  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={t('tour.labels.beacon')}
      className="fixed z-40 flex items-center justify-center min-h-tap min-w-tap -translate-x-1/2 -translate-y-1/2"
      style={{ top: pos.top, left: pos.left }}
    >
      {/* The glow is a separate layer so reduced-motion users get a
          calm, static spark instead of nothing. */}
      <span className="absolute size-4 rounded-full bg-primary/30 motion-safe:animate-ping" aria-hidden />
      <span className="relative flex size-4.5 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-sm">
        <Sparkles className="size-3" />
      </span>
    </button>
  );
}
