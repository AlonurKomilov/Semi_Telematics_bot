/* eslint-disable react-refresh/only-export-components --
   Standard React context module: the Provider and its consumer hooks
   are colocated by design.  react-refresh's "components only"
   expectation is a false positive for context files. */
/**
 * Page-context registry — "what is the user looking at right now."
 *
 * A feature page publishes a small descriptor via `usePublishContext`
 * while it's mounted; the assistant panel reads the latest one via
 * `useCurrentPageContext` and attaches it to each request so the model
 * understands "this truck" / "these rows" / the active filters.
 *
 * ONE mechanism, every feature: any page (present or future) registers
 * its own descriptor in its own file — no central map, no per-feature
 * branch anywhere.  A recruiter on Applications and an accountant on
 * Cost Reports each get a context-aware assistant automatically, because
 * the tools + snapshot the backend can reach are ALREADY permission-
 * masked per role.
 *
 * SECURITY: the descriptor is a PROMPT HINT ONLY.  The backend still
 * resolves tools + vehicle scope from the JWT — a spoofed page_context
 * can change what the model is *told* the user is viewing, never what
 * data it is *allowed* to fetch.  Same trust boundary as X-View-As.
 */
import {
  createContext, useContext, useState, useEffect, useMemo, type ReactNode,
} from 'react';

export interface PageContext {
  /** MUST equal a featureCatalog `id` (drives deep-links + the chip label). */
  feature: string;
  /** Human label for the "@ Maintenance" context chip. */
  label: string;
  /** The page's live filter state, whatever shape it is. */
  filters?: Record<string, unknown>;
  /** Rows/entities the user has selected. */
  selectedIds?: (string | number)[];
  /** The single entity in focus (an opened row/detail), if any. */
  focus?: { kind: string; id: string | number; label: string };
}

interface PageContextStore {
  current: PageContext | null;
  set: (ctx: PageContext | null) => void;
}

const Ctx = createContext<PageContextStore | null>(null);

export function PageContextProvider({ children }: { children: ReactNode }) {
  const [current, set] = useState<PageContext | null>(null);
  const value = useMemo(() => ({ current, set }), [current]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/**
 * Publish this page's context while the calling component is mounted;
 * clears it on unmount so a stale descriptor never outlives its page.
 * Pass `null` to publish nothing (e.g. before data loads).
 *
 * Serialize the descriptor's identity into the dep array at the call
 * site (or wrap in useMemo) so it doesn't re-publish every render.
 */
export function usePublishContext(ctx: PageContext | null): void {
  const store = useContext(Ctx);
  // Stable key so we only re-publish when the meaningful content changes,
  // not on every parent re-render (filters object identity churns).
  const key = ctx ? JSON.stringify(ctx) : '';
  useEffect(() => {
    if (!store) return;
    store.set(ctx);
    return () => store.set(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store, key]);
}

/** Latest published page context, or null.  Read by the assistant panel. */
export function useCurrentPageContext(): PageContext | null {
  return useContext(Ctx)?.current ?? null;
}
