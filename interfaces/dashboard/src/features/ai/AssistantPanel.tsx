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
 * Gated on `can_ai_chat` (view-scoped) so personas without the assistant
 * never see the launcher.  ⌘/Ctrl-J toggles it from anywhere.
 */
import { useEffect, lazy, Suspense } from 'react';
import { useLocation } from 'react-router-dom';
import { Bot, X, Loader2, Maximize2, Minimize2 } from 'lucide-react';
import {
  useAssistant, clampPanelW, setPanelWidthVar, PANEL_W_DEFAULT,
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
    const onMove = (ev: PointerEvent) => {
      latest = clampPanelW(window.innerWidth - ev.clientX);
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
  // can_ai_chat defaults true for every role; a persona that lacks it
  // (or a page that gates it off) never gets the copilot.
  const allowed = hasAny('can_ai_chat');

  if (onAssistantPage || !allowed) return null;

  return (
    <>
      {/* Slide-over panel — non-modal, right-docked.  Styled as SHELL
          CHROME (bg-sidebar) holding the chat in a rounded bg-background
          canvas — the exact anatomy of the app frame (see the shells'
          `<main className="bg-background rounded-xl">` inside a bg-sidebar
          wrapper), so a docked panel reads as a second canvas in the SAME
          frame, not a foreign white card floating over it. */}
      <div
        className={`fixed right-0 z-40 top-12 bottom-0 bg-sidebar text-sidebar-foreground transition-transform duration-200 ${
          panelExpanded
            // Expanded = fill the whole CONTENT region: right of the
            // sidebar (--sidebar-w).  Below lg the sidebar is a drawer,
            // so no left offset there.
            ? 'left-0 lg:left-[var(--sidebar-w)] shadow-none'
            // Docked = a right strip BELOW the topbar (top-12), the app
            // topbar stays full-width and the content resizes beside it.
            : 'w-full sm:w-[var(--assistant-w)] shadow-2xl xl:shadow-none'
        } ${
          open ? 'translate-x-0' : 'translate-x-full pointer-events-none'
        }`}
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
          className={`absolute left-0 inset-y-0 z-10 w-1.5 cursor-col-resize items-center justify-center group focus:outline-none ${
            panelExpanded ? 'hidden' : 'hidden sm:flex'
          }`}
        >
          <span
            className="h-8 w-0.5 rounded-full bg-sidebar-border group-hover:bg-ring group-focus-visible:bg-ring transition-colors"
            aria-hidden
          />
        </div>
        {/* The chat canvas — a rounded content card inside the bg-sidebar
            chrome (mirrors the shells' <main>).  The assistant's header
            now lives INSIDE this card as a mini bar with a divider
            (Samsara/Gemini), not on the chrome above it — so the title +
            controls read as the top of the chat surface, not a separate
            frame element. */}
        <div className="h-full p-2">
          <div className="flex h-full flex-col rounded-xl bg-background text-foreground overflow-hidden">
            {/* Mini header bar — title + New-chat / History (portalled) +
                Expand + Close, divided from the messages by a border. */}
            <div className="flex h-11 items-center justify-between px-3 border-b border-border shrink-0">
              <span className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <Bot size={16} className="text-primary" aria-hidden />
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
                    {panelExpanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
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
                    <X size={16} />
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
                    <Loader2 size={20} className="animate-spin text-muted-foreground" />
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
