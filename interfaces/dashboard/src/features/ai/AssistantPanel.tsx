/**
 * Assistant panel — the embedded copilot surface.
 *
 * A right-docked, NON-modal slide-over (no backdrop — you keep seeing
 * your data behind it, which is the whole point of a copilot) plus a
 * docked launcher button when closed.  Renders the SAME <Chat> component
 * the full `/ai/chat` page uses, in `variant="panel"`.
 *
 * Suppressed entirely on the `/ai/*` routes — the full page IS the
 * assistant there, so we don't stack a second instance on top of it.
 * Gated on `can_view_ai_assistant` (view-scoped) so personas without the assistant
 * never see the launcher.  ⌘/Ctrl-J toggles it from anywhere.
 */
import { useEffect, lazy, Suspense } from 'react';
import { useLocation } from 'react-router-dom';
import { Bot, X, Loader2, Maximize2, Minimize2 } from '../../lib/icons';
import {
  useAssistant, clampPanelW, setPanelWidthVar, panelScale, PANEL_W_DEFAULT,
} from './AssistantContext';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { Tip } from '../../components/tooltip';
import { Button } from '../../components/ui/button';
import { shortcut } from '../../utils/platform';

// Lazy so the chat body (+ its DataGrid / recharts / formatAI deps) is a
// separate chunk loaded on FIRST panel open — not baked into the main
// bundle just because the launcher is mounted on every page.
const Chat = lazy(() => import('./Chat'));

export default function AssistantPanel() {
  const {
    open, closePanel, togglePanel,
    panelWidth, setPanelWidth, setPanelResizing, setHeaderSlot,
    panelExpanded, setPanelExpanded,
  } = useAssistant();
  const { hasAny } = useViewPermissions();
  const location = useLocation();

  /** Divider drag: pointer-captured on the handle so move/up keep firing
   *  even when the cursor leaves it.  Width = distance from the pointer to
   *  the right viewport edge (the panel is right-docked).
   *
   *  Perf: each move writes the CSS var STRAIGHT to the DOM — no context
   *  state — so dragging doesn't re-render the <Chat> tree it would
   *  otherwise jank against.  React state is committed once on release
   *  (which also persists the width and reflows maps/charts).
   *
   *  `pointercancel` matters: a browser-interrupted drag (touch-scroll or
   *  OS gesture) never fires `pointerup`, which would strand
   *  `panelResizing = true` — transitions disabled and the width never
   *  persisted until the next completed drag. */
  function startResize(e: React.PointerEvent<HTMLDivElement>) {
    e.preventDefault();
    const el = e.currentTarget;
    el.setPointerCapture(e.pointerId);
    setPanelResizing(true);
    let latest = panelWidth;
    // The pointer reports SCREEN pixels; what we store is the width at
    // 100%. Without the divide, dragging at 130% would commit a number
    // that renders 30% wider than where the user let go — and then wider
    // again on the next drag.
    const scale = panelScale();
    // MEASURED TO THE PANEL'S OWN RIGHT EDGE, not the window's. They
    // used to be the same line — the dock was `fixed right-0`. It is a
    // page in the row now and the frame closes beside it, so the window
    // edge is a gutter's width away and every drag would have committed
    // a panel that much wider than where the pointer was let go.
    const panel = el.parentElement;
    const onMove = (ev: PointerEvent) => {
      const right = panel ? panel.getBoundingClientRect().right : window.innerWidth;
      latest = clampPanelW((right - ev.clientX) / scale);
      setPanelWidthVar(latest);
    };
    const finish = () => {
      el.removeEventListener('pointermove', onMove);
      el.removeEventListener('pointerup', finish);
      el.removeEventListener('pointercancel', finish);
      setPanelWidth(latest);      // commit + persist
      setPanelResizing(false);
      window.dispatchEvent(new Event('resize'));
    };
    el.addEventListener('pointermove', onMove);
    el.addEventListener('pointerup', finish);
    el.addEventListener('pointercancel', finish);
  }

  // ⌘/Ctrl-J toggles the panel from anywhere in the app.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'j') {
        e.preventDefault();
        togglePanel();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [togglePanel]);

  // The full-page assistant already occupies /ai/* — don't double-mount.
  const onAssistantPage = location.pathname.startsWith('/ai');
  // can_view_ai_assistant seeds true for every role; a persona that lacks it
  // (or a page that gates it off) never gets the copilot.
  const allowed = hasAny('can_view_ai_assistant');

  if (onAssistantPage || !allowed) return null;

  return (
    <>
      {/* THE SUB-PAGE. A second page card in the same frame as the
          first, laid out BESIDE it by the shell's content row — not a
          sheet over it.

          It used to be `fixed right-0 top-12 bottom-0`, a sibling of
          the shell, and everything odd about it followed from that. It
          covered the frame's right side instead of sitting inside it.
          The space between it and the page was the page's own
          `margin-right`, so the split the owner could see was not an
          object anybody could style. It carried the chrome colour and a
          `chrome-ground` of its own, because outside the shell there
          was no ground for a frame wallpaper to paint on. And under
          Glass it matched `.surface.fixed` — the rule that makes menus
          OCCLUDE what they float over — so it was made of a different
          material than the rail two hundred pixels to its left.

          In the row none of that is needed: it is a page, so it wears
          what a page wears (`page-ground`, the background colour, the
          border and the radius), and the frame wraps it the way the
          frame wraps everything. The comment that used to sit here
          already claimed this — "a second canvas in the SAME frame, not
          a foreign white card floating over it" — it was the structure
          that never caught up. */}
      <div
        className={`relative min-h-0 ${
          panelExpanded
            // The whole row; the page card beside it is hidden.
            ? 'flex-1 min-w-0'
            // Beside the page above xl, the whole row below it — two
            // pages do not fit in a phone's width. `flex-1` rather than
            // `w-full` for the narrow case: the gutter that closes the
            // frame beside it is 8px of the row, and a child asking for
            // 100% of the row would push exactly that much of itself
            // off the end.
            : 'flex-1 min-w-0 xl:flex-none xl:w-[var(--assistant-w)]'
        } ${open ? '' : 'hidden'}`}
        role="complementary"
        aria-label="AI assistant"
        aria-hidden={!open}
      >
        {/* Drag-to-resize divider (Chrome-side-panel style).  Pointer drag
            resizes; ← / → arrows nudge by 16px; double-click resets.
            Not tabbable while closed — the panel is only visually hidden
            (translate + aria-hidden), so a focusable child would otherwise
            be reachable by Tab off-screen. */}
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize assistant panel"
          aria-valuenow={panelWidth}
          aria-valuemin={320}
          aria-valuemax={680}
          tabIndex={open && !panelExpanded ? 0 : -1}
          onPointerDown={startResize}
          onDoubleClick={() => setPanelWidth(PANEL_W_DEFAULT)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowLeft') { e.preventDefault(); setPanelWidth(panelWidth + 16); }
            else if (e.key === 'ArrowRight') { e.preventDefault(); setPanelWidth(panelWidth - 16); }
          }}
          style={{ touchAction: 'none' }}
          className={`absolute -left-8 inset-y-0 z-10 w-8 cursor-col-resize items-center justify-center group focus:outline-none ${
            panelExpanded ? 'hidden' : 'hidden sm:flex'
          }`}
        >
          <span
            className="h-8 w-0.5 rounded-full bg-sidebar-border group-hover:bg-ring group-focus-visible:bg-ring transition-colors"
            aria-hidden
          />
        </div>
        {/* The chat canvas — and now simply a page card, the same one
            the shell's <main> is, `page-ground` included: it mirrored
            everything about that element except the class that lets a
            wallpaper reach it, so the dock sat as a flat slab while the
            page behind it wore the pattern.  The assistant's header
            now lives INSIDE this card as a mini bar with a divider
            (Samsara/Gemini), not on the chrome above it — so the title +
            controls read as the top of the chat surface, not a separate
            frame element. */}
        {/* NO PADDING OF ITS OWN any more. It used to hold the card in
            `px-2 pb-2` — its own private copy of the frame's gutters,
            the third place in this app that drew a side of the frame
            with padding. The row puts a real gutter on each side of it
            now, and the frame's bottom runs under both pages, so the
            card simply fills what it is given and lines up with the
            page beside it for free. */}
        <div className="h-full">
          <div className="relative page-ground flex h-full flex-col rounded-xl border border-border bg-background text-foreground overflow-hidden">
            {/* Mini header bar — title + New-chat / History (portalled) +
                Expand + Close, divided from the messages by a border. */}
            <div className="flex h-11 items-center justify-between px-3 border-b border-border shrink-0">
              <span className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <Bot className="text-primary size-4" aria-hidden />
                Assistant
              </span>
              <div className="flex items-center gap-1">
                {/* Docked <Chat> portals its New-chat / History controls here. */}
                <div ref={setHeaderSlot} className="flex items-center gap-1" />
                <Tip label={panelExpanded ? 'Collapse to side panel' : 'Expand'}>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => setPanelExpanded(!panelExpanded)}
                    className="hidden sm:inline-flex shrink-0 text-muted-foreground"
                    aria-label={panelExpanded ? 'Collapse assistant to side panel' : 'Expand assistant'}
                    aria-pressed={panelExpanded}
                  >
                    {panelExpanded ? <Minimize2 /> : <Maximize2 />}
                  </Button>
                </Tip>
                <Tip label={`Close (${shortcut('J')})`}>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={closePanel}
                    className="shrink-0 text-muted-foreground"
                    aria-label="Close assistant"
                  >
                    <X />
                  </Button>
                </Tip>
              </div>
            </div>
            {/* Chat messages.  Only mount while open — a closed panel does
                no work (no history load / polling) and re-mounts fresh on
                reopen.  Expanded: centre the column at a readable measure. */}
            <div className={`flex-1 min-h-0 p-3 ${panelExpanded ? 'mx-auto w-full max-w-4xl' : ''}`}>
              {open && (
                <Suspense fallback={
                  <div className="flex h-full items-center justify-center">
                    <Loader2 className="animate-spin text-muted-foreground size-5" />
                  </div>
                }>
                  <Chat variant="panel" />
                </Suspense>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
