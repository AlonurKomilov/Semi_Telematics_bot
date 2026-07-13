/* eslint-disable react-refresh/only-export-components --
   Standard React context module: the Provider component and its
   consumer hook are colocated by design (the recommended pattern).
   react-refresh's "components only" expectation is a false positive
   for context files. */
/**
 * Assistant panel state — global open/close + a one-shot "prefill this
 * question" bus so any surface can pop the panel open with a question
 * queued (e.g. a future "Ask about this" button on a page).
 *
 * This is deliberately tiny and UI-only: it holds no chat state (that
 * lives in the chat component + the backend threads).  It just decides
 * whether the slide-over is visible and carries an optional prefill
 * string across the open.
 */
import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';

interface AssistantState {
  open: boolean;
  openPanel: (prefill?: string) => void;
  closePanel: () => void;
  togglePanel: () => void;
  /** One-shot question to send on open; the chat consumes then clears it. */
  prefill: string | null;
  consumePrefill: () => string | null;
}

const AssistantCtx = createContext<AssistantState | null>(null);

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [prefill, setPrefill] = useState<string | null>(null);

  const openPanel = useCallback((q?: string) => {
    if (q) setPrefill(q);
    setOpen(true);
  }, []);
  const closePanel = useCallback(() => setOpen(false), []);
  const togglePanel = useCallback(() => setOpen((o) => !o), []);
  const consumePrefill = useCallback(() => {
    let v: string | null = null;
    setPrefill((cur) => { v = cur; return null; });
    return v;
  }, []);

  return (
    <AssistantCtx.Provider value={{ open, openPanel, closePanel, togglePanel, prefill, consumePrefill }}>
      {children}
    </AssistantCtx.Provider>
  );
}

/** Panel controls.  Safe outside the provider (returns a no-op shape) so
 *  a component that might render before the host mounts never crashes. */
export function useAssistant(): AssistantState {
  const ctx = useContext(AssistantCtx);
  if (!ctx) {
    return {
      open: false,
      openPanel: () => {}, closePanel: () => {}, togglePanel: () => {},
      prefill: null, consumePrefill: () => null,
    };
  }
  return ctx;
}
