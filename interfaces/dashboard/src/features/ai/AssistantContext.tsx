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
import { createContext, useContext, useState, useCallback, useEffect, useLayoutEffect, useMemo, useRef, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { usePreference } from '../../preferences';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { playUiCue } from '../../mods/sound/cue';

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
export const clampPanelW = (w: number) =>
  Math.min(PANEL_W_MAX, Math.max(PANEL_W_MIN, Math.round(w)));

/** The live width is published as a CSS var on <html>, NOT via inline
 *  styles: the divider drag can then write it straight to the DOM at
 *  pointer rate without re-rendering <Chat> (a context-state write per
 *  pointermove re-renders the whole message tree and janks the drag).
 *  React state stays the source of truth — it's committed once on release
 *  and re-synced here. */
export const PANEL_W_VAR = '--assistant-w';
/**
 * The product of the axes this panel sits under. The stored width is the
 * width AT 100% — so a drag has to divide by this before committing, and
 * the published variable multiplies by it again.
 */
export function panelScale(): number {
  const cs = getComputedStyle(document.documentElement);
  const n = (v: string) => {
    const f = parseFloat(cs.getPropertyValue(v));
    return Number.isFinite(f) && f > 0 ? f : 1;
  };
  return n('--size-panel') * n('--size-region-assistant');
}

/**
 * `w` is the width the user chose AT 100%, not the pixels on screen.
 *
 * It used to be published raw, so the panel stayed 420px however large
 * the interface got: at global 115% x assistant 130% the text inside grew
 * to 149.5% and the box did not, leaving two and a half of five starter
 * chips visible with 197px of the list hidden. `--sidebar-w` next door
 * already emitted the formula — the pattern existed, this variable had
 * just been left out of it.
 */
export const setPanelWidthVar = (w: number) =>
  document.documentElement.style.setProperty(
    PANEL_W_VAR,
    `calc(${w / 16}rem * var(--size-panel, 1) * var(--size-region-assistant, 1))`,
  );

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
  /** Samsara-style Expand: the docked panel becomes a full-canvas
   *  overlay (same chat, bigger room).  Persisted per device. */
  panelExpanded: boolean;
  setPanelExpanded: (v: boolean) => void;
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
  // The PERSISTED width lives in the preferences service; the live value
  // stays local state because a drag updates it every pointermove and we
  // only want to store the settled result (see the effect below).
  const { value: storedPanelWidth, setValue: setStoredPanelWidth } =
    usePreference('assistant.panelWidth');
  const [panelWidth, setPanelWidthState] = useState<number>(storedPanelWidth);
  const [panelResizing, setPanelResizing] = useState(false);
  const { value: panelExpanded, setValue: setPanelExpandedPref } =
    usePreference('assistant.expanded');
  const setPanelExpanded = useCallback((v: boolean) => {
    setPanelExpandedPref(v);
    // Maps/charts behind the overlay need a reflow when it toggles.
    setTimeout(() => window.dispatchEvent(new Event('resize')), 220);
  }, [setPanelExpandedPref]);
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
    setStoredPanelWidth(panelWidth);
  }, [panelResizing, panelWidth, setStoredPanelWidth]);

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
  /**
   * The phase the cue below has already announced.
   *
   * A ref and not state: the edge has to be read and written inside the
   * same call, and the sound must stay OUT of the updater — React is
   * free to run an updater twice, and a cue is not idempotent.
   */
  const soundedPhase = useRef<AssistantRunPhase>('idle');
  const setRunState = useCallback((phase: AssistantRunPhase, label = '') => {
    // An answer finishing is the app answering a question asked minutes
    // ago and then looked away from — the launcher already assumes
    // exactly that, which is why it renders a done-dot for a panel
    // nobody is watching. Nothing in this feature made a sound before.
    if (phase === 'done' && soundedPhase.current !== 'done') playUiCue('success');
    soundedPhase.current = phase;
    setRun((prev) => (prev.phase === phase && prev.label === label ? prev : { phase, label }));
  }, []);

  // Memoised: a fresh object each render would re-render every consumer
  // (notably the whole <Chat> tree) on any unrelated provider state change.
  const value = useMemo<AssistantState>(() => ({
    open, openPanel, closePanel, togglePanel, prefill, consumePrefill,
    runPhase: run.phase, runLabel: run.label, setRunState,
    panelWidth, setPanelWidth, panelResizing, setPanelResizing,
    panelExpanded, setPanelExpanded,
    headerSlot, setHeaderSlot,
  }), [
    open, openPanel, closePanel, togglePanel, prefill, consumePrefill,
    run.phase, run.label, setRunState,
    panelWidth, setPanelWidth, panelResizing, setPanelResizing,
    panelExpanded, setPanelExpanded,
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
      panelExpanded: false, setPanelExpanded: () => {},
      headerSlot: null, setHeaderSlot: () => {},
    };
  }
  return ctx;
}

/**
 * IS THE SUB-PAGE ACTUALLY ON SCREEN — one answer, for everyone who
 * needs it.
 *
 * `open` alone is not that answer: the panel also refuses to render on
 * the `/ai/*` routes (the full page IS the assistant there) and for a
 * persona without `can_view_ai_assistant`. While the panel was a fixed
 * overlay that mismatch was invisible — the content kept a right margin
 * with nothing in it. In the frame it would hide the page and show
 * nothing in its place, so the gate has to be in ONE place that the
 * shell and the panel both read.
 */
export function useAssistantDock(): { docked: boolean; expanded: boolean } {
  const { open, panelExpanded } = useAssistant();
  const { hasAny } = useViewPermissions();
  const location = useLocation();
  const docked = open
    && hasAny('can_view_ai_assistant')
    && !location.pathname.startsWith('/ai');
  return { docked, expanded: docked && panelExpanded };
}

/**
 * What the page card does when the sub-page opens beside it.
 *
 * The assistant is a SECOND PAGE in the same frame, not a sheet over
 * the first one — so this is no longer a margin that opens a hole for
 * an overlay to sit in. The row lays both pages out, and this says
 * whether the first one is in it:
 *
 *   · closed — nothing, the page has the row to itself
 *   · docked, wide — the two sit side by side with a gutter between
 *   · docked, narrow — the sub-page takes the whole view. Two pages do
 *     not fit in a phone's width, and the owner's call was that the
 *     sub-page wins there rather than hovering over a page nobody can
 *     read behind it.
 *   · expanded — the sub-page takes the row at every width
 *
 * `hidden`, not unmounted: display:none keeps the page's React state
 * and its scroll position, so coming back from the assistant lands
 * where you left. The centre gutter takes this same class — when the
 * page is not there, neither is the split between them.
 */
export function useDockedContentClass(): string {
  const { docked, expanded } = useAssistantDock();
  if (!docked) return '';
  return expanded ? 'hidden' : 'hidden xl:block';
}
