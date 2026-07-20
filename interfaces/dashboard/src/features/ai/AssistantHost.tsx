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

/** Docked split view: on wide screens an open panel resizes the page
 *  CONTENT beside it (the shells push their own `<main>` via
 *  useDockedContentClass) so the assistant sits BELOW the full-width
 *  topbar and beside the content — the Samsara/Gemini pattern.  This
 *  wrapper no longer pushes the whole shell (that shrank the topbar
 *  too); it only fires a reflow so maps/charts re-measure when the
 *  panel opens or expands. */
function DockedContent({ children }: { children: ReactNode }) {
  const { open, panelExpanded } = useAssistant();
  useEffect(() => {
    // Maps (Google Maps doesn't observe container size) and any component
    // that caches its width reflow on a window resize — fire one after the
    // margin transition settles so charts/maps fill the new width.
    const id = setTimeout(() => window.dispatchEvent(new Event('resize')), 220);
    return () => clearTimeout(id);
  }, [open, panelExpanded]);
  return <>{children}</>;
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
