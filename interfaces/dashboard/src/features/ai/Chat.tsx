import { useState, useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useLocation } from 'react-router-dom';
import { Bot, ArrowUp, Square, Trash2, Copy, Check, RefreshCw, Sparkles, Pencil, Download, RotateCcw, ChevronDown, Zap, Brain, Microscope, Lightbulb, Loader2, ThumbsUp, ThumbsDown, Eye, History, SquarePen, Plus, Paperclip, FileText, Image as ImageIcon, X, type LucideIcon } from 'lucide-react';
import { Tip } from '../../components/tooltip';
import { Button } from '../../components/ui/button';
import { toneClasses, toneText } from '../../lib/status';
import { apiJSON, apiJSONAI, apiStreamChat, getWorkspace } from '../../api/client';
import { useShellConfig } from '../../hooks/useShellConfig';
import type { AIChatMessage, AIConversation, AIConversationsResponse, AIConversationMessagesResponse, AIProcessStep, AISummaryResponse, AIUsage, AITierChoice, AITierOption, AITierResponse } from '../../types';
import { formatAIResponse } from '../../utils/formatAI';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate, formatTime } from '../../utils/datetime';
import { DislikeReasonForm } from './sections/DislikeReasonForm';
import { ReferencedVehicles } from './sections/ReferencedVehicles';
import { thoughtKey, saveThought, getThought, deleteThoughtsForConversation } from './thoughtStore';
import { loadPendingAttachments, addPendingAttachment, removePendingAttachment, clearPendingAttachments, setPendingAttachments, loadConversationAttachments, bindConversationAttachments, removeConversationAttachment, clearConversationAttachments, listRecentAttachments, type PendingAttachment } from './attachmentStore';
import { DOCUMENT_ACCEPT, isDocumentFile, fileToAttachmentParts } from './documents';
import { useAssistant } from './AssistantContext';
import { useCurrentPageContext, useRouteFallbackContext } from './PageContext';
import { toolDeepLink } from './toolLinks';
import { useNavigate } from 'react-router-dom';
import { ArrowUpRight } from 'lucide-react';
import { renderArtifact, type Artifact } from './artifacts';
import { ScrollRegion } from '../../components/scrolling';
import { cn } from '@/lib/utils';
import { sizeRegion } from '@/lib/sizeRegion';
import { Card } from '@/components/ui/card';
import { usePreference } from '../../preferences';
import { scaledPx } from '@/lib/scaledLength';

// Extended message type with client-side timestamp
interface LocalMessage extends AIChatMessage {
  timestamp: Date;
  usage?: AIUsage;
  /** Structured tool outputs from the `done` event — drives the clickable
   *  "Vehicles in this answer" chips.  Absent on history-loaded messages
   *  (the DB stores text only). */
  toolResults?: unknown[];
  /** Tier label ("Fast" / "Thinking" / "Reasoning") that produced THIS
   *  answer, frozen at receipt — per-message so switching the tier picker
   *  never relabels past answers.  The raw model id never reaches the
   *  client (server-side vocabulary for the operator console/analytics).
   *  Absent on history-loaded messages (the DB stores text only). */
  modelTier?: string;
  /** Chain-of-thought that produced THIS answer — from the done event
   *  (reasoning-tier models, Claude thinking) or accumulated from the
   *  live `thinking` stream (Gemini).  Shown as a collapsible section
   *  on the bubble.  Absent on history-loaded messages and on models
   *  that ran without thinking. */
  reasoning?: string;
  /** Ordered process timeline (thinking + tool steps with result
   *  digests) — preferred over the flat `reasoning` blob when present. */
  process?: AIProcessStep[];
  /** Structured artifacts (tables/charts) rendered natively under the
   *  answer.  Browser-local like reasoning/process. */
  artifacts?: Artifact[];
  /** Files that rode WITH this user message (name + kind; the content
   *  stays device-local in attachmentStore).  Standard-chat look: the
   *  chip shows on the sent message, not stuck in the composer. */
  attachments?: { name: string; kind: string }[];
}

// No hardcoded loading messages — we show real tool activity from the stream

// Tier name → lucide icon component.  Backend's ``icon`` field on the
// tier response is intentionally ignored here: design.md §7 mandates
// lucide-react with no emoji as UI icons, so the icon choice is a
// pure frontend concern.  Four choices: the three real tiers plus
// ``auto`` (which routes to one of the three per-request).
const TIER_ICONS: Record<AITierChoice, LucideIcon> = {
  auto:      Sparkles,   // ✨ — picks the right tier per question
  fast:      Zap,        // ⚡ — quick, snappy
  thinking:  Brain,      // 🧠 — balanced reasoning
  reasoning: Microscope, // 🔬 — deep analysis
};

// Suggestion list is keyed by the active persona view — Owner previewing
// as Safety should see safety-flavored prompts.  The briefingLabel and
// chatSubject helpers used to live here too; they now come from
// useShellConfig so all persona-tuned copy stays in one place.
function getSuggestedQuestions(view?: string): string[] {
  switch (view) {
    case 'driver':
      return [
        'How is my truck doing today?',
        'What faults does my truck have?',
        'Show my recent safety events',
        'Is my truck due for maintenance?',
      ];
    case 'dispatcher':
      return [
        'Which trucks are rolling right now?',
        "Which vehicles haven't driven in 3 days?",
        'Show current fuel status',
        'Which routes are active today?',
      ];
    case 'safety':
      return [
        'Any critical faults today?',
        'Show safety events this week',
        'Which drivers had harsh braking?',
        'Trucks with stop engine lights?',
      ];
    case 'recruiter':
      return [
        'Show me pending driver applications',
        'How many people applied in the last 30 days?',
        'Which applicants are ready to hire?',
        'Any applications in screening?',
      ];
    case 'hr':
      return [
        'Show me pending driver applications',
        'How many people applied in the last 30 days?',
        'How many drivers do we have?',
        'Which applicants are ready to hire?',
      ];
    case 'accounting':
      return [
        'Show me fuel costs this month',
        'Which vehicles have the highest fuel spend?',
        "What's our total fuel cost?",
        'Show me the fuel cost summary',
      ];
    case 'admin':
    case 'owner':
      return [
        'Which trucks have active faults?',
        "Which vehicles haven't driven in 3 days?",
        'Show me fuel costs this month',
        'Any overdue maintenance tasks?',
      ];
    default:
      return [
        'How are my vehicles doing today?',
        'Which trucks have active faults?',
        "Which vehicles haven't driven in 3 days?",
        'Any overdue maintenance tasks?',
      ];
  }
}

// Topic hint shown in the empty-state subtitle, per persona — so a Recruiter
// isn't told to "ask about faults & maintenance".  Falls back to the
// fleet-ops topics for fleet/owner/admin.
function getChatTopics(view?: string): string {
  switch (view) {
    case 'recruiter': return 'driver applications and the hiring pipeline';
    case 'hr':        return 'applications, drivers, and coaching';
    case 'accounting':return 'fuel costs, spend, and cost per mile';
    case 'safety':    return 'safety events, faults, and coaching';
    case 'dispatcher':return 'locations, routes, and fuel status';
    default:          return 'vehicles, faults, fuel, events, maintenance';
  }
}

/** Append a LIVE step, first stamping the currently-last step's
 *  client-measured duration (it just closed because a new one is starting).
 *  Live view only — the finished timeline uses the server's `elapsed_ms`. */
function closeAndAppend(prev: AIProcessStep[], next: AIProcessStep): AIProcessStep[] {
  const now = Date.now();
  const closed = prev.length
    ? [...prev.slice(0, -1), stampElapsed(prev[prev.length - 1], now)]
    : prev;
  return [...closed, { ...next, startedAt: now }];
}
/** Stamp a step's client duration from its `startedAt` (idempotent). */
function stampElapsed(step: AIProcessStep, now: number): AIProcessStep {
  if (step.elapsed_ms != null || step.startedAt == null) return step;
  return { ...step, elapsed_ms: Math.max(0, now - step.startedAt) };
}

/** A step is a LOGICAL unit — one contiguous Thinking block, one Tool
 *  call — never a paragraph.  The timeline used to explode thinking text
 *  on blank lines ("one dot per thought"), which read fine for Gemini's
 *  short streamed fragments but turned a reasoning model's long
 *  chain-of-thought into a "211 steps" wall for a one-tool answer.  The
 *  backend already coalesces consecutive thinking chunks into one entry,
 *  so the process array IS the logical step list — render it directly. */

/** A thinking step, COLLAPSED to a one-line preview by default — the
 *  timeline reads as a step list (Thinking → Tool → …) and each step
 *  expands on demand.  A reasoning model's step can be an essay; inlining
 *  it whole buried the structure the timeline exists to show.  While a
 *  live step is still streaming it stays open (that's the progress
 *  feedback); it folds when the next step starts unless the user
 *  explicitly opened it. */
function ThinkingStep({ text, label, live = false }: {
  text: string;
  /** Collapsed-row caption — the MODE, not the content ("Reasoning" /
   *  "Thinking").  A content preview reads as a duplicate the moment the
   *  step expands (the full text starts with the same sentence). */
  label: string;
  live?: boolean;
}) {
  const [choice, setChoice] = useState<boolean | null>(null);
  const open = choice ?? live;
  const clean = (text || '').replace(/\*\*/g, '').trim();
  return (
    <div className="min-w-0">
      <button
        onClick={() => setChoice(!open)}
        aria-expanded={open}
        className="flex items-center gap-1 text-left text-2xs font-medium text-muted-foreground hover:text-foreground transition-colors py-1 -my-1 min-h-tap"
      >
        <span>{label}</span>
        <ChevronDown
          className={`shrink-0 transition-transform ${open ? 'rotate-180' : ''} size-3`}
          aria-hidden
        />
      </button>
      {open && (
        <div className="mt-0.5 text-2xs text-muted-foreground/80 whitespace-pre-wrap leading-relaxed">
          {clean}
        </div>
      )}
    </div>
  );
}

/** One row of the dot-and-line process timeline: marker column (dot /
 *  check / beacon) with a connector line down to the next row, content
 *  beside it. */
function TimelineRow({ marker, last, children }: {
  marker: ReactNode; last: boolean; children: ReactNode;
}) {
  return (
    <div className="flex gap-2">
      <div className="flex w-4 shrink-0 flex-col items-center">
        <span className="mt-1 flex h-3 items-center justify-center">{marker}</span>
        {!last && <span className="mt-1 w-px flex-1 bg-border" aria-hidden />}
      </div>
      <div className={`min-w-0 flex-1 ${last ? '' : 'pb-3'}`}>{children}</div>
    </div>
  );
}

/** "Open <Feature>" deep-link for a tool step — navigates to the
 *  feature's page and closes the panel.  Renders nothing for tools with
 *  no product page (get_weather etc.). */
function ToolStepLink({ toolName }: { toolName?: string }) {
  const link = toolName ? toolDeepLink(toolName) : null;
  const navigate = useNavigate();
  const { closePanel } = useAssistant();
  const { t } = useTranslation();
  if (!link) return null;
  return (
    <button
      onClick={() => { navigate(link.path); closePanel(); }}
      className="mt-1.5 inline-flex items-center gap-1.5 rounded-md border border-border bg-muted/50 px-2 py-1 text-2xs font-medium text-muted-foreground hover:text-foreground hover:border-ring hover:bg-muted transition-colors min-h-tap"
    >
      <ArrowUpRight className="size-3" aria-hidden />
      {t('chat.open_feature', { feature: t(link.labelKey) })}
    </button>
  );
}

/** Monotonic run id for launcher-chip status ownership.  MODULE-level on
 *  purpose: closing the panel UNMOUNTS <Chat> while its request keeps
 *  streaming (by design — hiding never cancels), so an instance ref would
 *  reset to 0 on reopen and the abandoned run's late events would stomp the
 *  new run's status.  Module scope outlives the remount; at most one <Chat>
 *  is mounted at a time (the panel is suppressed on /ai/*). */
let runSeq = 0;

/** Compact elapsed label for a step (e.g. "12s", "4m 3s"). */
function stepDuration(ms?: number): string | null {
  if (ms == null || ms < 0) return null;
  const s = Math.round(ms / 1000);
  if (s < 1) return '<1s';
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem ? `${m}m ${rem}s` : `${m}m`;
}

const THINKING_DOT = (
  <span className="size-1.5 rounded-full bg-muted-foreground/50" aria-hidden />
);
const ACTIVE_BEACON = (
  <span className="relative inline-flex size-2" aria-hidden>
    <span className="absolute inline-flex h-full w-full rounded-full bg-primary/60 animate-ping" />
    <span className="relative inline-flex size-2 rounded-full bg-primary" />
  </span>
);

/** `variant` decides the root chrome only:
 *  - 'page'  (default): the full-page /ai/chat view — fixed viewport height.
 *  - 'panel': embedded in the assistant slide-over — fills its container.
 *  All chat logic is identical across both; this is a layout switch. */
export default function Chat({ variant = 'page' }: { variant?: 'page' | 'panel' }) {
  const { t } = useTranslation();
  const tz = useTimezone();
  const location = useLocation();
  const { activeView, isDriverView, briefingLabel, chatSubject } = useShellConfig();
  // Copilot wiring: what the user is looking at (attached to each request)
  // and the panel's prefill bus (a queued question to send on open).
  // Rich page descriptor when the page publishes one; otherwise the
  // route-derived "user is on <feature>" fallback — the AI always knows
  // where the user is.
  const publishedContext = useCurrentPageContext();
  const routeContext = useRouteFallbackContext();
  const pageContext = publishedContext ?? routeContext;
  const { consumePrefill, setRunState, headerSlot } = useAssistant();

  // ── Chat state ───────────────────────────────────────────────
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  // Regenerate / thumbs buttons only operate on the LATEST AI bubble
  // — older bubbles can't be correlated with a specific ai_usage row
  // from the frontend (no row id flows through), so we'd risk
  // flipping had_reask / had_thumbs_up on the wrong row.  Disabling
  // on non-latest bubbles is the conservative fix; threading
  // usage_id end-to-end is a follow-up if users ever ask to give
  // feedback on older answers.
  const [regeneratingIdx, setRegeneratingIdx] = useState<number | null>(null);
  /** Tracks which feedback action the user clicked on each AI bubble,
   *  by index → 'up' | 'down'.  Keeps the icon filled after click so
   *  the user has visual confirmation their feedback registered, and
   *  prevents a second click from triggering a duplicate POST. */
  const [feedbackByIdx, setFeedbackByIdx] = useState<Record<number, 'up' | 'down'>>({});
  /** Bubble index whose dislike-reason form is currently open.
   *  null = no form open.  Opens on thumbs-down click; closes on
   *  Skip / Send / outside-click.  Only one form open at a time. */
  const [dislikeFormFor, setDislikeFormFor] = useState<number | null>(null);

  // ── Threads (History panel) ──────────────────────────────────
  /** The open thread's id.  null until the first reply of a brand-new
   *  chat comes back (the backend creates the thread lazily and returns
   *  its id on the done event). */
  const [conversationId, setConversationId] = useState<number | null>(null);
  /** True between "New chat" and the first send — tells the backend to
   *  force a fresh thread instead of reusing the latest one. */
  const [isNewChat, setIsNewChat] = useState(false);
  const [conversations, setConversations] = useState<AIConversation[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  /** Two-step confirm on New chat while a LONG run is in flight — a
   *  stray click shouldn't discard 30+ seconds of Reasoning work. */
  const [newChatConfirm, setNewChatConfirm] = useState(false);
  const newChatConfirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** One-time "step logs are stored on this device" note — honest
   *  expectation-setting for the browser-local thought-log policy. */
  const { value: thoughtNoteDismissed, setValue: setThoughtNoteDismissed } =
    usePreference('ai.thoughtNoteDismissed');
  /** Two-step delete confirm inside the panel — id armed for deletion. */
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null);
  /** Bubble indexes whose per-message Reasoning section is expanded. */
  const [reasoningExpanded, setReasoningExpanded] = useState<Set<number>>(new Set());
  const deleteConfirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const historyRef = useRef<HTMLDivElement>(null);

  // ── Streaming state ──────────────────────────────────────────
  /** Live answer text as it streams in (token-by-token), when the backend
   *  emits `delta` events.  Empty on the non-streaming path — the answer then
   *  arrives whole in the `done` event, exactly as before. */
  const [streamingText, setStreamingText] = useState('');
  /** Live reasoning text as it streams in (`thinking` events) — shown in a
   *  collapsible panel while the model works. */
  /** Last message that failed — shown in retry button */
  const [lastFailed, setLastFailed] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  /** Cycling index for pre-tool thinking phrases */
  /** REAL process steps building live from the event stream, in true
   *  order (thinking → tool → thinking → …) — the live bubble renders
   *  these, so the user watches the actual process instead of canned
   *  "figuring out…" phrases.  Same shape as the persisted timeline. */
  const [liveSteps, setLiveSteps] = useState<AIProcessStep[]>([]);
  /** Inner scroller of the live timeline — kept pinned to the newest
   *  step as thoughts stream in. */
  const liveScrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    liveScrollRef.current?.scrollTo({ top: liveScrollRef.current.scrollHeight });
  }, [liveSteps]);
  /** Seconds since the request started — the live bubble's elapsed
   *  counter, so a long Reasoning-tier turn reads as progress, not a
   *  hang.  Resets whenever loading flips. */
  const [elapsedSec, setElapsedSec] = useState(0);
  useEffect(() => {
    if (!loading) { setElapsedSec(0); return; }
    const started = Date.now();
    const id = setInterval(
      () => setElapsedSec(Math.floor((Date.now() - started) / 1000)), 1000,
    );
    return () => clearInterval(id);
  }, [loading]);
  /** Composer status strip: wall-clock start of the current run (ref — read
   *  at completion inside the send() closure where state would be stale) and
   *  a short-lived "Done · Xs" flash after a successful run.  null = no
   *  flash showing. */
  const runStartRef = useRef<number | null>(null);
  const [doneFlash, setDoneFlash] = useState<number | null>(null);
  const doneFlashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    return () => { if (doneFlashTimer.current) clearTimeout(doneFlashTimer.current); };
  }, []);
  /** Idle strip: whether the follow-up suggestion chips are expanded.
   *  Collapsed by default (compact-first — the count badge advertises
   *  them) and re-collapses whenever a new answer brings new chips. */
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  useEffect(() => { setSuggestionsOpen(false); }, [suggestions]);
  /** "+" quick-actions menu in the composer toolbar — attach a
   *  spreadsheet (the AI import flow) + the slash commands. */
  const [plusOpen, setPlusOpen] = useState(false);
  const plusRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!plusOpen) return;
    function onClick(e: MouseEvent) {
      if (plusRef.current && !plusRef.current.contains(e.target as Node)) setPlusOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [plusOpen]);
  /** Pending attachments — DEVICE-LOCAL by policy (attachmentStore): the
   *  file text rides inline on every send while the chip is attached
   *  (each turn parses transiently server-side, so a follow-up like
   *  "now import it" still has the grid), and is removed only by ✕. */
  const [attachments, setAttachments] = useState<PendingAttachment[]>(() => loadPendingAttachments());
  // Live mirror for async FileReader callbacks — two overlapping attaches
  // would otherwise compute against the render-time array and silently
  // drop the first file (reviewer W1).
  const attachmentsRef = useRef(attachments);
  useEffect(() => { attachmentsRef.current = attachments; }, [attachments]);
  /** Files bound to the OPEN conversation — sent with an earlier message
   *  and still riding every turn (the server holds nothing between
   *  turns; this is the stateless equivalent of a standard chat keeping
   *  the file "in the conversation").  Managed via the "+" menu. */
  const [convoFiles, setConvoFiles] = useState<PendingAttachment[]>([]);
  useEffect(() => {
    setConvoFiles(conversationId != null ? loadConversationAttachments(conversationId) : []);
  }, [conversationId]);
  /** File names being converted on-device right now (PDF text extraction
   *  / workbook parsing take seconds) — each shows a spinner chip that
   *  becomes the real chip when its content is ready. */
  const [preparing, setPreparing] = useState<string[]>([]);
  const [attachError, setAttachError] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const attachErrorTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  function flashAttachError(msg: string) {
    setAttachError(msg);
    if (attachErrorTimer.current) clearTimeout(attachErrorTimer.current);
    attachErrorTimer.current = setTimeout(() => setAttachError(''), 5000);
  }
  useEffect(() => () => { if (attachErrorTimer.current) clearTimeout(attachErrorTimer.current); }, []);
  async function attachFiles(files: FileList | File[]) {
    for (const file of Array.from(files)) {
      if (!isDocumentFile(file)) {
        flashAttachError(t('chat.attach_bad_type'));
        continue;
      }
      let parts: { name: string; content: string; kind: 'sheet' | 'text' | 'image' }[];
      // Placeholder chip while the device-side conversion runs — a big
      // PDF/workbook takes seconds, and silence reads as "stuck".
      setPreparing((prev) => [...prev, file.name]);
      try {
        // Everything converts ON THE DEVICE (documents.ts): CSV passes
        // through, Excel → CSV per sheet, PDF/TXT → extracted text.
        parts = await fileToAttachmentParts(file, [
          ...attachmentsRef.current.map((a) => a.name),
          ...convoFiles.map((a) => a.name),
        ]);
      } catch {
        flashAttachError(t('chat.attach_read_failed'));
        continue;
      } finally {
        setPreparing((prev) => {
          const i = prev.indexOf(file.name);
          return i === -1 ? prev : [...prev.slice(0, i), ...prev.slice(i + 1)];
        });
      }
      if (parts.length === 0) {
        flashAttachError(t('chat.attach_no_text'));
        continue;
      }
      for (const part of parts) {
        const res = addPendingAttachment(attachmentsRef.current, part.name, part.content, part.kind);
        if ('error' in res) {
          flashAttachError(t(res.error === 'too_large' ? 'chat.attach_too_large' : 'chat.attach_limit'));
          break;   // the cap applies to the rest of this workbook too
        }
        attachmentsRef.current = res.list;   // close the race before React re-renders
        setAttachments(res.list);
        // A budget eviction is a real loss — say it, never a vanishing chip.
        if (res.evicted.length > 0) {
          flashAttachError(t('chat.attach_evicted', { name: res.evicted.join(', ') }));
        }
      }
    }
  }
  /** Drag-and-drop onto the composer — counter-based so child enter/leave
   *  events don't flicker the highlight. */
  const [dragOver, setDragOver] = useState(false);
  const dragDepth = useRef(0);
  function onDragEnter(e: React.DragEvent) {
    if (!e.dataTransfer.types.includes('Files')) return;
    e.preventDefault();
    dragDepth.current += 1;
    setDragOver(true);
  }
  function onDragOver(e: React.DragEvent) {
    if (!e.dataTransfer.types.includes('Files')) return;
    e.preventDefault();
  }
  function onDragLeave(e: React.DragEvent) {
    if (!e.dataTransfer.types.includes('Files')) return;
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragOver(false);
  }
  function onDrop(e: React.DragEvent) {
    if (!e.dataTransfer.types.includes('Files')) return;
    e.preventDefault();
    dragDepth.current = 0;
    setDragOver(false);
    if (e.dataTransfer.files.length > 0) void attachFiles(e.dataTransfer.files);
  }
  /** Composer placeholder alternates between the primary ask and the "/"
   *  hint while the field is idle+empty (two short phrases beat one long
   *  one); it holds on the ask once focused, so it's stable to type against. */
  const [phIdx, setPhIdx] = useState(0);
  const [inputFocused, setInputFocused] = useState(false);
  useEffect(() => {
    if (inputFocused) { setPhIdx(0); return; }
    const id = setInterval(() => setPhIdx((i) => (i + 1) % 2), 4000);
    return () => clearInterval(id);
  }, [inputFocused]);

  // ── Model state ──────────────────────────────────────────────
  // ``currentModel`` resolves the tier into a model name so per-reply
  // bubbles ("Gemini 2.5 Flash") under each AI message can show what
  // actually produced the answer.  The user-facing knob is the tier;
  // the per-model picker was retired in 73894ed.  The picker itself is
  // a single-button dropdown — see the design pass that flagged the
  // emoji icons and always-visible-3-chip layout for revision.
  const [tiers, setTiers] = useState<AITierOption[]>([]);
  const [currentTier, setCurrentTier] = useState<AITierChoice>('fast');
  const [tierSwitching, setTierSwitching] = useState(false);
  const [tierOpen, setTierOpen] = useState(false);

  // ── Data-scope (trust badge) ─────────────────────────────────
  // Set from the `done` event's `scope` descriptor (server-resolved Vehicle
  // Access).  Per-user, not per-message, so we hold the latest and show one
  // persistent header badge for restricted users.  null = not yet known.
  const [scope, setScope] = useState<{ restricted: boolean; vehicle_count?: number } | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const modelRef = useRef<HTMLDivElement>(null);

  // Load conversation history + models on mount
  useEffect(() => {
    // Legacy entry points (overview KPI tile, AI summary card, the
    // /ai/summary redirect) link to ?tab=briefing.  The separate Briefing
    // tab is gone, so honour those links by auto-running the briefing inline
    // — sequenced AFTER history load so it appends to the conversation
    // instead of being clobbered by the history replace.
    const wantBriefing = new URLSearchParams(location.search).get('tab') === 'briefing';
    // Threads-first load: list the user's conversations, open the most
    // recent one.  No threads yet → clean empty chat (first message
    // creates one lazily server-side).
    apiJSON<AIConversationsResponse>(`/ai/conversations?workspace=${getWorkspace()}`)
      .then(async (d) => {
        const convs = d.conversations || [];
        setConversations(convs);
        if (convs.length > 0) {
          const m = await apiJSON<AIConversationMessagesResponse>(
            `/ai/conversations/${convs[0].id}/messages`,
          );
          setConversationId(convs[0].id);
          const loaded = (m.messages || []).map(msg => {
            // Thought logs live in localStorage (never the DB) — re-attach
            // by content-derived key so refresh restores them on this device.
            const local = getThought(thoughtKey(convs[0].id, msg.text));
            return {
              ...msg,
              // Use the backend timestamp when present so loaded scrollback
              // shows the real send time, not "12:34 PM" stamped at page load.
              timestamp: msg.ts ? new Date(msg.ts) : new Date(),
              modelTier: msg.model_tier || undefined,
              reasoning: msg.role === 'model' ? local?.reasoning : undefined,
              process: msg.role === 'model' ? local?.process : undefined,
              artifacts: msg.role === 'model' ? local?.artifacts : undefined,
              attachments: msg.role === 'user' ? local?.files : undefined,
            };
          });
          setMessages(loaded);
          // Restore the last answer's suggestion chips (browser-local too).
          const lastModel = [...loaded].reverse().find(msg => msg.role === 'model');
          if (lastModel) {
            const localLast = getThought(thoughtKey(convs[0].id, lastModel.text));
            if (localLast?.suggestions?.length) setSuggestions(localLast.suggestions);
          }
        }
      })
      .catch((e: unknown) => {
        // Don't swallow silently — an empty chat after history failed
        // to load is indistinguishable from a genuine first-visit
        // empty state, which is confusing.  Surface it.
        const msg = e instanceof Error ? e.message : String(e);
        setError(`Couldn't load chat history: ${msg}`);
      })
      .finally(() => {
        if (wantBriefing) {
          window.history.replaceState({}, document.title, '/ai/chat');
          runBriefing();
        }
      });
    // The bubble subtitle shows the TIER label carried on each done event
    // (never the raw model id — that's server-side vocabulary for the
    // operator console), so no /ai/models fetch is needed here.  The
    // user-facing picker is /ai/tier below.
    apiJSON<AITierResponse>('/ai/tier')
      .then((d) => {
        setTiers(d.tiers || []);
        setCurrentTier(d.current_tier);
      })
      .catch(() => {});
    // Mount-only: the ?tab=briefing read is a one-shot legacy-link honour,
    // not a value we re-run on navigation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Handle initial message passed via router state (e.g. "Diagnose faults on Truck 231")
  useEffect(() => {
    const state = location.state as { initialMessage?: string } | null;
    if (state?.initialMessage) {
      send(state.initialMessage);
      window.history.replaceState({}, document.title);
    }
    // Panel prefill: a question queued by openPanel('…') sends on mount.
    const queued = consumePrefill();
    if (queued) send(queued);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-scroll on new messages AND as live steps / streamed text grow,
  // so the newest step is always in view while the model works.
  useEffect(() => {
    // ``smooth`` only when the transcript gains something DISCRETE.
    // Firing a smooth scroll per streamed token starts an animation the
    // next token restarts, so the view chases the text and never
    // settles; ``auto`` keeps the tail pinned exactly while it streams.
    bottomRef.current?.scrollIntoView({
      behavior: streamingText ? 'auto' : 'smooth',
    });
  }, [messages, loading, liveSteps, streamingText]);

  /** Live status label: the CURRENT step's identity, not canned filler —
   *  the picker's tier while thinking ("Reasoning…"), the tool label while
   *  a tool runs.  Canned rotating phrases pretended to be steps; the real
   *  timeline covers that ground now. */
  const liveTierLabel = (currentTier !== 'auto'
    && tiers.find((x) => x.name === currentTier)?.label)
    || t('chat.thinking_live');
  const lastLiveStep = liveSteps[liveSteps.length - 1];
  const liveStatusLabel = lastLiveStep?.type === 'tool'
    ? (lastLiveStep.label || t('chat.running'))
    : liveTierLabel;

  // Close tier dropdown when clicking anywhere outside it.
  useEffect(() => {
    if (!tierOpen) return;
    function handleClick(e: MouseEvent) {
      if (modelRef.current && !modelRef.current.contains(e.target as Node)) {
        setTierOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [tierOpen]);

  // Close the History panel on outside click (same pattern as the tier picker)
  useEffect(() => {
    if (!historyOpen) return;
    function handleClick(e: MouseEvent) {
      if (historyRef.current && !historyRef.current.contains(e.target as Node)) {
        setHistoryOpen(false);
        setDeleteConfirmId(null);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [historyOpen]);

  // Cleanup delete-confirm timeout on unmount
  useEffect(() => {
    return () => { if (deleteConfirmTimer.current) clearTimeout(deleteConfirmTimer.current); };
  }, []);

  // ── Chat functions ───────────────────────────────────────────
  async function send(text: string) {
    if (!text.trim() || loading) return;
    // NEW files were attached THIS turn — only these get chips on the
    // sent message (a follow-up must not look like a re-upload).
    const newFiles = [...attachmentsRef.current];
    // The wire payload still carries the thread's bound files too — the
    // server holds nothing between turns, so every request re-rides
    // them (newest wins on a name clash, newest 3 within the caps).
    const sentFiles = (() => {
      const byName = new Map<string, PendingAttachment>();
      for (const f of [...convoFiles, ...newFiles]) byName.set(f.name, f);
      return [...byName.values()].sort((a, b) => a.t - b.t).slice(-3);
    })();
    const userMsg: LocalMessage = {
      role: 'user', text: text.trim(), timestamp: new Date(),
      attachments: newFiles.length > 0
        ? newFiles.map(({ name, kind }) => ({ name, kind }))
        : undefined,
    };
    // The files leave the composer the moment the message does (their
    // chips now live on the message above) and belong to the CHAT from
    // here on.  An existing thread binds immediately; a brand-new chat
    // binds when the done event brings the conversation id — and on
    // failure the files return to the composer so nothing is lost.
    const sentFromNewChat = newFiles.length > 0 && conversationId == null;
    if (newFiles.length > 0) {
      attachmentsRef.current = [];
      setAttachments([]);
      clearPendingAttachments();
      if (conversationId != null) {
        setConvoFiles(bindConversationAttachments(conversationId, newFiles));
      }
    }
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setSuggestions([]);
    setLoading(true);
    setError('');
    setLiveSteps([]);
    setStreamingText('');
    setLastFailed(null);
    // Status strip: start this run's clock, clear any lingering Done flash.
    runStartRef.current = Date.now();
    if (doneFlashTimer.current) clearTimeout(doneFlashTimer.current);
    setDoneFlash(null);
    // Publish to the launcher chip (visible while the panel is hidden).
    const myRun = ++runSeq;
    setRunState('running', t('chat.working'));

    // Cancel any in-flight request
    abortRef.current?.abort();
    const abort = new AbortController();
    abortRef.current = abort;

    try {
      let finalReply = '';
      let finalSuggestions: string[] = [];
      let finalUsage: AIUsage | undefined;
      let finalToolResults: unknown[] = [];
      let finalModelTier = '';
      let finalConversationId: number | null = null;
      let finalReasoning = '';
      let finalProcess: AIProcessStep[] = [];
      let finalArtifacts: Artifact[] = [];
      // Mirror of the streamed `reasoning` state — the state itself is
      // stale inside this closure, so accumulate locally too and attach
      // it to the finished message (Gemini streams thinking live; the
      // reasoning tier delivers it whole on `done`).
      let liveReasoning = '';

      await apiStreamChat(
        text.trim(),
        (event) => {
          if (event.type === 'tool') {
            // Closes (and client-times) the prior step, starts this tool's clock.
            setLiveSteps((prev) => closeAndAppend(prev, { type: 'tool', name: event.name, label: event.label }));
            if (runSeq === myRun) setRunState('running', t('chat.running'));
          } else if (event.type === 'thinking') {
            liveReasoning += event.text;
            if (runSeq === myRun) setRunState('running', t('chat.thinking_live'));
            // Coalesce consecutive thinking chunks into one growing step —
            // Gemini streams token-sized fragments (keep its startedAt).
            setLiveSteps((prev) => {
              const last = prev[prev.length - 1];
              if (last?.type === 'thinking') {
                return [...prev.slice(0, -1), { ...last, text: (last.text || '') + event.text }];
              }
              return closeAndAppend(prev, { type: 'thinking', text: event.text });
            });
          } else if (event.type === 'delta') {
            setStreamingText((prev) => prev + event.text);
          } else if (event.type === 'done') {
            finalReply = event.reply;
            finalSuggestions = event.suggestions || [];
            finalUsage = event.usage as unknown as AIUsage | undefined;
            finalToolResults = event.tool_results || [];
            finalModelTier = event.model_tier || '';
            finalConversationId = event.conversation_id ?? null;
            finalReasoning = event.reasoning || '';
            finalProcess = event.process || [];
            finalArtifacts = (event.artifacts || []) as Artifact[];
            if (event.scope) setScope(event.scope);
          } else if (event.type === 'error') {
            throw new Error(event.message);
          }
        },
        abort.signal,
        {
          conversationId, newConversation: isNewChat, pageContext,
          // Device-held file text: this thread's bound files + the new
          // chips — the server parses per turn and stores nothing.
          attachments: sentFiles.length > 0
            ? sentFiles.map(({ name, content, kind }) => ({ name, content, kind }))
            : undefined,
        },
      );

      if (!finalReply) throw new Error('No response received');
      const aiMsg: LocalMessage = { role: 'model', text: finalReply, timestamp: new Date(), usage: finalUsage, toolResults: finalToolResults, modelTier: finalModelTier || undefined, reasoning: (finalReasoning || liveReasoning).trim() || undefined, process: finalProcess.length > 0 ? finalProcess : undefined, artifacts: finalArtifacts.length > 0 ? finalArtifacts : undefined };
      // Thought logs + suggestion chips + artifacts are browser-local by
      // policy (the server stores only text + tier) — stash them so
      // refresh / thread-reopen restores them.
      if (aiMsg.reasoning || aiMsg.process || aiMsg.artifacts || finalSuggestions.length > 0) {
        saveThought(thoughtKey(finalConversationId, finalReply), {
          reasoning: aiMsg.reasoning, process: aiMsg.process,
          artifacts: aiMsg.artifacts, suggestions: finalSuggestions,
        });
      }
      // Standard-chat lifecycle: the chips live ON the sent message
      // (persisted device-local for thread-reopen); a new chat's files
      // bind to the thread now that its id exists.
      if (userMsg.attachments?.length) {
        saveThought(thoughtKey(finalConversationId, userMsg.text), { files: userMsg.attachments });
      }
      if (newFiles.length > 0 && finalConversationId != null) {
        setConvoFiles(bindConversationAttachments(finalConversationId, newFiles));
      }
      setMessages((prev) => [...prev, aiMsg]);
      setSuggestions(finalSuggestions);
      // Status strip: flash "Done · Xs" for a few seconds after a successful
      // run (aborts and errors skip this — they have their own signals).
      if (runStartRef.current != null) {
        setDoneFlash(Date.now() - runStartRef.current);
        doneFlashTimer.current = setTimeout(() => setDoneFlash(null), 4000);
      }
      // Launcher chip mirrors the flash, then rests.
      if (runSeq === myRun) {
        setRunState('done', t('chat.done_step'));
        setTimeout(() => {
          if (runSeq === myRun) setRunState('idle');
        }, 4000);
      }
      // Adopt the thread the backend answered in (created lazily for a
      // new chat / a stale id) and refresh the History panel's list so
      // the thread title + activity time stay current.
      if (finalConversationId != null) setConversationId(finalConversationId);
      setIsNewChat(false);
      void loadConversations();
    } catch (e) {
      // Chip rests on any failure/abort (guarded — a superseding run owns it).
      if (runSeq === myRun) setRunState('idle');
      // A failed/aborted NEW chat has no thread holding the files —
      // return them to the composer so a retry still carries them.
      if (sentFromNewChat) {
        const restored = setPendingAttachments(newFiles);
        attachmentsRef.current = restored;
        setAttachments(restored);
      }
      if (e instanceof Error && e.name === 'AbortError') return;
      const msg = e instanceof Error ? e.message : 'Failed to get response';
      if (msg.includes('429') || msg.toLowerCase().includes('rate limit') || msg.toLowerCase().includes('too many')) {
        setError('Too many messages — please wait a moment before sending again.');
      } else {
        setError(msg);
      }
      setLastFailed(text.trim());
    } finally {
      setLoading(false);
      setLiveSteps([]);
      setStreamingText('');
        inputRef.current?.focus();
    }
  }

  /** Run the operations briefing as an inline chat turn.  The briefing is a
   *  curated executive summary from a dedicated endpoint (/ai/summary) — we
   *  render it as a normal AI message so it lives in the conversation and can
   *  be followed up on, rather than living in a separate tab.  Triggered by
   *  the `/briefing` command or the empty-state chip. */
  async function runBriefing() {
    if (loading) return;
    const userMsg: LocalMessage = { role: 'user', text: briefingLabel, timestamp: new Date() };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setSuggestions([]);
    setError('');
    setLoading(true);
    const myRun = ++runSeq;
    setRunState('running', t('chat.working'));
    try {
      const data = await apiJSONAI<AISummaryResponse>('/ai/summary', { method: 'POST' });
      const aiMsg: LocalMessage = { role: 'model', text: data.summary, timestamp: new Date() };
      setMessages((prev) => [...prev, aiMsg]);
      setSuggestions(data.suggestions || []);
      if (runSeq === myRun) {
        setRunState('done', t('chat.done_step'));
        setTimeout(() => {
          if (runSeq === myRun) setRunState('idle');
        }, 4000);
      }
    } catch (e) {
      if (runSeq === myRun) setRunState('idle');
      setError(e instanceof Error ? e.message : 'Failed to generate briefing');
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  /** Slash commands typed in the chat input.  Extensible — add an entry here
   *  to expose a new shortcut (e.g. /parked, /undriven).  Discoverable via the
   *  `/` menu above the input and the empty-state chips.
   *
   *  The briefing is available to EVERY persona: its content is derived
   *  server-side from the role's effective permissions (BRIEFING_TOPICS), so
   *  a recruiter gets a hiring-pipeline brief, accounting a cost brief, etc. */
  const SLASH_COMMANDS: { name: string; label: string; run: () => void }[] = [
    { name: 'briefing', label: briefingLabel, run: runBriefing },
  ];

  /** Dispatch the input box: a known `/command` runs its action, anything
   *  else is sent to the AI as a normal message. */
  function submit(text: string) {
    const trimmed = text.trim();
    if (!trimmed || loading) return;
    // A file is still being read on-device — sending now would silently
    // drop it from the turn the user attached it for.
    if (preparing.length > 0) {
      flashAttachError(t('chat.attach_wait'));
      return;
    }
    const cmd = SLASH_COMMANDS.find((c) => `/${c.name}` === trimmed.toLowerCase());
    if (cmd) {
      setInput('');
      cmd.run();
      return;
    }
    send(text);
  }

  /** Abort the in-flight streaming request.  apiStreamChat's fetch rejects
   *  with AbortError, which send()'s catch swallows (and its finally clears
   *  loading) — but we optimistically clear the streaming UI here too so the
   *  Stop feels instant rather than waiting for the rejection to propagate. */
  function stopGeneration() {
    abortRef.current?.abort();
    setLoading(false);
    setLiveSteps([]);
    setStreamingText('');
  }

  function switchTier(tier: AITierChoice) {
    if (tierSwitching || tier === currentTier) {
      setTierOpen(false);
      return;
    }
    // Optimistic: reflect the choice and close the dropdown IMMEDIATELY,
    // then persist in the background.  The PUT can take a couple of
    // seconds (the backend warms the tier's model), and holding the
    // dropdown open with disabled buttons for that long read as a
    // frozen UI.  On failure the previous tier is restored + an error
    // banner explains why.  Chat requests racing the PUT are safe —
    // the server re-stages the model per request from the DB-stored
    // tier, so the worst case is one message on the old tier.
    const prev = currentTier;
    setCurrentTier(tier);
    setTierOpen(false);
    setTierSwitching(true);
    apiJSON<{
      ok: boolean;
      tier: AITierChoice;
      resolved_model: string | null;
      resolved_model_display: string | null;
    }>('/ai/tier', {
      method: 'PUT',
      body: { tier },
    })
      .then((r) => setCurrentTier(r.tier))
      .catch((e: unknown) => {
        setCurrentTier(prev);
        setError(e instanceof Error ? e.message : 'Failed to switch tier');
      })
      .finally(() => setTierSwitching(false));
  }

  // ── Thread (History panel) actions ───────────────────────────

  async function loadConversations() {
    try {
      const d = await apiJSON<AIConversationsResponse>(`/ai/conversations?workspace=${getWorkspace()}`);
      setConversations(d.conversations || []);
    } catch { /* panel keeps its last list; next open retries */ }
  }

  function dismissThoughtNote() {
    setThoughtNoteDismissed(true);
  }

  /** Start a fresh thread: clean slate locally; the backend creates the
   *  thread lazily on the first message (so abandoning an empty "new
   *  chat" leaves no hollow row in the panel).  While a LONG run is in
   *  flight (>10s), the first click arms a confirm instead of silently
   *  discarding the work. */
  function newChat() {
    if (loading && elapsedSec > 10 && !newChatConfirm) {
      setNewChatConfirm(true);
      if (newChatConfirmTimer.current) clearTimeout(newChatConfirmTimer.current);
      newChatConfirmTimer.current = setTimeout(() => setNewChatConfirm(false), 3000);
      return;
    }
    setNewChatConfirm(false);
    abortRef.current?.abort();
    setMessages([]);
    setSuggestions([]);
    setError('');
    setLastFailed(null);
    setLiveSteps([]);
    setConversationId(null);
    setIsNewChat(true);
    setHistoryOpen(false);
    inputRef.current?.focus();
  }

  /** Open a previous thread from the History panel. */
  async function openConversation(conv: AIConversation) {
    abortRef.current?.abort();
    setHistoryOpen(false);
    setDeleteConfirmId(null);
    try {
      const m = await apiJSON<AIConversationMessagesResponse>(
        `/ai/conversations/${conv.id}/messages`,
      );
      setConversationId(conv.id);
      setIsNewChat(false);
      const loaded = (m.messages || []).map(msg => {
        const local = getThought(thoughtKey(conv.id, msg.text));
        return {
          ...msg,
          timestamp: msg.ts ? new Date(msg.ts) : new Date(),
          modelTier: msg.model_tier || undefined,
          reasoning: msg.role === 'model' ? local?.reasoning : undefined,
          process: msg.role === 'model' ? local?.process : undefined,
          artifacts: msg.role === 'model' ? local?.artifacts : undefined,
          attachments: msg.role === 'user' ? local?.files : undefined,
        };
      });
      setMessages(loaded);
      // Restore the reopened thread's last suggestion chips too.
      const lastModel = [...loaded].reverse().find(msg => msg.role === 'model');
      const localLast = lastModel
        ? getThought(thoughtKey(conv.id, lastModel.text)) : null;
      setSuggestions(localLast?.suggestions ?? []);
      setError('');
      setLastFailed(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(`Couldn't open that chat: ${msg}`);
    }
  }

  /** Arm the row's inline delete confirm — the trash swaps for explicit
   *  Delete / Cancel buttons, so nobody has to guess "press it twice".
   *  Auto-disarms after 5s of inaction. */
  function armDelete(id: number) {
    setDeleteConfirmId(id);
    if (deleteConfirmTimer.current) clearTimeout(deleteConfirmTimer.current);
    deleteConfirmTimer.current = setTimeout(() => setDeleteConfirmId(null), 5000);
  }

  /** Per-chat delete — called by the armed row's explicit Delete button. */
  async function deleteConversation(conv: AIConversation) {
    setDeleteConfirmId(null);
    await apiJSON(`/ai/conversations/${conv.id}`, { method: 'DELETE' }).catch(() => {});
    deleteThoughtsForConversation(conv.id);  // local thought logs go with it
    clearConversationAttachments(conv.id);   // and its device-held files
    if (conv.id === conversationId) setConvoFiles([]);
    await loadConversations();
    // Deleting the OPEN thread clears the view — next message starts fresh.
    if (conv.id === conversationId) {
      abortRef.current?.abort();
      setMessages([]);
      setSuggestions([]);
      setConversationId(null);
      setIsNewChat(true);
    }
  }

  /** Copy an AI message as clean plain text (no HTML tags). */
  function copyMessage(text: string, idx: number) {
    // Parse the raw HTML into a DOM tree, then extract plain text
    const div = document.createElement('div');
    div.innerHTML = text;
    // Replace <br> and block-level tags with newlines before extracting text
    div.querySelectorAll('br').forEach((br) => br.replaceWith('\n'));
    div.querySelectorAll('p, li, h1, h2, h3, h4, h5, h6').forEach((el) => {
      el.insertAdjacentText('beforebegin', '');
      el.insertAdjacentText('afterend', '\n');
    });
    // Collapse multiple blank lines
    const plain = (div.textContent || div.innerText || '')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
    navigator.clipboard.writeText(plain).catch(() => {});
    setCopiedIdx(idx);
    setTimeout(() => setCopiedIdx(null), 1500);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit(input);
    }
  }

  /** Regenerate the AI response at messages[aiIdx].
   *
   *  Two-step flow:
   *  1. POST /ai/feedback/regenerate — flips ``had_reask=true`` on
   *     the prior response's ai_usage row so the router penalises
   *     the model that produced the unsatisfying answer.  Best-
   *     effort: a transient network error doesn't block the re-fire.
   *  2. Find the user message immediately preceding the AI bubble
   *     (walking ``messages`` backward) and call ``send()`` with
   *     that exact text — same prompt, fresh chance for the router
   *     to land on a better model (especially under Auto tier).
   *
   *  Intentionally limited to the latest AI bubble: the dashboard
   *  doesn't currently thread ai_usage row ids through to the
   *  client, so flipping had_reask on a non-latest bubble would
   *  target the wrong row.  See the regeneratingIdx comment for
   *  the follow-up path.
   */
  async function regenerateMessage(aiIdx: number) {
    if (regeneratingIdx !== null) return;
    // Walk back to find the user prompt that produced this answer.
    let priorUserText = '';
    for (let i = aiIdx - 1; i >= 0; i--) {
      if (messages[i].role === 'user') {
        priorUserText = messages[i].text;
        break;
      }
    }
    if (!priorUserText) return;
    setRegeneratingIdx(aiIdx);
    try {
      // Fire-and-forget the dissatisfaction signal — if it fails,
      // the regenerate still proceeds; we just lose this one
      // telemetry row.
      apiJSON('/ai/feedback/regenerate', { method: 'POST', body: {} }).catch(() => {});
      await send(priorUserText);
    } finally {
      setRegeneratingIdx(null);
    }
  }

  /** Record a thumbs-up or thumbs-down vote on the AI bubble at aiIdx.
   *
   *  Two distinct endpoints (``/feedback/thumbs-up`` flips
   *  ``had_thumbs_up``, ``/feedback/thumbs-down`` flips ``had_reask``
   *  via a different code path) so the scorer can distinguish
   *  positive from negative signals.  Fire-and-forget — a failed
   *  POST silently drops the vote rather than nagging the user.
   *
   *  Local state ``feedbackByIdx`` records the click so the icon
   *  stays filled afterwards and a second click on the same vote is
   *  a no-op.  Clicking the OPPOSITE vote after one is registered
   *  *does* fire a fresh POST and overwrite the local marker — users
   *  changing their mind is a real flow.
   */
  function voteOnMessage(aiIdx: number, kind: 'up' | 'down') {
    if (feedbackByIdx[aiIdx] === kind) {
      // Already voted this way.  On thumbs-down, treat the second
      // click as "re-open the reason form" so the user can change
      // their mind about WHY — the initial had_reask flip is
      // idempotent so re-firing the bare POST is fine.
      if (kind === 'down') setDislikeFormFor(aiIdx);
      return;
    }
    setFeedbackByIdx((s) => ({ ...s, [aiIdx]: kind }));
    // Switching from down to up (or vice-versa) closes any open form.
    setDislikeFormFor(kind === 'down' ? aiIdx : null);
    const endpoint = kind === 'up' ? '/ai/feedback/thumbs-up' : '/ai/feedback/thumbs-down';
    apiJSON(endpoint, { method: 'POST', body: {} }).catch(() => {});
  }

  function editMessage(text: string) {
    setInput(text);
    setTimeout(() => {
      if (inputRef.current) {
        inputRef.current.focus();
        inputRef.current.style.height = 'auto';
        inputRef.current.style.height = Math.min(inputRef.current.scrollHeight, 120) + 'px';
      }
    }, 0);
  }

  function downloadTranscript(
    rows: { role: string; text: string; timestamp: Date }[],
  ) {
    const lines = rows.map((m) => {
      const time = formatDate(m.timestamp, { timeZone: tz });
      const who = m.role === 'user' ? 'You' : 'AI';
      // Strip HTML tags for plain-text export
      const plainText = m.text.replace(/<[^>]+>/g, '');
      return `[${time}] ${who}:\n${plainText}\n`;
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `4truck-chat-${new Date().toISOString().slice(0, 10)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }

  /** Per-chat export from the History panel.  The open thread exports
   *  what's on screen; other threads are fetched first. */
  async function exportConversation(conv: AIConversation) {
    if (conv.id === conversationId) {
      downloadTranscript(messages);
      return;
    }
    try {
      const m = await apiJSON<AIConversationMessagesResponse>(
        `/ai/conversations/${conv.id}/messages`,
      );
      downloadTranscript((m.messages || []).map(msg => ({
        role: msg.role,
        text: msg.text,
        timestamp: msg.ts ? new Date(msg.ts) : new Date(),
      })));
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(`Couldn't export that chat: ${msg}`);
    }
  }

  const suggestedQuestions = getSuggestedQuestions(activeView);

  // Slash-command autocomplete: when the input begins with "/", show the
  // matching commands above the input so the feature is discoverable.
  const slashMatches = input.startsWith('/')
    ? SLASH_COMMANDS.filter((c) => `/${c.name}`.startsWith(input.trim().toLowerCase()))
    : [];

  // Index of the most recent AI message in ``messages``.  The
  // Regenerate button is only shown on that bubble (see the
  // regeneratingIdx comment for why) — older AI bubbles can't be
  // safely re-flagged from the client today.
  const lastAiIdx = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'model') return i;
    }
    return -1;
  })();

  // Trust badge + thread controls are extracted so the DOCKED panel can
  // portal the controls into its chrome header (beside ✕), while the
  // full-page view renders them inline.  Rendered in exactly one place per
  // variant, so the historyRef / dropdown live in a single subtree.
  const trustBadge = scope?.restricted ? (
    <Tip label="Your access is limited to these vehicles — answers cover only them.">
      <span className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-2xs font-medium bg-muted text-muted-foreground border border-border whitespace-nowrap">
        <Eye className="size-3" aria-hidden />
        Answering for your {scope.vehicle_count ?? 0} vehicle{scope.vehicle_count === 1 ? '' : 's'}
      </span>
    </Tip>
  ) : null;

  const threadControls = (
    <>
      {/* New chat — ghost icon-button (the topbar's flat convention: these
          sit on chrome, so no fill/border of their own).  The two-step
          confirm borrows the destructive variant's soft tint. */}
      <Tip label={newChatConfirm ? t('chat.confirm_discard') : t('chat.new_chat')}>
        <Button
          variant={newChatConfirm ? 'destructive' : 'ghost'}
          size="icon"
          onClick={newChat}
          disabled={messages.length === 0 && conversationId === null && !loading}
          aria-label={newChatConfirm ? t('chat.confirm_discard') : t('chat.new_chat')}
          className="shrink-0 text-muted-foreground"
        >
          <SquarePen aria-hidden />
        </Button>
      </Tip>

      {/* History — previous chats with per-chat export / delete. */}
      <div className="relative" ref={historyRef}>
        <Tip label={t('chat.history')}>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => { setHistoryOpen(!historyOpen); setDeleteConfirmId(null); if (!historyOpen) void loadConversations(); }}
            aria-haspopup="listbox"
            aria-expanded={historyOpen}
            aria-label={t('chat.history')}
            className="shrink-0 text-muted-foreground"
          >
            <History aria-hidden />
          </Button>
        </Tip>
        {historyOpen && (
          <Card padding="none" className="absolute right-0 top-full mt-1 z-50 w-80 shadow-xl max-h-96 overflow-y-auto">
            {conversations.length === 0 ? (
              <div className="px-3 py-4 text-xs text-muted-foreground text-center">
                {t('chat.no_previous_chats')}
              </div>
            ) : (
              conversations.map((conv) => (
                <div
                  key={conv.id}
                  className={`group/conv flex items-center gap-2 px-3 py-2 transition-colors cursor-pointer ${
                    conv.id === conversationId
                      ? 'bg-primary/10'
                      : 'hover:bg-muted'
                  }`}
                  onClick={() => void openConversation(conv)}
                >
                  <div className="min-w-0 flex-1">
                    <div className={`text-sm truncate ${conv.id === conversationId ? 'text-primary font-medium' : 'text-foreground'}`}>
                      {conv.title}
                    </div>
                    <div className="text-2xs text-muted-foreground">
                      {formatDate(new Date(conv.updated_at), { timeZone: tz })}
                      {' · '}
                      {conv.message_count} {t('chat.messages_count')}
                    </div>
                  </div>
                  {deleteConfirmId === conv.id ? (
                    /* Armed: explicit Delete / Cancel — two distinct
                       targets, never "press the same icon twice". */
                    <div
                      className="flex items-center gap-1"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        onClick={() => void deleteConversation(conv)}
                        className="px-2 py-1 rounded-md text-2xs font-medium bg-destructive/15 text-destructive hover:bg-destructive/25 transition-colors min-h-tap"
                      >
                        {t('chat.delete_yes')}
                      </button>
                      <button
                        onClick={() => setDeleteConfirmId(null)}
                        className="px-2 py-1 rounded-md text-2xs text-muted-foreground hover:text-foreground transition-colors min-h-tap"
                      >
                        {t('chat.cancel')}
                      </button>
                    </div>
                  ) : (
                    <>
                      <Tip label={t('chat.export_conversation')}>
                        <button
                          onClick={(e) => { e.stopPropagation(); void exportConversation(conv); }}
                          aria-label={t('chat.export_conversation')}
                          className="opacity-0 group-hover/conv:opacity-100 inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:text-foreground transition-opacity min-h-tap min-w-tap"
                        >
                          <Download className="size-3.5" />
                        </button>
                      </Tip>
                      <Tip label={t('chat.delete_chat')}>
                        <button
                          onClick={(e) => { e.stopPropagation(); armDelete(conv.id); }}
                          aria-label={t('chat.delete_chat')}
                          className="opacity-0 group-hover/conv:opacity-100 inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:text-destructive transition-opacity min-h-tap min-w-tap"
                        >
                          <Trash2 className="size-3.5" />
                        </button>
                      </Tip>
                    </>
                  )}
                </div>
              ))
            )}
          </Card>
        )}
      </div>
    </>
  );

  return (
    // Both variants are h-full now: the page branch used to subtract a
    // hardcoded 6rem for the shell frame, which was 0.5rem SHORT at lg
    // and would drift further the moment Size scales that padding.
    // The assistant is its own Size REGION — it is a conversation, and
    // reading comfort there is a different judgement from the density
    // someone wants on an operations screen. See lib/sizeRegion.ts.
    <div className="flex flex-col h-full" style={sizeRegion('assistant')}>
      {/* ── Header ──────────────────────────────────────────────
          Panel mode: the New-chat / History controls PORTAL into the panel's
          chrome header (beside ✕, below) so the chat area keeps the full
          height; only a slim trust badge stays here.  Full page: inline
          header with title + badge + controls. */}
      {variant === 'panel' ? (
        trustBadge && <div className="mb-2 flex-shrink-0">{trustBadge}</div>
      ) : (
        <div className="flex items-center justify-between flex-shrink-0 mb-4">
          <div className="flex items-center gap-3 min-w-0">
            <h1 className="text-xl font-semibold flex items-center gap-2">
              <Bot className="text-primary size-5" />
              AI Assistant
            </h1>
            {trustBadge}
          </div>
          <div className="flex items-center gap-1">{threadControls}</div>
        </div>
      )}
      {variant === 'panel' && headerSlot && createPortal(threadControls, headerSlot)}

      {/* Messages area */}
      <ScrollRegion label="Conversation" className="flex-1 space-y-4 pr-1 min-h-0">
        {messages.length === 0 && !loading && (
          <div className="text-center text-muted-foreground mt-16">
            <Bot className="mx-auto mb-3 text-primary/40 size-10" />
            <p className="text-lg font-semibold">{t('chat.title')}</p>
            <p className="text-sm mt-1">
              {isDriverView
                ? t('chat.ask_driver')
                : `Ask anything about ${chatSubject} — ${getChatTopics(activeView)}.`}
            </p>
            {/* Primary action: the operations briefing.  Lives here (and as the
                /briefing command) instead of a separate tab. */}
            <div className="mt-6 flex flex-col items-center gap-3">
              <Tip label={`Generate your ${briefingLabel.toLowerCase()} — or type /briefing`}>
                <button
                  onClick={runBriefing}
                  className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-primary/10 text-primary border border-primary/20 hover:bg-primary/15 transition-colors min-h-tap"
                >
                  <Sparkles className="size-4" aria-hidden />
                  {briefingLabel}
                </button>
              </Tip>
              <div className="flex flex-wrap justify-center gap-2">
                {suggestedQuestions.map((q) => (
                  <button
                    key={q}
                    onClick={() => send(q)}
                    className="px-3 py-2 text-sm rounded-lg bg-muted hover:bg-muted/80 text-foreground/80 border border-border transition-colors"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            {msg.role === 'user' ? (
              <div className="max-w-[80%] group">
                {(msg.attachments?.length ?? 0) > 0 && (
                  <div className="mb-1 flex flex-wrap justify-end gap-1.5">
                    {msg.attachments!.map((f) => (
                      <span
                        key={f.name}
                        className="inline-flex items-center gap-1 rounded-md border border-border bg-muted/60 px-2 py-0.5 text-xs text-foreground"
                      >
                        {f.kind === 'image'
                          ? <ImageIcon className="shrink-0 text-muted-foreground size-3" aria-hidden />
                          : f.kind === 'text'
                            ? <FileText className="shrink-0 text-muted-foreground size-3" aria-hidden />
                            : <Paperclip className="shrink-0 text-muted-foreground size-3" aria-hidden />}
                        <span className="max-w-40 truncate">{f.name}</span>
                      </span>
                    ))}
                  </div>
                )}
                <div className="rounded-2xl px-4 py-3 text-sm whitespace-pre-wrap bg-primary text-primary-foreground rounded-br-none">
                  {msg.text}
                </div>
                <div className="flex items-center justify-end gap-2 mt-1">
                  <Tip label={t('chat.edit_message')}>
                    <button
                      onClick={() => editMessage(msg.text)}
                      aria-label={t('chat.edit_message')}
                      className="opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground"
                    >
                      <Pencil className="size-3" />
                    </button>
                  </Tip>
                  <p className="text-2xs text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">
                    {formatTime(msg.timestamp, { timeZone: tz, intl: { hour: '2-digit', minute: '2-digit' } })}
                  </p>
                </div>
              </div>
            ) : (
              /* Assistant messages render FLAT on the chat background —
                 no bubble/card — per the assistant-UI convention (only
                 the USER is a speech bubble; the answer reads as the
                 page).  Full width: tables/artifacts need it, especially
                 in the 420px panel. */
              <div className="w-full group">
                {/* Process log — the ordered timeline of what produced
                    this answer (thinking → tool call → result → …),
                    collapsed by default.  Falls back to the flat
                    reasoning blob for messages saved before timelines
                    existed.  Deliberately NOT labelled with a tier name
                    ("Reasoning" / "Thinking") or a tier icon — those
                    belong to the picker and the footer label. */}
                {(msg.process || msg.reasoning) && (() => {
                  // Logical steps only: one row per contiguous thinking
                  // block / tool call (see the note above TimelineRow) —
                  // "4 steps" means think → tool → think → done, not 200
                  // paragraphs of one chain-of-thought.  Legacy messages
                  // without a process array render their flat reasoning
                  // as a single thinking step.
                  const exploded: AIProcessStep[] =
                    msg.process ?? [{ type: 'thinking', text: msg.reasoning || '' }];
                  return (
                    <div className="mb-1">
                      <button
                        onClick={() => setReasoningExpanded((prev) => {
                          const next = new Set(prev);
                          if (next.has(i)) next.delete(i); else next.add(i);
                          return next;
                        })}
                        className="flex items-center gap-1.5 text-2xs font-medium text-muted-foreground hover:text-foreground transition-colors py-1 -my-1 min-h-tap"
                      >
                        <Lightbulb className="size-3" />
                        <span>
                          {exploded.length > 1
                            ? `${exploded.length} ${t('chat.steps')}`
                            : t('chat.thought_process')}
                        </span>
                        <ChevronDown className={`transition-transform ${reasoningExpanded.has(i) ? 'rotate-180' : ''} size-3`} />
                      </button>
                      {reasoningExpanded.has(i) && (
                        <div className="mt-1.5 max-h-64 overflow-y-auto pr-1">
                          {/* One-time expectation-setter: thought logs are
                              browser-local by policy — say so before a user
                              meets it as a surprise on another device. */}
                          {!thoughtNoteDismissed && (
                            <div className="mb-2 flex items-start justify-between gap-2 rounded-md bg-muted px-2 py-1.5 text-2xs text-muted-foreground">
                              <span>{t('chat.thoughts_local_note')}</span>
                              <button
                                onClick={dismissThoughtNote}
                                className="shrink-0 font-medium hover:text-foreground transition-colors py-0.5 -my-0.5 min-h-tap"
                              >
                                {t('chat.got_it')}
                              </button>
                            </div>
                          )}
                          {exploded.map((step, si) => (
                            step.type === 'thinking' ? (
                              <TimelineRow key={si} marker={THINKING_DOT} last={false}>
                                {/* Label = the tier that produced THIS answer
                                    (frozen on the message), so the step reads
                                    "Reasoning ⌄" / "Thinking ⌄". */}
                                <ThinkingStep
                                  text={step.text || ''}
                                  label={msg.modelTier || t('chat.thinking_live')}
                                />
                              </TimelineRow>
                            ) : (
                              <TimelineRow
                                key={si}
                                marker={<Check className="text-primary/70 size-3" />}
                                last={false}
                              >
                                <div className="flex items-center gap-2 text-2xs font-medium text-muted-foreground">
                                  <span>{step.label || step.name}</span>
                                  {stepDuration(step.elapsed_ms) && (
                                    <span className="text-2xs text-muted-foreground/50 tabular-nums">
                                      {stepDuration(step.elapsed_ms)}
                                    </span>
                                  )}
                                </div>
                                {step.result && (
                                  <div className="mt-0.5 text-2xs text-muted-foreground/60 font-mono break-all line-clamp-2">
                                    {step.result}
                                  </div>
                                )}
                                <ToolStepLink toolName={step.name} />
                              </TimelineRow>
                            )
                          ))}
                          <TimelineRow
                            marker={<Check className="text-primary/70 size-3" />}
                            last
                          >
                            <div className="text-2xs text-muted-foreground">
                              {t('chat.done_step')}
                            </div>
                          </TimelineRow>
                        </div>
                      )}
                    </div>
                  );
                })()}
                <div
                  className="text-sm text-foreground ai-response"
                  dangerouslySetInnerHTML={{ __html: formatAIResponse(msg.text) }}
                />
                {/* Native artifacts (tables/charts) the model produced —
                    rendered through the registry (unknown types degrade
                    to a safe fallback). */}
                {msg.artifacts?.map((a, ai) => (
                  <div key={ai}>{renderArtifact(a)}</div>
                ))}
                {msg.toolResults && <ReferencedVehicles toolResults={msg.toolResults} />}
                <div className="flex items-center gap-2 mt-1">
                  {/* The tier that produced THIS answer, frozen at receipt,
                      shown with the SAME icon the tier picker uses so the
                      two read as one concept.  Absent on history-loaded
                      bubbles (no tier stored) — and switching the picker
                      never relabels past answers. */}
                  {msg.modelTier && (() => {
                    const TierIcon = (TIER_ICONS as Record<string, LucideIcon>)[msg.modelTier.toLowerCase()];
                    return (
                      <span className="inline-flex items-center gap-1 text-2xs text-muted-foreground/60">
                        {TierIcon && <TierIcon className="size-3" aria-hidden />}
                        {msg.modelTier}
                      </span>
                    );
                  })()}
                  {/* Data-freshness stamp — hover-reveal like the action
                      icons, so it's available but not noisy by default. */}
                  <span className="text-2xs text-muted-foreground/50 opacity-0 group-hover:opacity-100 transition-opacity">
                    {formatTime(msg.timestamp, { timeZone: tz, intl: { hour: '2-digit', minute: '2-digit' } })}
                  </span>
                  {/* Feedback row — only on the latest AI bubble.
                      Thumbs up = explicit positive (flips
                      had_thumbs_up).  Thumbs down = explicit
                      negative without retry (flips had_reask).
                      Regenerate = explicit negative WITH retry
                      (flips had_reask + re-fires the same prompt
                      via the existing send() dispatcher).  Once
                      the user votes, the icon stays filled so
                      they have visual confirmation. */}
                  {i === lastAiIdx && (
                    <>
                      <Tip label={t('chat.feedback_good')}>
                        <button
                          onClick={() => voteOnMessage(i, 'up')}
                          disabled={loading}
                          aria-label={t('chat.feedback_good')}
                          className={`opacity-0 group-hover:opacity-100 transition-opacity ml-auto disabled:cursor-not-allowed ${
                            feedbackByIdx[i] === 'up'
                              ? 'opacity-100 text-primary'
                              : 'text-muted-foreground hover:text-foreground'
                          }`}
                          aria-pressed={feedbackByIdx[i] === 'up'}
                        >
                          <ThumbsUp
                            className={cn(feedbackByIdx[i] === 'up' ? 'fill-current' : '', 'size-3')}
                          />
                        </button>
                      </Tip>
                      <Tip label={t('chat.feedback_bad')}>
                        <button
                          onClick={() => voteOnMessage(i, 'down')}
                          disabled={loading}
                          aria-label={t('chat.feedback_bad')}
                          className={`opacity-0 group-hover:opacity-100 transition-opacity disabled:cursor-not-allowed ${
                            feedbackByIdx[i] === 'down'
                              ? 'opacity-100 text-warn'
                              : 'text-muted-foreground hover:text-foreground'
                          }`}
                          aria-pressed={feedbackByIdx[i] === 'down'}
                        >
                          <ThumbsDown
                            className={cn(feedbackByIdx[i] === 'down' ? 'fill-current' : '', 'size-3')}
                          />
                        </button>
                      </Tip>
                      <Tip label={t('chat.regenerate')}>
                        <button
                          onClick={() => regenerateMessage(i)}
                          disabled={regeneratingIdx !== null || loading}
                          aria-label={t('chat.regenerate')}
                          className="opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground disabled:opacity-30 disabled:cursor-not-allowed"
                        >
                          <RefreshCw
                            className={cn(regeneratingIdx === i ? 'animate-spin' : '', 'size-3')}
                          />
                        </button>
                      </Tip>
                    </>
                  )}
                  <Tip label={t('chat.copy_response')}>
                    <button
                      onClick={() => copyMessage(msg.text, i)}
                      aria-label={t('chat.copy_response')}
                      className={`opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground ${i === lastAiIdx ? '' : 'ml-auto'}`}
                    >
                      {copiedIdx === i
                        ? <Check className="text-ok size-3" />
                        : <Copy className="size-3" />}
                    </button>
                  </Tip>
                </div>
                {dislikeFormFor === i && (
                  <DislikeReasonForm
                    onSkip={() => setDislikeFormFor(null)}
                    onSubmitted={() => setDislikeFormFor(null)}
                  />
                )}
              </div>
            )}
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            {/* Live progress — renders the REAL process as it happens,
                in true event order (thinking → tool → thinking → …), using
                the SAME visual language as the finished "N steps" timeline.
                FLAT like the finished answer (assistant side never gets a
                bubble); canned phrases appear only in the first seconds
                before any real event has arrived. */}
            <div className="w-full text-sm">
              <div className="flex items-center justify-between gap-4 mb-2">
                <span className="inline-flex items-center gap-1.5 text-2xs font-medium text-muted-foreground">
                  <Loader2 className="animate-spin text-primary size-3" aria-hidden />
                  <span>{liveStatusLabel}…</span>
                </span>
                {elapsedSec > 0 && (
                  <span className="text-2xs text-muted-foreground/60 tabular-nums">
                    {elapsedSec}s
                  </span>
                )}
              </div>

              {streamingText ? (
                /* Answer streaming in token-by-token (streaming models). */
                <div
                  className="ai-response"
                  dangerouslySetInnerHTML={{ __html: formatAIResponse(streamingText) }}
                />
              ) : (() => {
                // Same dot-and-line timeline as the finished view, one
                // paragraph-thought per dot, building in real time.
                const exploded = liveSteps;
                const lastIsTool = exploded.length > 0
                  && exploded[exploded.length - 1].type === 'tool';
                return (
                  <div ref={liveScrollRef} className="max-h-72 overflow-y-auto pr-1">
                    {exploded.map((step, si) => {
                      const isActiveTool = step.type === 'tool'
                        && si === exploded.length - 1;
                      if (step.type === 'thinking') {
                        return (
                          <TimelineRow key={si} marker={THINKING_DOT} last={false}>
                            <ThinkingStep
                              text={step.text || ''}
                              label={
                                (currentTier !== 'auto'
                                  && tiers.find((x) => x.name === currentTier)?.label)
                                || t('chat.thinking_live')
                              }
                              live={si === exploded.length - 1}
                            />
                          </TimelineRow>
                        );
                      }
                      return isActiveTool ? (
                        // Active tool = tinted running card with a live per-step
                        // timer ticking from its client startedAt (the elapsedSec
                        // interval re-renders us each second).
                        <TimelineRow key={si} marker={ACTIVE_BEACON} last>
                          <div className="flex items-center justify-between gap-2 rounded-md bg-primary/5 px-2 py-1">
                            <span className="text-xs font-medium text-foreground">
                              {step.label}…
                            </span>
                            {step.startedAt != null && (
                              <span className="shrink-0 text-2xs font-medium text-primary/70 tabular-nums">
                                {t('chat.running')} · {stepDuration(Date.now() - step.startedAt) ?? '0s'}
                              </span>
                            )}
                          </div>
                        </TimelineRow>
                      ) : (
                        <TimelineRow
                          key={si}
                          marker={<Check className="text-primary/70 size-3" />}
                          last={false}
                        >
                          <div className="flex items-center gap-2 text-2xs font-medium text-muted-foreground">
                            <span>{step.label}</span>
                            {stepDuration(step.elapsed_ms) && (
                              <span className="text-2xs text-muted-foreground/50 tabular-nums">
                                {stepDuration(step.elapsed_ms)}
                              </span>
                            )}
                          </div>
                          <ToolStepLink toolName={step.name} />
                        </TimelineRow>
                      );
                    })}
                    {/* Trailing activity row: canned phrase only before the
                        first real event; honest "Thinking…" while the last
                        step is a growing thought. */}
                    {!lastIsTool && (
                      <TimelineRow marker={ACTIVE_BEACON} last>
                        <div className="text-xs font-medium text-foreground animate-pulse">
                          {`${liveTierLabel}…`}
                        </div>
                      </TimelineRow>
                    )}
                  </div>
                );
              })()}
            </div>
          </div>
        )}

        {error && (
          <div className="flex flex-col items-center gap-2">
            <p className="text-destructive text-sm bg-destructive/10 px-3 py-2 rounded-lg">{error}</p>
            {lastFailed && (
              <button
                onClick={() => { setError(''); send(lastFailed!); }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-muted hover:bg-muted/80 text-muted-foreground border border-border transition-colors"
              >
                <RotateCcw className="size-3" />
                Retry
              </button>
            )}
          </div>
        )}

        <div ref={bottomRef} />
      </ScrollRegion>

      {/* Slash-command menu — appears when the input starts with "/" */}
      {slashMatches.length > 0 && !loading && (
        <div className="flex flex-col gap-0.5 mt-2 rounded-lg border border-border bg-card p-1 shadow-sm flex-shrink-0">
          {slashMatches.map((c) => (
            <button
              key={c.name}
              onClick={() => { setInput(''); c.run(); }}
              className="flex items-center gap-2 px-3 py-1.5 text-sm rounded-md text-left text-foreground/80 hover:bg-muted transition-colors"
            >
              <Sparkles className="text-primary shrink-0 size-3.5" aria-hidden />
              <span className="font-medium">/{c.name}</span>
              <span className="text-2xs text-muted-foreground">{c.label}</span>
            </button>
          ))}
        </div>
      )}

      {/* Composer — STACKED (modern assistant layout): the field spans the
          full width on top; a toolbar row sits beneath it (slash-commands on
          the left, send/stop on the right).  Stays editable while a reply
          streams — Enter is still blocked by submit()'s loading guard — so the
          user can keep drafting their next question. */}
      <div
        className="mt-3 flex-shrink-0"
        // Drag a spreadsheet anywhere onto the composer to attach it.
        onDragEnter={onDragEnter}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        {/* Status strip — a tinted WRAPPER around the composer (one connected
            card, like the reference design — never a floating bar with a gap).
            The header row sits inside the wrapper, above the input card.
            Three states, all via theme tokens (multi-theme safe):
              • Running   — current step + ticking elapsed (primary tint)
              • Done · Xs — brief success stamp after a run (ok tone)
              • Waiting for your reply — persistent idle state that CARRIES
                the follow-up suggestions, collapsed behind a count badge
                (compact-first: 6 chips ≈ 3 rows in the panel otherwise).
            With no suggestions the wrapper leaves after the Done flash —
            a bare "waiting" banner with nothing to offer is noise. */}
        {(() => {
          const stripState = loading
            ? 'running'
            : suggestions.length > 0
              ? 'idle'
              : doneFlash != null ? 'done' : null;
          const wrapClass = stripState === 'running'
            ? 'rounded-xl border border-primary/20 bg-primary/5 p-1'
            : stripState === 'idle'
              ? 'rounded-xl border border-border bg-muted/40 p-1'
              : stripState === 'done'
                ? `rounded-xl border p-1 ${toneClasses('ok')}`
                : '';
          return (
            <div className={wrapClass}>
              {stripState === 'running' && (
                <div
                  role="status"
                  className="flex items-center gap-1.5 px-2 py-1 text-2xs font-medium text-primary"
                >
                  <Sparkles className="animate-pulse shrink-0 size-3" aria-hidden />
                  <span className="truncate">
                    {t('chat.running')}
                    {(() => {
                      const lastLive = liveSteps[liveSteps.length - 1];
                      const stepLabel = lastLive?.type === 'tool'
                        ? lastLive.label
                        : liveSteps.length > 0 ? liveTierLabel : null;
                      return stepLabel ? ` · ${stepLabel}` : '';
                    })()}
                  </span>
                  {elapsedSec > 0 && (
                    <span className="ml-auto shrink-0 tabular-nums opacity-70">{elapsedSec}s</span>
                  )}
                </div>
              )}
              {stripState === 'idle' && (
                <>
                  <div className="flex items-center gap-1.5 px-2 py-1 text-2xs font-medium text-muted-foreground">
                    {doneFlash != null ? (
                      // Fresh answer: success stamp first, swaps to the idle
                      // label when the flash timer clears doneFlash.
                      <span className={`inline-flex items-center gap-1.5 ${toneText('ok')}`} role="status">
                        <Check className="shrink-0 size-3" aria-hidden />
                        {t('chat.done_step')}
                        {stepDuration(doneFlash) && (
                          <span className="tabular-nums opacity-70">{stepDuration(doneFlash)}</span>
                        )}
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5">
                        <Bot className="shrink-0 size-3" aria-hidden />
                        {t('chat.awaiting_reply')}
                      </span>
                    )}
                    <Tip label={t('chat.suggestions')}>
                      <button
                        onClick={() => setSuggestionsOpen((v) => !v)}
                        aria-expanded={suggestionsOpen}
                        aria-label={t('chat.suggestions')}
                        className="ml-auto inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 hover:bg-muted hover:text-foreground transition-colors"
                      >
                        <Lightbulb className="size-3" aria-hidden />
                        <span className="tabular-nums">{suggestions.length}</span>
                        <ChevronDown
                          className={`transition-transform ${suggestionsOpen ? 'rotate-180' : ''} size-3`}
                          aria-hidden
                        />
                      </button>
                    </Tip>
                  </div>
                  {suggestionsOpen && (
                    <div className="flex flex-wrap gap-1.5 px-2 pb-1.5">
                      {suggestions.map((s, i) => (
                        <button
                          key={i}
                          onClick={() => send(s)}
                          className="px-2.5 py-1 text-2xs rounded-full bg-card hover:bg-primary/10 hover:text-primary hover:border-primary/30 text-foreground/70 border border-border transition-colors min-h-tap"
                        >
                          {s}
                        </button>
                      ))}
                    </div>
                  )}
                </>
              )}
              {stripState === 'done' && (
                <div
                  role="status"
                  className="flex items-center gap-1.5 px-2 py-1 text-2xs font-medium"
                >
                  <Check className="shrink-0 size-3" aria-hidden />
                  <span>{t('chat.done_step')}</span>
                  {stepDuration(doneFlash ?? undefined) && (
                    <span className="ml-auto shrink-0 tabular-nums opacity-70">
                      {stepDuration(doneFlash ?? undefined)}
                    </span>
                  )}
                </div>
              )}
              <div className={`${stripState ? 'rounded-lg' : 'rounded-xl'} border bg-card px-3 pt-2.5 pb-2 transition-colors focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/20 ${dragOver ? 'border-primary ring-2 ring-primary/30' : 'border-border'}`}>
          {dragOver && (
            <div className="mb-1.5 flex items-center gap-1.5 text-2xs font-medium text-primary" role="status">
              <Paperclip className="size-3" aria-hidden /> {t('chat.attach_drop')}
            </div>
          )}
          {/* Attachment chips — the device-held file(s) riding with each
              send until removed.  name · size · ✕ per the import spine. */}
          {(attachments.length > 0 || preparing.length > 0 || attachError) && (
            <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
              {/* Being converted on-device (PDF text extraction / workbook
                  parsing) — the wait is real, so it gets a real chip. */}
              {preparing.map((name) => (
                <span
                  key={`preparing:${name}`}
                  role="status"
                  className="inline-flex items-center gap-1 rounded-md border border-border border-dashed bg-muted/40 px-2 py-0.5 text-xs text-muted-foreground"
                >
                  <Loader2 className="shrink-0 animate-spin size-3" aria-hidden />
                  <span className="max-w-40 truncate">{name}</span>
                  <span className="text-2xs">{t('chat.attach_preparing')}</span>
                </span>
              ))}
              {attachments.map((a) => (
                <span
                  key={a.name}
                  className="inline-flex items-center gap-1 rounded-md border border-border bg-muted/60 px-2 py-0.5 text-xs text-foreground"
                >
                  {a.kind === 'text'
                    ? <FileText className="shrink-0 text-muted-foreground size-3" aria-hidden />
                    : a.kind === 'image'
                      ? <ImageIcon className="shrink-0 text-muted-foreground size-3" aria-hidden />
                      : <Paperclip className="shrink-0 text-muted-foreground size-3" aria-hidden />}
                  <span className="max-w-40 truncate">{a.name}</span>
                  <span className="text-2xs text-muted-foreground tabular-nums">
                    {Math.max(1, Math.round(a.size / 1024))} KB
                  </span>
                  <button
                    onClick={() => {
                      const next = removePendingAttachment(attachmentsRef.current, a.name);
                      attachmentsRef.current = next;
                      setAttachments(next);
                    }}
                    aria-label={t('chat.attach_remove')}
                    className="ml-0.5 rounded text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <X className="size-3" aria-hidden />
                  </button>
                </span>
              ))}
              {attachError && (
                <span className="text-2xs text-destructive" role="alert">{attachError}</span>
              )}
            </div>
          )}
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = 'auto';
              e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
            }}
            onKeyDown={handleKeyDown}
            onPaste={(e) => {
              // Ctrl+V screenshots: clipboard files become attachments
              // through the same lane as picker/drag.
              const files = Array.from(e.clipboardData?.files ?? []);
              if (files.length > 0) {
                e.preventDefault();
                void attachFiles(files);
              }
            }}
            onFocus={() => setInputFocused(true)}
            onBlur={() => setInputFocused(false)}
            placeholder={attachments.length > 0
              // With a file attached, hold the hint on the next step of the
              // import flow instead of the generic ask/commands rotation.
              ? t('chat.placeholder_attached')
              : phIdx === 0
                ? t('chat.placeholder_ask', { subject: chatSubject })
                : t('chat.placeholder_commands')}
            rows={1}
            style={{ maxHeight: scaledPx(120, 'text') }}
            className="w-full bg-transparent text-foreground text-sm resize-none focus:outline-none placeholder:text-muted-foreground/50"
          />
          {/* Toolbar — slash-commands (icon-only) on the left; the model
              picker + send/stop grouped on the right, next to the input. */}
          <div className="mt-1.5 flex items-center justify-between gap-2">
            {/* "+" quick actions — attach a CSV for the AI import flow,
                plus the slash commands (also reachable by typing "/",
                per the placeholder hint). */}
            <div className="relative" ref={plusRef}>
              <Tip label={t('chat.commands')}>
                <button
                  type="button"
                  onClick={() => setPlusOpen((v) => !v)}
                  aria-haspopup="menu"
                  aria-expanded={plusOpen}
                  aria-label={t('chat.commands')}
                  className="flex size-8 items-center justify-center rounded-md text-muted-foreground/60 hover:text-foreground hover:bg-muted transition-colors"
                >
                  <Plus
                    aria-hidden
                    className={`transition-transform ${plusOpen ? 'rotate-45' : ''} size-4`}
                  />
                </button>
              </Tip>
              <input
                ref={fileInputRef}
                type="file"
                accept={DOCUMENT_ACCEPT}
                multiple
                className="hidden"
                onChange={(e) => {
                  if (e.target.files?.length) void attachFiles(e.target.files);
                  e.target.value = '';   // same file re-attachable after ✕
                }}
              />
              {plusOpen && (
                <div
                  role="menu"
                  className="absolute left-0 bottom-full mb-1 z-50 w-64 rounded-lg border border-border bg-card p-1 shadow-xl"
                >
                  <Tip label={t('chat.attach_formats')}>
                    <button
                      role="menuitem"
                      onClick={() => { setPlusOpen(false); fileInputRef.current?.click(); }}
                      className="flex w-full items-center gap-2 px-2.5 py-1.5 text-sm rounded-md text-left text-foreground/80 hover:bg-muted transition-colors"
                    >
                      <Paperclip className="text-primary shrink-0 size-3.5" aria-hidden />
                      <span className="font-medium">{t('chat.attach_file')}</span>
                    </button>
                  </Tip>
                  {/* Files attached in OTHER chats, still on this device —
                      one click re-attaches without hunting the disk. */}
                  {plusOpen && (() => {
                    const recent = listRecentAttachments(
                      conversationId,
                      [...attachments.map((a) => a.name), ...convoFiles.map((a) => a.name)],
                    );
                    if (recent.length === 0) return null;
                    return (
                      <>
                        <div className="my-1 border-t border-border" role="separator" />
                        <div className="px-2.5 py-1 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                          {t('chat.attach_recent')}
                        </div>
                        {recent.map((f) => (
                          <button
                            key={f.name}
                            role="menuitem"
                            onClick={() => {
                              const res = addPendingAttachment(attachmentsRef.current, f.name, f.content, f.kind);
                              if ('error' in res) {
                                flashAttachError(t(res.error === 'too_large' ? 'chat.attach_too_large' : 'chat.attach_limit'));
                              } else {
                                attachmentsRef.current = res.list;
                                setAttachments(res.list);
                              }
                            }}
                            className="flex w-full items-center gap-2 px-2.5 py-1 text-xs rounded-md text-left text-foreground/80 hover:bg-muted transition-colors min-h-tap"
                          >
                            {f.kind === 'image'
                              ? <ImageIcon className="shrink-0 text-muted-foreground size-3" aria-hidden />
                              : f.kind === 'text'
                                ? <FileText className="shrink-0 text-muted-foreground size-3" aria-hidden />
                                : <Paperclip className="shrink-0 text-muted-foreground size-3" aria-hidden />}
                            <span className="max-w-40 truncate">{f.name}</span>
                            <span className="ml-auto text-2xs text-muted-foreground tabular-nums">
                              {Math.max(1, Math.round(f.size / 1024))} KB
                            </span>
                          </button>
                        ))}
                      </>
                    );
                  })()}
                  {/* Files already sent in this thread — still riding each
                      turn so follow-ups work; removable here. */}
                  {conversationId != null && convoFiles.length > 0 && (
                    <>
                      <div className="my-1 border-t border-border" role="separator" />
                      <div className="px-2.5 py-1 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                        {t('chat.attach_in_chat')}
                      </div>
                      {convoFiles.map((f) => (
                        <div
                          key={f.name}
                          className="flex w-full items-center gap-2 px-2.5 py-1 text-xs text-foreground/80"
                        >
                          {f.kind === 'image'
                            ? <ImageIcon className="shrink-0 text-muted-foreground size-3" aria-hidden />
                            : f.kind === 'text'
                              ? <FileText className="shrink-0 text-muted-foreground size-3" aria-hidden />
                              : <Paperclip className="shrink-0 text-muted-foreground size-3" aria-hidden />}
                          <span className="max-w-40 truncate">{f.name}</span>
                          <button
                            onClick={() => setConvoFiles(removeConversationAttachment(conversationId, f.name))}
                            aria-label={t('chat.attach_remove')}
                            className="ml-auto rounded text-muted-foreground hover:text-foreground transition-colors"
                          >
                            <X className="size-3" aria-hidden />
                          </button>
                        </div>
                      ))}
                    </>
                  )}
                  <div className="my-1 border-t border-border" role="separator" />
                  {SLASH_COMMANDS.map((c) => (
                    <button
                      key={c.name}
                      role="menuitem"
                      onClick={() => { setPlusOpen(false); setInput(''); c.run(); }}
                      className="flex w-full items-center gap-2 px-2.5 py-1.5 text-sm rounded-md text-left text-foreground/80 hover:bg-muted transition-colors"
                    >
                      <Sparkles className="text-primary shrink-0 size-3.5" aria-hidden />
                      <span className="font-medium">{c.label}</span>
                      <span className="ml-auto text-2xs text-muted-foreground font-mono">/{c.name}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div className="flex items-center gap-1.5">
              {/* Model (tier) picker — moved here from the header; opens
                  UPWARD since it sits at the bottom of the panel. */}
              {tiers.length > 0 && (() => {
                const active = tiers.find((t) => t.name === currentTier) ?? tiers[0];
                const ActiveIcon = TIER_ICONS[active.name];
                return (
                  <div className="relative" ref={modelRef}>
                    <Tip label={active.description}>
                      <button
                        onClick={() => setTierOpen(!tierOpen)}
                        aria-haspopup="listbox"
                        aria-expanded={tierOpen}
                        className="flex items-center gap-1 rounded-md px-2 py-1.5 text-2xs font-medium text-muted-foreground/80 hover:text-foreground hover:bg-muted transition-colors cursor-pointer"
                      >
                        <ActiveIcon className="size-3.5" aria-hidden />
                        <span>{active.label}</span>
                        <ChevronDown
                          className={`transition-transform shrink-0 ${tierOpen ? 'rotate-180' : ''} size-3`}
                        />
                      </button>
                    </Tip>
                    {tierOpen && (
                      <Card padding="none" className="absolute right-0 bottom-full mb-1 z-50 w-64 shadow-xl" role="listbox">
                        {tiers.map((tier) => {
                          const Icon = TIER_ICONS[tier.name];
                          const isActive = tier.name === currentTier;
                          return (
                            <button
                              key={tier.name}
                              onClick={() => switchTier(tier.name)}
                              disabled={tierSwitching || isActive}
                              role="option"
                              aria-selected={isActive}
                              className={`w-full text-left px-3 py-2 text-sm transition-colors ${
                                isActive
                                  ? 'bg-primary/15 text-primary'
                                  : 'text-foreground/80 hover:bg-muted'
                              } disabled:cursor-default`}
                            >
                              <div className="flex items-center gap-2">
                                <Icon aria-hidden className="shrink-0 size-4" />
                                <span className="font-medium">{tier.label}</span>
                                {tier.model_count > 0 && (
                                  <span className="ml-auto text-2xs text-muted-foreground">
                                    {tier.model_count} models
                                  </span>
                                )}
                              </div>
                              {tier.description && (
                                <span className="text-2xs text-muted-foreground block mt-0.5 ml-6">
                                  {tier.description}
                                </span>
                              )}
                            </button>
                          );
                        })}
                      </Card>
                    )}
                  </div>
                );
              })()}

              {loading ? (
                <Tip label={t('chat.stop_generating')}>
                  <button
                    onClick={stopGeneration}
                    aria-label={t('chat.stop_generating')}
                    className="flex size-9 shrink-0 items-center justify-center rounded-full border border-border bg-muted text-foreground hover:bg-muted/80 transition-colors"
                  >
                    <Square className="fill-current size-3.5" />
                  </button>
                </Tip>
              ) : (
                <Tip label={t('chat.send')}>
                  <button
                    onClick={() => submit(input)}
                    disabled={!input.trim() || preparing.length > 0}
                    aria-label={t('chat.send')}
                    className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <ArrowUp className="size-4" />
                  </button>
                </Tip>
              )}
                </div>
              </div>
              </div>
            </div>
          );
        })()}
      </div>
    </div>
  );
}
