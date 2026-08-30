/**
 * The tour engine — lights one real control at a time and advances on
 * the user's real action.
 *
 * NOT a modal, by design.  The page stays fully interactive: the four
 * dim panels are `pointer-events-none`, there is no focus trap, and
 * Escape (or the always-visible Exit button) ends the tour instantly.
 * A teacher stands beside you; a teacher does not hold your hands.
 *
 * Mechanics, in one breath: resolve the step's `data-spotlight` anchor
 * (waiting for it to APPEAR — step 2's checkbox only exists after step
 * 1 opens the form), scroll it into view, draw the cutout + step card,
 * advance on a capture-phase click inside the anchor, and when an
 * anchor never shows up — or LEAVES, because the user closed the form
 * mid-tour — go back to waiting rather than point at empty space, and
 * give up quietly at the timeout.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { cardVariants } from '@/components/ui/card';
import type { TourSpec } from './types';

/** How long a step's anchor may stay absent before the tour exits. */
const ANCHOR_TIMEOUT_MS = 15_000;
/** A 'click-gone' arm expires: the anchor vanishing long after the
 *  click is the user closing the form, not the submit succeeding. */
const ARM_WINDOW_MS = 8_000;
/** Hairline breathing room between the lit element and the cutout —
 *  a visual inset like a border width, not a rendered size, so it
 *  deliberately does not ride the Size multipliers. */
const PAD = 6;

interface Rect { top: number; left: number; width: number; height: number }

function measure(el: Element): Rect {
  const r = el.getBoundingClientRect();
  return {
    top: r.top - PAD,
    left: r.left - PAD,
    width: r.width + PAD * 2,
    height: r.height + PAD * 2,
  };
}

const find = (anchor: string): Element | null =>
  document.querySelector(`[data-spotlight="${anchor}"]`);

export default function TourOverlay({
  tour,
  onDone,
  onExit,
}: {
  tour: TourSpec;
  /** Every step's action performed — the congratulations moment. */
  onDone: () => void;
  /** Left early (Exit, Escape, or an anchor that never appeared). */
  onExit: () => void;
}) {
  const { t } = useTranslation();
  const [stepIdx, setStepIdx] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);
  const [celebrating, setCelebrating] = useState(false);
  // Bumped when a RESOLVED anchor leaves the DOM (the user closed the
  // form mid-tour) — re-arms the resolver below with a fresh timeout,
  // because its deps would otherwise never change and the ring would
  // stay frozen over empty space.
  const [generation, setGeneration] = useState(0);
  const elRef = useRef<Element | null>(null);
  // 'click-gone' arming — the moment the user clicked the anchor.
  // Success is "armed AND the anchor then left the DOM"; a validation
  // refusal leaves the element in place, so the step honestly holds.
  const armedAtRef = useRef(0);
  // The step index a click has already advanced.  One label click
  // dispatches TWO native click events (the label's own and the one
  // the browser forwards to its input) — both land here, and without
  // this guard both advanced, silently skipping a step.
  const advancedRef = useRef(-1);
  const cardRef = useRef<HTMLDivElement | null>(null);

  const step = tour.steps[stepIdx];
  const total = tour.steps.length;
  // The position tracker closes over these via refs: its dep list is
  // deliberately narrow, and a stale `advance` there would celebrate
  // with an old step index.
  const stepRef = useRef(step);
  stepRef.current = step;

  // ── Resolve the anchor, waiting for it to appear ────────────────
  useEffect(() => {
    if (celebrating) return;
    let cancelled = false;
    const deadline = Date.now() + ANCHOR_TIMEOUT_MS;
    const observer = new MutationObserver(() => attempt());
    // The observer misses attribute-only mounts and CSS-driven
    // reveals; a slow interval catches those.
    const timer = setInterval(() => attempt(), 500);
    const stop = () => { observer.disconnect(); clearInterval(timer); };

    function attempt() {
      if (cancelled) return;
      const el = find(step.anchor);
      if (el) {
        stop();                       // found — nothing left to watch
        elRef.current = el;
        el.scrollIntoView({ block: 'center', behavior: 'smooth' });
        setRect(measure(el));
      } else if (Date.now() > deadline) {
        stop();
        onExit();                     // pointing at nothing teaches nothing
      }
    }
    attempt();
    if (!cancelled && !elRef.current) {
      observer.observe(document.body, { childList: true, subtree: true });
    }
    return () => { cancelled = true; stop(); };
  }, [step.anchor, celebrating, generation, onExit]);

  // ── Track the element through scroll / resize / rerender ────────
  useEffect(() => {
    if (celebrating) return;
    let raf = 0;
    const sync = () => {
      raf = 0;
      const el = elRef.current;
      if (!el) return;
      if (!el.isConnected) {
        // The lit control was unmounted under us.  For an ARMED
        // 'click-gone' step this IS the outcome we were waiting for —
        // the form closed itself on a successful submit.  Beyond the
        // window, or with no arm at all, it is the user closing the
        // form: hide the chrome and send the resolver back to waiting.
        elRef.current = null;
        if (stepRef.current.advanceOn === 'click-gone'
            && armedAtRef.current
            && Date.now() - armedAtRef.current < ARM_WINDOW_MS) {
          advanceRef.current();
          return;
        }
        setRect(null);
        setGeneration((g) => g + 1);
        return;
      }
      setRect(measure(el));
    };
    const queue = () => { if (!raf) raf = requestAnimationFrame(sync); };
    window.addEventListener('scroll', queue, true);
    window.addEventListener('resize', queue);
    const poll = setInterval(queue, 300);   // layout shifts with no event
    return () => {
      window.removeEventListener('scroll', queue, true);
      window.removeEventListener('resize', queue);
      clearInterval(poll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [stepIdx, celebrating]);

  // ── Advance on the real action ──────────────────────────────────
  const advance = useCallback(() => {
    if (advancedRef.current === stepIdx) return;   // label double-dispatch
    advancedRef.current = stepIdx;
    armedAtRef.current = 0;
    if (stepIdx + 1 >= total) {
      setCelebrating(true);
    } else {
      setRect(null);            // next anchor may not exist yet
      elRef.current = null;
      setStepIdx((i) => i + 1);
    }
  }, [stepIdx, total]);
  const advanceRef = useRef(advance);
  advanceRef.current = advance;

  useEffect(() => {
    if (celebrating) return;
    const onClick = (e: MouseEvent) => {
      const target = e.target as Element | null;
      if (!target?.closest(`[data-spotlight="${step.anchor}"]`)) return;
      // A step may demand the click land on something REAL inside the
      // anchor — step 3 anchors the chip well, but only pressing a
      // chip picks a vehicle; the well's own padding does not.
      if (step.advanceWithin && !target.closest(step.advanceWithin)) return;
      if (step.advanceOn === 'click-gone') {
        // The click alone proves nothing — validation may refuse it.
        // Arm, and let the anchor's disappearance be the verdict.  A
        // re-click after a refusal simply re-arms.
        armedAtRef.current = Date.now();
        return;
      }
      advance();
    };
    // Capture phase: the click reaches us even when the control's own
    // handler stops propagation, and BEFORE a rerender unmounts it.
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, [step.anchor, step.advanceWithin, advance, celebrating]);

  // ── Escape always exits ─────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') (celebrating ? onDone : onExit)();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onExit, onDone, celebrating]);

  // ── Congratulations ─────────────────────────────────────────────
  if (celebrating) {
    return (
      <div className="fixed inset-x-0 bottom-6 z-[60] flex justify-center pointer-events-none">
        <div className={cn(cardVariants({ padding: 'none' }), 'pointer-events-auto flex items-center gap-3 border-ok-bd px-4 py-3 shadow-lg motion-safe:animate-in motion-safe:slide-in-from-bottom-4')}>
          <CheckCircle2 className="size-5 text-ok" />
          <div>
            <p className="text-sm font-semibold text-foreground">
              {t('spotlight.labels.done_title')}
            </p>
            <p className="text-xs text-muted-foreground">
              {t(`spotlight.${tour.key}.done`)}
            </p>
          </div>
          <button
            type="button"
            onClick={onDone}
            aria-label={t('spotlight.labels.close')}
            className="ml-2 rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground transition min-h-tap min-w-tap"
          >
            <X className="size-4" />
          </button>
        </div>
      </div>
    );
  }

  if (!rect) return null;       // anchor still being waited for

  // Place the step card below the target when there is room, above
  // otherwise — judged by the card's own MEASURED size (it scales with
  // the user's Size setting, so a pixel constant here would be wrong
  // at every multiplier but one).  First paint uses a conservative
  // guess; the re-render after `cardRef` mounts corrects it.
  const cardH = cardRef.current?.offsetHeight ?? 120;
  const cardW = cardRef.current?.offsetWidth ?? 320;
  const below = rect.top + rect.height + cardH + 20 < window.innerHeight;
  const popTop = below ? rect.top + rect.height + 10 : undefined;
  const popBottom = below ? undefined : window.innerHeight - rect.top + 10;
  const popLeft = Math.max(12, Math.min(rect.left, window.innerWidth - cardW - 12));

  // `bg-background`, not foreground: each theme fades toward its OWN
  // ground (light page → light haze, dark page → dark haze), so the
  // dim always recedes and the lit element always pops.  A foreground
  // scrim inverts in dark mode — a bright fog over a dark page.
  const dim = 'fixed bg-background/70 supports-[backdrop-filter]:backdrop-blur-sm pointer-events-none z-[60]';
  return (
    <div role="status" aria-live="polite">
      {/* Four panels around the cutout — the page stays clickable
          everywhere; only the LIT element matters, and it is the one
          spot the dim never covers. */}
      <div className={dim} style={{ top: 0, left: 0, right: 0, height: Math.max(0, rect.top) }} />
      <div className={dim} style={{ top: rect.top + rect.height, left: 0, right: 0, bottom: 0 }} />
      <div className={dim} style={{ top: rect.top, left: 0, width: Math.max(0, rect.left), height: rect.height }} />
      <div className={dim} style={{ top: rect.top, left: rect.left + rect.width, right: 0, height: rect.height }} />
      {/* The ring around the lit control. */}
      <div
        className="fixed z-[60] rounded-lg border-2 border-primary pointer-events-none motion-safe:animate-pulse"
        style={{ top: rect.top, left: rect.left, width: rect.width, height: rect.height }}
      />
      {/* The step card. */}
      <div
        ref={cardRef}
        className={cn(cardVariants({ padding: 'none' }), 'fixed z-[60] w-80 max-w-[calc(100vw-24px)] p-3 shadow-lg')}
        style={{ top: popTop, bottom: popBottom, left: popLeft }}
      >
        <div className="flex items-start justify-between gap-3">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {t('spotlight.labels.step_of', {
              n: String(stepIdx + 1), total: String(total),
            })}
          </p>
          <button
            type="button"
            onClick={onExit}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition min-h-tap"
          >
            <X className="size-3.5" />
            {t('spotlight.labels.exit')}
          </button>
        </div>
        <p className="mt-1 text-sm text-foreground">
          {t(`spotlight.${tour.key}.step${stepIdx + 1}`)}
        </p>
      </div>
    </div>
  );
}
