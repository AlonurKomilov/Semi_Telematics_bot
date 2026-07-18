/**
 * Assistant host — mounts the copilot infrastructure ONCE, above the
 * per-persona shell.  Wrapping the shell here (rather than editing all
 * six shell files) keeps the panel a single instance with a single
 * state, and lets both feature pages (which publish page context) and
 * the panel (which reads it) sit inside the same providers.
 *
 * Router usage:  <Route element={<AssistantHost><Shell/></AssistantHost>}>
 * The wrapped Shell still renders <Outlet/> normally — wrapping is
 * transparent to routing.
 */
import { useEffect, type ReactNode } from 'react';
import { AssistantProvider, useAssistant } from './AssistantContext';
import { PageContextProvider } from './PageContext';
import AssistantPanel from './AssistantPanel';

/** Docked split view: on wide screens an open panel PUSHES the app aside
 *  (margin-right = panel width) so the assistant sits BESIDE the page,
 *  never over it — the Gemini-side-panel pattern.  Below xl the panel
 *  stays an overlay: pushing 420px out of a small viewport would crush
 *  the content more than the overlay covers it. */
function DockedContent({ children }: { children: ReactNode }) {
  const { open, panelResizing, panelExpanded } = useAssistant();
  // The push only applies in DOCKED mode — the expanded overlay covers
  // the page instead of sitting beside it, so reserving margin there
  // would just shove the (hidden) content around for nothing.
  const pushed = open && !panelExpanded;
  useEffect(() => {
    // Maps (Google Maps doesn't observe container size) and any component
    // that caches its width reflow on a window resize — fire one after the
    // margin transition settles so charts/maps fill the new width.
    const id = setTimeout(() => window.dispatchEvent(new Event('resize')), 220);
    return () => clearTimeout(id);
  }, [pushed]);
  // Width comes from the `--assistant-w` var on <html> (AssistantContext),
  // so a divider drag repaints this margin without re-rendering the app.
  return (
    <div
      className={`${panelResizing ? '' : 'transition-[margin-right] duration-200'} ${
        pushed ? 'xl:mr-[var(--assistant-w)]' : ''
      }`}
    >
      {children}
    </div>
  );
}

export default function AssistantHost({ children }: { children: ReactNode }) {
  return (
    <PageContextProvider>
      <AssistantProvider>
        <DockedContent>{children}</DockedContent>
        <AssistantPanel />
      </AssistantProvider>
    </PageContextProvider>
  );
}
