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
import { type ReactNode } from 'react';
import { AssistantProvider } from './AssistantContext';
import { PageContextProvider } from './PageContext';
import AssistantPanel from './AssistantPanel';

export default function AssistantHost({ children }: { children: ReactNode }) {
  return (
    <PageContextProvider>
      <AssistantProvider>
        {children}
        <AssistantPanel />
      </AssistantProvider>
    </PageContextProvider>
  );
}
