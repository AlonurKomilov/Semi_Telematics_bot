// ABC Checker, as an overlay — the fourth member of the failure family,
// and the only one that is not a page.
//
// The other three replace the app because the app is not there: the
// service worker's offline.html and Cloudflare's 5xx page both answer a
// navigation that never reached a running dashboard. This one answers
// mid-session, with the app loaded and the person's place in it intact,
// so replacing it with a page would throw away the very thing worth
// protecting. It covers instead, and reloads when the API is back —
// by then the app underneath has already failed requests (auth check,
// page data) it will never retry, so merely hiding the card would
// strand the user on a broken screen.
//
// Two reasons reach it, and they are not the same sentence:
//
//   'updating'     502/503/504 from nginx — the API is restarting. A
//                  deploy. Nothing is wrong.
//   'unreachable'  the request never got an answer at all. The network
//                  between this browser and us is gone. Until now this
//                  announced NOTHING, so a connection that died
//                  mid-shift surfaced as a raw error on each panel —
//                  exactly the "this product is broken" impression the
//                  offline page exists to prevent, and the offline page
//                  never appears here because a single-page app does
//                  not navigate.
//
// And it gives up honestly. It used to poll forever under "a quick
// update is being rolled out — this usually takes under a minute",
// which after five minutes is a lie and after thirty is a harmful one:
// the person believes it is routine while it is an outage. Past
// ESCALATE_AFTER_MS it stops claiming that and offers the full page,
// which is cached and opens with the network down.
import { useEffect, useRef, useState } from 'react';
import { CloudOff, ExternalLink, Loader2, TriangleAlert } from '../lib/icons';
import { Card } from '@/components/ui/card';

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api';

// A deploy that has not finished in ninety seconds is not the "under a
// minute" the calm copy promises. The number is deliberately just past
// a real rolling restart (the last one took 107s end to end, but the API
// answers well before it completes) rather than a round guess.
const ESCALATE_AFTER_MS = 90_000;

type Reason = 'updating' | 'unreachable';
type Phase = 'hidden' | 'waiting' | 'reloading';

interface Copy { title: string; body: string; }

function copyFor(reason: Reason, escalated: boolean): Copy {
  if (reason === 'unreachable') {
    return escalated
      ? {
          title: 'Still can’t reach 4truck',
          body: 'The connection has been down for a while. Your work is still here and nothing '
              + 'was lost — this page is waiting, not broken.',
        }
      : {
          title: 'Connection lost',
          body: 'This device stopped being able to reach 4truck. Your work is still here. '
              + 'We’ll reconnect automatically.',
        };
  }
  return escalated
    ? {
        title: 'This update is taking longer than usual',
        body: 'It should have finished by now. Your work is still here and nothing was lost.',
      }
    : {
        title: 'Updating the platform',
        body: 'A quick update is being rolled out — this usually takes under a minute. '
            + 'We’ll reconnect automatically.',
      };
}

export default function MaintenanceOverlay() {
  const [phase, setPhase] = useState<Phase>('hidden');
  const [reason, setReason] = useState<Reason>('updating');
  const [escalated, setEscalated] = useState(false);
  const polling = useRef(false);

  useEffect(() => {
    const onMaintenance = (e: Event) => {
      const why: Reason =
        (e as CustomEvent<{ reason?: Reason }>).detail?.reason === 'unreachable'
          ? 'unreachable'
          : 'updating';
      // First reason wins: a restart that then drops the connection is
      // still a restart, and flipping the wording under the reader
      // would read as the page guessing.
      //
      // Decided BEFORE the ref is touched, and outside a state updater.
      // React runs an updater lazily during render, by which time
      // `polling.current` is already true — so reading the ref inside
      // one made the reason permanently whatever it was initialised to.
      // A test caught it saying "Updating the platform" over a dead
      // connection.
      const first = !polling.current;
      setPhase((p) => (p === 'hidden' ? 'waiting' : p));
      if (!first) return;
      setReason(why);
      polling.current = true;

      const startedAt = Date.now();
      const poll = async () => {
        try {
          const r = await fetch(`${API_BASE}/health`, { cache: 'no-store', credentials: 'include' });
          if (r.ok) {
            setPhase('reloading');
            window.location.reload();
            return;
          }
        } catch { /* still down */ }
        if (Date.now() - startedAt >= ESCALATE_AFTER_MS) setEscalated(true);
        setTimeout(poll, 4000);
      };
      setTimeout(poll, 2500);
    };
    window.addEventListener('4truck:maintenance', onMaintenance);
    return () => window.removeEventListener('4truck:maintenance', onMaintenance);
  }, []);

  if (phase === 'hidden') return null;

  const done = phase === 'reloading';
  const { title, body } = copyFor(reason, escalated);
  const Icon = done ? Loader2 : escalated ? TriangleAlert : reason === 'unreachable' ? CloudOff : Loader2;
  const spin = done || (!escalated && reason === 'updating');

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-background/80 backdrop-blur-sm"
      role="status"
      aria-live="polite"
    >
      <Card padding="panel" className="mx-4 max-w-sm text-center shadow-xl">
        {/* The same identity the other three carry, so a customer who
            has seen one recognises the voice rather than meeting a new
            stranger each time something goes wrong. */}
        {/* The same lockup the other three carry — ABC Legacy LLC set as
            type rather than shipped as an image, so it takes the theme's
            colour instead of arriving as a black PNG on a dark card. */}
        <div className="flex items-center justify-center gap-2">
          <span className="font-light uppercase tracking-[0.17em] text-[12px] text-foreground [font-family:Futura,'Century_Gothic','Avenir_Next','Trebuchet_MS',ui-sans-serif,system-ui,sans-serif]">
            ABC&nbsp;Legacy&nbsp;LLC
          </span>
          <span aria-hidden="true" className="text-border">/</span>
          <span className="text-[13.5px] font-semibold text-foreground">Checker</span>
        </div>

        <Icon
          className={`mx-auto mt-4 size-6 ${spin ? 'animate-spin text-primary' : escalated ? 'text-warn' : 'text-muted-foreground'}`}
        />
        <h2 className="mt-4 text-base font-semibold text-foreground">
          {done ? 'Back online' : title}
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          {done ? 'Reloading…' : body}
        </p>

        {/* offline.html is precached by the service worker, so it opens
            with the network down — which is exactly when this link is
            offered. A new tab, because the session under this overlay is
            the thing being protected. */}
        {!done && escalated && (
          <a
            href="/offline.html"
            target="_blank"
            rel="noopener"
            className="mt-4 inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
          >
            See what to try
            <ExternalLink className="size-3.5" />
          </a>
        )}
      </Card>
    </div>
  );
}
