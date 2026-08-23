/**
 * AppBanner — the rich notification card (severity underline, expand,
 * countdown) rendered through sonner's `toast.custom`, so banners share
 * ONE stacking lane + position with every other toast in the app.
 *
 * Two countdown modes, deliberately distinct:
 *   'dismiss' — a NOTICE: "This closes in Ns. Click to stop." — stopping
 *               just keeps it on screen (nothing else happens).
 *   'commit'  — a PENDING ACTION: "Applies in Ns" + a Cancel button —
 *               when the countdown ends `onExpire` fires (that's where
 *               stagedAction runs the real request).  No stop-pause here:
 *               an indefinitely-paused "will apply" would lie; the only
 *               honest interventions are Cancel or let it apply.
 */
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, Info, X, XCircle,
} from 'lucide-react';
import type { Tone } from '@/lib/status';
import { toneText } from '@/lib/status';
import { formatAgoShort } from '@/utils/datetime';

const TONE_ICON: Record<Tone, typeof Info> = {
  ok: CheckCircle2,
  warn: AlertTriangle,
  danger: XCircle,
  info: Info,
  neutral: Info,
};

// Progress underline per tone (solid foreground colours, token-based).
const TONE_BAR: Record<Tone, string> = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  danger: 'bg-danger',
  info: 'bg-info',
  neutral: 'bg-muted-foreground',
};

export interface BannerAction {
  label: string;
  onClick: () => void | Promise<void>;
  /** Primary renders as a filled button; default is outline. */
  primary?: boolean;
  /** Clicking this action also dismisses the banner (default true). */
  dismisses?: boolean;
}

export interface BannerOptions {
  tone: Tone;
  title: string;
  detail?: string;
  /** Timestamp the banner's event happened.  Rendered as a LIVE age
   *  ("2m ago" → "3d ago") in the header, recomputed on an interval — so a
   *  sticky banner that's lingered is obviously old, never mistaken for
   *  new (a critical banner never auto-closes, so this is its honesty). */
  ageSince?: string | number;
  /** Fire count for a recurring alert — shown as "×N" beside the age. */
  occurrence?: number;
  /** Small muted chip after the title — the object the notice is ABOUT
   *  (e.g. an alert's company code), so a banner disambiguates the same
   *  way the bell rows do. */
  tag?: string;
  actions?: BannerAction[];
  /** Seconds on the countdown; omit for a sticky banner (manual ✕). */
  seconds?: number;
  countdown?: 'dismiss' | 'commit';
  /** 'commit' mode: fires when the countdown completes. */
  onExpire?: () => void | Promise<void>;
  /** Fires on the ✕ (and on a 'commit' Cancel action). */
  onClose?: () => void;
}

const TICK_MS = 100;

export function AppBanner({ id, opts }: { id: string | number; opts: BannerOptions }) {
  const {
    tone, title, detail, ageSince, occurrence, tag, actions = [], seconds,
    countdown = 'dismiss', onExpire, onClose,
  } = opts;
  const total = (seconds ?? 0) * 1000;
  const [leftMs, setLeftMs] = useState(total);
  const [stopped, setStopped] = useState(seconds == null);
  const [expanded, setExpanded] = useState(false);
  const firedRef = useRef(false);

  // Re-render the age on a slow interval so a lingering (sticky) banner's
  // "3d ago" stays truthful instead of freezing at its show-time value.
  const [, bumpAge] = useState(0);
  useEffect(() => {
    if (ageSince == null) return;
    const iv = setInterval(() => bumpAge((n) => n + 1), 30_000);
    return () => clearInterval(iv);
  }, [ageSince]);
  const ageLabel = ageSince != null ? formatAgoShort(ageSince) : '';
  const meta = occurrence && occurrence > 1
    ? `×${occurrence} · ${ageLabel}` : ageLabel;

  useEffect(() => {
    if (stopped || total <= 0) return;
    const startedAt = Date.now();
    const iv = setInterval(() => {
      const left = total - (Date.now() - startedAt);
      if (left > 0) {
        setLeftMs(left);
        return;
      }
      clearInterval(iv);
      setLeftMs(0);
      if (!firedRef.current) {
        firedRef.current = true;
        toast.dismiss(id);
        if (countdown === 'commit') void onExpire?.();
      }
    }, TICK_MS);
    return () => clearInterval(iv);
  }, [stopped, total, id, countdown, onExpire]);

  const close = () => {
    if (!firedRef.current) {
      firedRef.current = true;
      onClose?.();
    }
    toast.dismiss(id);
  };

  const Icon = TONE_ICON[tone] ?? Info;
  const leftSec = Math.ceil(leftMs / 1000);
  const pct = total > 0 ? Math.max(0, Math.min(100, (leftMs / total) * 100)) : 0;

  return (
    <div className="w-80 bg-popover text-popover-foreground border border-border rounded-lg shadow-lg overflow-hidden">
      <div className="px-3 pt-2.5 pb-2">
        <div className="flex items-start gap-2">
          <Icon size={16} className={`${toneText(tone)} mt-0.5 shrink-0`} aria-hidden />
          <p className="flex-1 min-w-0 text-sm font-semibold inline-flex items-baseline gap-1.5">
            <span className="truncate">{title}</span>
            {tag && (
              /* Object chip (e.g. company code) — same styling as the bell
                 alert-row / maintenance company chip. */
              <span className="shrink-0 inline-flex items-center px-1.5 py-0.5 rounded bg-muted text-muted-foreground text-3xs font-normal">
                {tag}
              </span>
            )}
          </p>
          {meta && (
            <span className="shrink-0 mt-0.5 text-2xs text-muted-foreground tabular-nums">
              {meta}
            </span>
          )}
          {detail && (
            <button
              onClick={() => setExpanded(v => !v)}
              aria-label={expanded ? 'Collapse' : 'Expand'}
              aria-expanded={expanded}
              className="inline-flex size-6 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors shrink-0 min-h-tap min-w-tap"
            >
              {expanded ? <ChevronUp size={14} aria-hidden /> : <ChevronDown size={14} aria-hidden />}
            </button>
          )}
          <button
            onClick={close}
            aria-label="Close"
            className="inline-flex size-6 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors shrink-0 min-h-tap min-w-tap"
          >
            <X size={14} aria-hidden />
          </button>
        </div>
        {detail && expanded && (
          <p className="text-xs text-muted-foreground mt-1.5 ml-6">{detail}</p>
        )}
        {actions.length > 0 && (
          <div className="flex items-center gap-2 mt-2 ml-6">
            {actions.map((a) => (
              <button
                key={a.label}
                onClick={() => {
                  if (a.dismisses !== false) {
                    firedRef.current = true;     // action supersedes expiry
                    toast.dismiss(id);
                  }
                  void a.onClick();
                }}
                className={`h-7 px-2.5 rounded-md text-xs font-medium transition-colors ${
                  a.primary
                    ? 'bg-primary text-primary-foreground hover:bg-primary/80'
                    : 'border border-border text-foreground hover:bg-muted'
                } min-h-tap`}
              >
                {a.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {seconds != null && !stopped && leftMs > 0 && (
        <div className="px-3 py-1.5 bg-muted/50 border-t border-border/60 text-2xs text-muted-foreground">
          {countdown === 'commit' ? (
            <>Applies in <span className="font-semibold tabular-nums">{leftSec}</span>s.</>
          ) : (
            <>
              This closes in <span className="font-semibold tabular-nums">{leftSec}</span>s.{' '}
              <button
                className="font-semibold text-foreground hover:underline py-0.5 -my-0.5 min-h-tap"
                onClick={() => setStopped(true)}
              >
                Click to stop.
              </button>
            </>
          )}
        </div>
      )}

      {/* Severity-coloured progress underline */}
      {seconds != null && !stopped && (
        <div className="h-0.5 bg-border/40">
          <div
            className={`h-full ${TONE_BAR[tone]} transition-[width] duration-100 ease-linear`}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}

/** Show a banner in the app's notification lane. Returns the toast id. */
export function showBanner(opts: BannerOptions): string | number {
  return toast.custom((id) => <AppBanner id={id} opts={opts} />, {
    duration: Infinity,          // AppBanner owns its own countdown
  });
}
