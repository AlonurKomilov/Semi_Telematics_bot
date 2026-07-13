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
import { Bot, X, Loader2 } from 'lucide-react';
import { useAssistant } from './AssistantContext';
import { useViewPermissions } from '../../hooks/useViewPermissions';

// Lazy so the chat body (+ its DataGrid / recharts / formatAI deps) is a
// separate chunk loaded on FIRST panel open — not baked into the main
// bundle just because the launcher is mounted on every page.
const Chat = lazy(() => import('./Chat'));

export default function AssistantPanel() {
  const { open, openPanel, closePanel, togglePanel } = useAssistant();
  const { hasAny } = useViewPermissions();
  const location = useLocation();

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
      {/* Docked launcher — bottom-right, shown only while closed. */}
      {!open && (
        <button
          onClick={() => openPanel()}
          className="fixed bottom-5 right-5 z-40 flex items-center gap-2 rounded-full bg-primary text-primary-foreground shadow-lg px-4 py-3 text-sm font-medium hover:brightness-110 transition"
          title="Assistant (⌘J)"
          aria-label="Open assistant"
        >
          <Bot size={18} aria-hidden />
          <span className="hidden sm:inline">Assistant</span>
        </button>
      )}

      {/* Slide-over panel — non-modal, right-docked. */}
      <div
        className={`fixed inset-y-0 right-0 z-40 w-full sm:w-[420px] bg-card border-l border-border shadow-2xl transition-transform duration-200 ${
          open ? 'translate-x-0' : 'translate-x-full pointer-events-none'
        }`}
        role="complementary"
        aria-label="AI assistant"
        aria-hidden={!open}
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between border-b border-border px-4 py-2.5 flex-shrink-0">
            <span className="flex items-center gap-2 text-sm font-semibold">
              <Bot size={18} className="text-primary" aria-hidden />
              Assistant
            </span>
            <button
              onClick={closePanel}
              className="p-1 rounded text-muted-foreground hover:text-foreground transition-colors"
              title="Close (⌘J)"
              aria-label="Close assistant"
            >
              <X size={18} />
            </button>
          </div>
          {/* Only mount the chat while open — a closed panel does no work
              (no history load, no polling) and re-mounts fresh on reopen. */}
          <div className="flex-1 min-h-0 px-3 pb-3">
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
    </>
  );
}
