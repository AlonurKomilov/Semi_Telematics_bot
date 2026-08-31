/**
 * Topbar AI launcher — the SINGLE entry point to the assistant, docked
 * in every shell's topbar cluster right beside the avatar (the Gemini /
 * Samsara "AI by the avatar" pattern).  Replaces both the old sidebar
 * "AI Assistant" nav item and the floating bottom-right chip.
 *
 * Click toggles the docked panel; ⌘/Ctrl-J still works (owned by
 * AssistantPanel).  Carries the live RUN STATUS the floating chip used
 * to show — a spinner while a turn runs and a check on completion — so
 * hiding the panel never hides that a run is in flight.
 *
 * Gated on `can_ai_chat` (view-scoped) like the panel, and hidden on
 * the `/ai/*` full-page route (you're already in the assistant there).
 */
import { useLocation } from 'react-router-dom';
import { Bot, Loader2, Check } from 'lucide-react';
import { useAssistant } from './AssistantContext';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { Tip } from '../../components/tooltip';
import { shortcut } from '../../utils/platform';
import { cn } from '@/lib/utils';

export function AssistantLauncher() {
  const { open, togglePanel, runPhase } = useAssistant();
  const { hasAny } = useViewPermissions();
  const location = useLocation();

  if (location.pathname.startsWith('/ai') || !hasAny('can_ai_chat')) return null;

  const Icon = runPhase === 'running' ? Loader2 : runPhase === 'done' ? Check : Bot;

  return (
    <Tip label={`Assistant (${shortcut('J')})`}>
      <button
        onClick={() => togglePanel()}
        aria-label="Toggle assistant"
        aria-pressed={open}
        className={`relative inline-flex size-8 items-center justify-center rounded-md transition-colors ${
          open
            ? 'bg-primary/15 text-foreground ring-1 ring-primary'
            : 'text-muted-foreground hover:bg-muted hover:text-foreground'
        }`}
      >
        <Icon className={cn(runPhase === 'running' ? 'animate-spin' : '', 'size-4.5')} aria-hidden />
        {/* Idle-but-running is impossible (running implies the spinner),
            so the dot only marks a just-finished run while closed. */}
        {runPhase === 'done' && !open && (
          <span className="absolute right-1 top-1 size-1.5 rounded-full bg-ok" aria-hidden />
        )}
      </button>
    </Tip>
  );
}

export default AssistantLauncher;
