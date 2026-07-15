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
import { createContext, useContext, useState, useCallback, useEffect, useLayoutEffect, useMemo, type ReactNode } from 'react';

/** Coarse run state the chat publishes so surfaces OUTSIDE the chat (the
 *  docked launcher chip) can show progress while the panel is hidden.
 *  Hiding the panel unmounts the chat but never aborts the request — the
 *  stream's closures keep calling setRunState, so the chip stays live. */
export type AssistantRunPhase = 'idle' | 'running' | 'done';

/** Docked-panel width bounds (px).  Drag/keyboard resizes clamp to these;
 *  the default matches the classic 420px slide-over. */
export const PANEL_W_DEFAULT = 420;
const PANEL_W_MIN = 320;
const PANEL_W_MAX = 680;
const PANEL_W_KEY = 'assistant.panelWidth';
export const clampPanelW = (w: number) =>
  Math.min(PANEL_W_MAX, Math.max(PANEL_W_MIN, Math.round(w)));

/** The live width is published as a CSS var on <html>, NOT via inline
 *  styles: the divider drag can then write it straight to the DOM at
 *  pointer rate without re-rendering <Chat> (a context-state write per
 *  pointermove re-renders the whole message tree and janks the drag).
 *  React state stays the source of truth — it's committed once on release
 *  and re-synced here. */
export const PANEL_W_VAR = '--assistant-w';
export const setPanelWidthVar = (w: number) =>
  document.documentElement.style.setProperty(PANEL_W_VAR, `${w}px`);

interface AssistantState {
  open: boolean;
  openPanel: (prefill?: string) => void;
  closePanel: () => void;
  togglePanel: () => void;
  /** One-shot question to send on open; the chat consumes then clears it. */
  prefill: string | null;
  consumePrefill: () => string | null;
  /** Coarse live run state for the launcher chip (see setRunState). */
  runPhase: AssistantRunPhase;
  /** Short human label for the phase ("Thinking" / "Running" / "Done"). */
  runLabel: string;
  setRunState: (phase: AssistantRunPhase, label?: string) => void;
  /** Docked-panel width (px), user-resizable via the divider; persisted. */
  panelWidth: number;
  setPanelWidth: (w: number) => void;
  /** True while the divider is being dragged — consumers disable their
   *  width/margin transitions so the panes track the pointer 1:1. */
  panelResizing: boolean;
  setPanelResizing: (v: boolean) => void;
  /** DOM node in the panel's chrome header where the docked <Chat> portals
   *  its New-chat / History controls (so they sit beside the ✕ and the chat
   *  area keeps the full height).  Null on the full-page route. */
  headerSlot: HTMLElement | null;
  setHeaderSlot: (el: HTMLElement | null) => void;
}

const AssistantCtx = createContext<AssistantState | null>(null);

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [prefill, setPrefill] = useState<string | null>(null);
  const [run, setRun] = useState<{ phase: AssistantRunPhase; label: string }>(
    { phase: 'idle', label: '' },
  );
  const [panelWidth, setPanelWidthState] = useState<number>(() => {
    try {
      const v = parseInt(localStorage.getItem(PANEL_W_KEY) ?? '', 10);
      return Number.isFinite(v) ? clampPanelW(v) : PANEL_W_DEFAULT;
    } catch { return PANEL_W_DEFAULT; }
  });
  const [panelResizing, setPanelResizing] = useState(false);
  const [headerSlot, setHeaderSlotState] = useState<HTMLElement | null>(null);
  const setHeaderSlot = useCallback((el: HTMLElement | null) => {
    setHeaderSlotState((prev) => (prev === el ? prev : el));
  }, []);
  const setPanelWidth = useCallback((w: number) => {
    setPanelWidthState(clampPanelW(w));
  }, []);
  // Keep the CSS var in step with state (before paint, so the restored
  // width never flashes at the default).  During a drag the divider writes
  // the var directly and this just re-affirms the committed value.
  useLayoutEffect(() => { setPanelWidthVar(panelWidth); }, [panelWidth]);
  // Persist once a drag settles (not per pointermove frame).
  useEffect(() => {
    if (panelResizing) return;
    try { localStorage.setItem(PANEL_W_KEY, String(panelWidth)); } catch { /* private mode */ }
  }, [panelResizing, panelWidth]);

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
  /** Equality-bailing setter — streaming models call this per thinking
   *  chunk with the same label; bailing keeps the chip from re-rendering
   *  hundreds of times per answer. */
  const setRunState = useCallback((phase: AssistantRunPhase, label = '') => {
    setRun((prev) => (prev.phase === phase && prev.label === label ? prev : { phase, label }));
  }, []);

  // Memoised: a fresh object each render would re-render every consumer
  // (notably the whole <Chat> tree) on any unrelated provider state change.
  const value = useMemo<AssistantState>(() => ({
    open, openPanel, closePanel, togglePanel, prefill, consumePrefill,
    runPhase: run.phase, runLabel: run.label, setRunState,
    panelWidth, setPanelWidth, panelResizing, setPanelResizing,
    headerSlot, setHeaderSlot,
  }), [
    open, openPanel, closePanel, togglePanel, prefill, consumePrefill,
    run.phase, run.label, setRunState,
    panelWidth, setPanelWidth, panelResizing, setPanelResizing,
    headerSlot, setHeaderSlot,
  ]);

  return (
    <AssistantCtx.Provider value={value}>
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
      runPhase: 'idle', runLabel: '', setRunState: () => {},
      panelWidth: PANEL_W_DEFAULT, setPanelWidth: () => {},
      panelResizing: false, setPanelResizing: () => {},
      headerSlot: null, setHeaderSlot: () => {},
    };
  }
  return ctx;
}
