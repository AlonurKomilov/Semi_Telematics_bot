import { useState, useEffect, useRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation } from 'react-router-dom';
import { Bot, Send, Square, Trash2, Copy, Check, RefreshCw, Sparkles, Pencil, Download, RotateCcw, ChevronDown, Zap, Brain, Microscope, Lightbulb, Loader2, ThumbsUp, ThumbsDown, Eye, History, SquarePen, type LucideIcon } from 'lucide-react';
import { apiJSON, apiJSONAI, apiStreamChat } from '../../api/client';
import { useShellConfig } from '../../hooks/useShellConfig';
import type { AIChatMessage, AIConversation, AIConversationsResponse, AIConversationMessagesResponse, AIProcessStep, AISummaryResponse, AIUsage, AITierChoice, AITierOption, AITierResponse } from '../../types';
import { formatAIResponse } from '../../utils/formatAI';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate, formatTime } from '../../utils/datetime';
import { DislikeReasonForm } from './sections/DislikeReasonForm';
import { ReferencedVehicles } from './sections/ReferencedVehicles';
import { thoughtKey, saveThought, getThought, deleteThoughtsForConversation } from './thoughtStore';

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

/** Split thinking steps into paragraph-level steps so the timeline reads
 *  as discrete thoughts — one dot per thought — instead of one flat text
 *  wall.  Tool steps pass through unchanged.  Purely a display transform;
 *  the stored process keeps the raw chunks. */
function explodeProcess(steps: AIProcessStep[]): AIProcessStep[] {
  const out: AIProcessStep[] = [];
  for (const s of steps) {
    if (s.type === 'thinking') {
      for (const para of (s.text || '').split(/\n\s*\n/)) {
        const text = para.trim();
        if (text) out.push({ type: 'thinking', text });
      }
    } else {
      out.push(s);
    }
  }
  return out;
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

const THINKING_DOT = (
  <span className="size-1.5 rounded-full bg-muted-foreground/50" aria-hidden />
);
const ACTIVE_BEACON = (
  <span className="relative inline-flex size-2" aria-hidden>
    <span className="absolute inline-flex h-full w-full rounded-full bg-primary/60 animate-ping" />
    <span className="relative inline-flex size-2 rounded-full bg-primary" />
  </span>
);

export default function Chat() {
  const { t } = useTranslation();
  const tz = useTimezone();
  const location = useLocation();
  const { activeView, isDriverView, briefingLabel, chatSubject } = useShellConfig();

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
  const [thoughtNoteDismissed, setThoughtNoteDismissed] = useState(() => {
    try { return localStorage.getItem('4truck:ai-thoughts-note:v1') === '1'; } catch { return true; }
  });
  /** Two-step delete confirm inside the panel — id armed for deletion. */
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null);
  /** Bubble indexes whose per-message Reasoning section is expanded. */
  const [reasoningExpanded, setReasoningExpanded] = useState<Set<number>>(new Set());
  const deleteConfirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const historyRef = useRef<HTMLDivElement>(null);

  // ── Streaming state ──────────────────────────────────────────
  /** Tool labels shown while streaming (e.g. "Checking fault codes") */
  const [toolActivity, setToolActivity] = useState<string[]>([]);
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
  const [thinkIdx, setThinkIdx] = useState(0);
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
    apiJSON<AIConversationsResponse>('/ai/conversations')
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
            const local = msg.role === 'model'
              ? getThought(thoughtKey(convs[0].id, msg.text)) : null;
            return {
              ...msg,
              // Use the backend timestamp when present so loaded scrollback
              // shows the real send time, not "12:34 PM" stamped at page load.
              timestamp: msg.ts ? new Date(msg.ts) : new Date(),
              modelTier: msg.model_tier || undefined,
              reasoning: local?.reasoning,
              process: local?.process,
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
  }, []);

  // Handle initial message passed via router state (e.g. "Diagnose faults on Truck 231")
  useEffect(() => {
    const state = location.state as { initialMessage?: string } | null;
    if (state?.initialMessage) {
      send(state.initialMessage);
      window.history.replaceState({}, document.title);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-scroll on new messages AND as live steps / streamed text grow,
  // so the newest step is always in view while the model works.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading, liveSteps, streamingText]);

  // Cycle through thinking phrases before first tool fires
  const THINK_PHRASES = [
    'Reading your question…',
    'Figuring out what data I need…',
    'Preparing the right query…',
    'Connecting to telematics…',
  ];
  useEffect(() => {
    if (!loading || toolActivity.length > 0) return;
    setThinkIdx(0);
    const t = setInterval(() => setThinkIdx((i) => (i + 1) % THINK_PHRASES.length), 1800);
    return () => clearInterval(t);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, toolActivity.length]);

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
    const userMsg: LocalMessage = { role: 'user', text: text.trim(), timestamp: new Date() };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setSuggestions([]);
    setLoading(true);
    setError('');
    setToolActivity([]);
    setLiveSteps([]);
    setStreamingText('');
    setLastFailed(null);

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
      // Mirror of the streamed `reasoning` state — the state itself is
      // stale inside this closure, so accumulate locally too and attach
      // it to the finished message (Gemini streams thinking live; the
      // reasoning tier delivers it whole on `done`).
      let liveReasoning = '';

      await apiStreamChat(
        text.trim(),
        (event) => {
          if (event.type === 'tool') {
            setToolActivity((prev) => [...prev, event.label]);
            setLiveSteps((prev) => [...prev, { type: 'tool', name: event.name, label: event.label }]);
          } else if (event.type === 'thinking') {
            liveReasoning += event.text;
            // Coalesce consecutive thinking chunks into one growing step —
            // Gemini streams token-sized fragments.
            setLiveSteps((prev) => {
              const last = prev[prev.length - 1];
              if (last?.type === 'thinking') {
                return [...prev.slice(0, -1), { ...last, text: (last.text || '') + event.text }];
              }
              return [...prev, { type: 'thinking', text: event.text }];
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
            if (event.scope) setScope(event.scope);
          } else if (event.type === 'error') {
            throw new Error(event.message);
          }
        },
        abort.signal,
        { conversationId, newConversation: isNewChat },
      );

      if (!finalReply) throw new Error('No response received');
      const aiMsg: LocalMessage = { role: 'model', text: finalReply, timestamp: new Date(), usage: finalUsage, toolResults: finalToolResults, modelTier: finalModelTier || undefined, reasoning: (finalReasoning || liveReasoning).trim() || undefined, process: finalProcess.length > 0 ? finalProcess : undefined };
      // Thought logs + suggestion chips are browser-local by policy (the
      // server stores only text + tier) — stash them so refresh /
      // thread-reopen restores them.
      if (aiMsg.reasoning || aiMsg.process || finalSuggestions.length > 0) {
        saveThought(thoughtKey(finalConversationId, finalReply), {
          reasoning: aiMsg.reasoning, process: aiMsg.process,
          suggestions: finalSuggestions,
        });
      }
      setMessages((prev) => [...prev, aiMsg]);
      setSuggestions(finalSuggestions);
      // Adopt the thread the backend answered in (created lazily for a
      // new chat / a stale id) and refresh the History panel's list so
      // the thread title + activity time stay current.
      if (finalConversationId != null) setConversationId(finalConversationId);
      setIsNewChat(false);
      void loadConversations();
    } catch (e) {
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
      setToolActivity([]);
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
    setToolActivity([]);
    setLoading(true);
    try {
      const data = await apiJSONAI<AISummaryResponse>('/ai/summary', { method: 'POST' });
      const aiMsg: LocalMessage = { role: 'model', text: data.summary, timestamp: new Date() };
      setMessages((prev) => [...prev, aiMsg]);
      setSuggestions(data.suggestions || []);
    } catch (e) {
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
    setToolActivity([]);
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
      const d = await apiJSON<AIConversationsResponse>('/ai/conversations');
      setConversations(d.conversations || []);
    } catch { /* panel keeps its last list; next open retries */ }
  }

  function dismissThoughtNote() {
    setThoughtNoteDismissed(true);
    try { localStorage.setItem('4truck:ai-thoughts-note:v1', '1'); } catch { /* ignore */ }
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
    setToolActivity([]);
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
        const local = msg.role === 'model'
          ? getThought(thoughtKey(conv.id, msg.text)) : null;
        return {
          ...msg,
          timestamp: msg.ts ? new Date(msg.ts) : new Date(),
          modelTier: msg.model_tier || undefined,
          reasoning: local?.reasoning,
          process: local?.process,
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

  return (
    <div className="flex flex-col h-[calc(100vh-6rem)]">
      {/* ── Header ────────────────────────────────────────────── */}
      <div className="flex items-center justify-between mb-4 flex-shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <h1 className="text-xl font-semibold flex items-center gap-2">
            <Bot size={20} className="text-primary" />
            AI Assistant
          </h1>
          {/* Trust badge: only for restricted (company/vehicle-scoped) users —
              surfaces the Vehicle Access scope so they understand answers cover
              a subset, not the whole account. */}
          {scope?.restricted && (
            <span
              className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-2xs font-medium bg-muted text-muted-foreground border border-border whitespace-nowrap"
              title="Your access is limited to these vehicles — answers cover only them."
            >
              <Eye size={12} aria-hidden />
              Answering for your {scope.vehicle_count ?? 0} vehicle{scope.vehicle_count === 1 ? '' : 's'}
            </span>
          )}
        </div>

        {/* Right-side controls: tier picker + export + clear */}
        <div className="flex items-center gap-2">
          {/* Tier picker — single button collapsing to a dropdown that
              shows the three tiers (Fast / Thinking / Reasoning).
              Icons come from lucide-react (design.md §7), sized to
              the standard 14px step that pairs with text-sm body.
              The resolved model name is intentionally NOT shown here —
              it appears under each AI response bubble instead, where
              it's tied to the message that produced it. */}
          {tiers.length > 0 && (() => {
            const active = tiers.find((t) => t.name === currentTier) ?? tiers[0];
            const ActiveIcon = TIER_ICONS[active.name];
            return (
              <div className="relative" ref={modelRef}>
                <button
                  onClick={() => setTierOpen(!tierOpen)}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border transition-colors bg-muted border-border hover:border-ring text-foreground/80 cursor-pointer"
                  aria-haspopup="listbox"
                  aria-expanded={tierOpen}
                  title={active.description}
                >
                  <ActiveIcon size={14} aria-hidden />
                  <span>{active.label}</span>
                  <ChevronDown
                    size={12}
                    className={`transition-transform shrink-0 ${tierOpen ? 'rotate-180' : ''}`}
                  />
                </button>
                {tierOpen && (
                  <div
                    role="listbox"
                    className="absolute right-0 top-full mt-1 z-50 w-64 rounded-lg border border-border bg-card shadow-xl"
                  >
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
                            <Icon size={16} aria-hidden className="shrink-0" />
                            <span className="font-medium">{tier.label}</span>
                            {tier.model_count > 0 && (
                              <span className="ml-auto text-3xs text-muted-foreground">
                                {tier.model_count} models
                              </span>
                            )}
                          </div>
                          {tier.description && (
                            <span className="text-3xs text-muted-foreground block mt-0.5 ml-6">
                              {tier.description}
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })()}

          {/* Separator */}
          <div className="w-px h-5 bg-border" />

          {/* New chat — start a fresh thread (created lazily on first send) */}
          <button
            onClick={newChat}
            disabled={messages.length === 0 && conversationId === null && !loading}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border transition-colors disabled:opacity-50 disabled:cursor-default ${
              newChatConfirm
                ? 'bg-destructive/15 text-destructive border-destructive/30 hover:bg-destructive/25'
                : 'bg-muted hover:bg-muted/80 text-muted-foreground border-border'
            }`}
            title={newChatConfirm ? t('chat.confirm_discard') : t('chat.new_chat')}
          >
            <SquarePen size={14} />
            {newChatConfirm ? t('chat.confirm_discard') : t('chat.new_chat')}
          </button>

          {/* History — previous chats with per-chat export / delete,
              the standard AI-chat threads panel. */}
          <div className="relative" ref={historyRef}>
            <button
              onClick={() => { setHistoryOpen(!historyOpen); setDeleteConfirmId(null); if (!historyOpen) void loadConversations(); }}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg transition-colors bg-muted hover:bg-muted/80 text-muted-foreground border border-border"
              aria-haspopup="listbox"
              aria-expanded={historyOpen}
              title={t('chat.history')}
            >
              <History size={14} />
              {t('chat.history')}
            </button>
            {historyOpen && (
              <div className="absolute right-0 top-full mt-1 z-50 w-80 rounded-lg border border-border bg-card shadow-xl max-h-96 overflow-y-auto">
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
                        <div className="text-3xs text-muted-foreground">
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
                            className="px-2 py-1 rounded-md text-2xs font-medium bg-destructive/15 text-destructive hover:bg-destructive/25 transition-colors"
                          >
                            {t('chat.delete_yes')}
                          </button>
                          <button
                            onClick={() => setDeleteConfirmId(null)}
                            className="px-2 py-1 rounded-md text-2xs text-muted-foreground hover:text-foreground transition-colors"
                          >
                            {t('chat.cancel')}
                          </button>
                        </div>
                      ) : (
                        <>
                          <button
                            onClick={(e) => { e.stopPropagation(); void exportConversation(conv); }}
                            className="opacity-0 group-hover/conv:opacity-100 p-1 rounded text-muted-foreground hover:text-foreground transition-opacity"
                            title={t('chat.export_conversation')}
                          >
                            <Download size={14} />
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); armDelete(conv.id); }}
                            className="opacity-0 group-hover/conv:opacity-100 p-1 rounded text-muted-foreground hover:text-destructive transition-opacity"
                            title={t('chat.delete_chat')}
                          >
                            <Trash2 size={14} />
                          </button>
                        </>
                      )}
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto space-y-4 pr-1 min-h-0">
        {messages.length === 0 && !loading && (
          <div className="text-center text-muted-foreground mt-16">
            <Bot size={40} className="mx-auto mb-3 text-primary/40" />
            <p className="text-lg font-medium">{t('chat.title')}</p>
            <p className="text-sm mt-1">
              {isDriverView
                ? t('chat.ask_driver')
                : `Ask anything about ${chatSubject} — ${getChatTopics(activeView)}.`}
            </p>
            {/* Primary action: the operations briefing.  Lives here (and as the
                /briefing command) instead of a separate tab. */}
            <div className="mt-6 flex flex-col items-center gap-3">
              <button
                onClick={runBriefing}
                className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-primary/10 text-primary border border-primary/20 hover:bg-primary/15 transition-colors"
                title={`Generate your ${briefingLabel.toLowerCase()} — or type /briefing`}
              >
                <Sparkles size={16} aria-hidden />
                {briefingLabel}
              </button>
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
                <div className="rounded-2xl px-4 py-3 text-sm whitespace-pre-wrap bg-primary text-primary-foreground rounded-br-none">
                  {msg.text}
                </div>
                <div className="flex items-center justify-end gap-2 mt-1">
                  <button
                    onClick={() => editMessage(msg.text)}
                    className="opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground"
                    title={t('chat.edit_message')}
                  >
                    <Pencil size={12} />
                  </button>
                  <p className="text-3xs text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">
                    {formatTime(msg.timestamp, { timeZone: tz, intl: { hour: '2-digit', minute: '2-digit' } })}
                  </p>
                </div>
              </div>
            ) : (
              <div className="max-w-[82%] group">
                {/* Process log — the ordered timeline of what produced
                    this answer (thinking → tool call → result → …),
                    collapsed by default.  Falls back to the flat
                    reasoning blob for messages saved before timelines
                    existed.  Deliberately NOT labelled with a tier name
                    ("Reasoning" / "Thinking") or a tier icon — those
                    belong to the picker and the footer label. */}
                {(msg.process || msg.reasoning) && (() => {
                  // Paragraph-exploded steps: each discrete thought gets its
                  // own dot on the rule, tools keep their check markers —
                  // one visual language for "what happened", never a flat
                  // text wall.  Legacy messages without a process array
                  // explode their flat reasoning the same way.
                  const exploded = explodeProcess(
                    msg.process ?? [{ type: 'thinking', text: msg.reasoning || '' }],
                  );
                  return (
                    <div className="mb-1">
                      <button
                        onClick={() => setReasoningExpanded((prev) => {
                          const next = new Set(prev);
                          if (next.has(i)) next.delete(i); else next.add(i);
                          return next;
                        })}
                        className="flex items-center gap-1.5 text-2xs font-medium text-muted-foreground hover:text-foreground transition-colors"
                      >
                        <Lightbulb size={12} />
                        <span>
                          {exploded.length > 1
                            ? `${exploded.length} ${t('chat.steps')}`
                            : t('chat.thought_process')}
                        </span>
                        <ChevronDown size={12} className={`transition-transform ${reasoningExpanded.has(i) ? 'rotate-180' : ''}`} />
                      </button>
                      {reasoningExpanded.has(i) && (
                        <div className="mt-1.5 max-h-64 overflow-y-auto pr-1">
                          {/* One-time expectation-setter: thought logs are
                              browser-local by policy — say so before a user
                              meets it as a surprise on another device. */}
                          {!thoughtNoteDismissed && (
                            <div className="mb-2 flex items-start justify-between gap-2 rounded-md bg-muted px-2 py-1.5 text-3xs text-muted-foreground">
                              <span>{t('chat.thoughts_local_note')}</span>
                              <button
                                onClick={dismissThoughtNote}
                                className="shrink-0 font-medium hover:text-foreground transition-colors"
                              >
                                {t('chat.got_it')}
                              </button>
                            </div>
                          )}
                          {exploded.map((step, si) => (
                            step.type === 'thinking' ? (
                              <TimelineRow key={si} marker={THINKING_DOT} last={false}>
                                <div className="text-2xs text-muted-foreground/80 whitespace-pre-wrap leading-relaxed">
                                  {(step.text || '').replace(/\*\*/g, '')}
                                </div>
                              </TimelineRow>
                            ) : (
                              <TimelineRow
                                key={si}
                                marker={<Check size={12} className="text-primary/70" />}
                                last={false}
                              >
                                <div className="text-2xs font-medium text-muted-foreground">
                                  {step.label || step.name}
                                </div>
                                {step.result && (
                                  <div className="mt-0.5 text-3xs text-muted-foreground/60 font-mono break-all line-clamp-2">
                                    {step.result}
                                  </div>
                                )}
                              </TimelineRow>
                            )
                          ))}
                          <TimelineRow
                            marker={<Check size={12} className="text-primary/70" />}
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
                  className="rounded-2xl px-4 py-3 text-sm bg-card border border-border text-foreground rounded-bl-none ai-response shadow-sm"
                  dangerouslySetInnerHTML={{ __html: formatAIResponse(msg.text) }}
                />
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
                      <span className="inline-flex items-center gap-1 text-3xs text-muted-foreground/60">
                        {TierIcon && <TierIcon size={12} aria-hidden />}
                        {msg.modelTier}
                      </span>
                    );
                  })()}
                  {/* Data-freshness stamp — hover-reveal like the action
                      icons, so it's available but not noisy by default. */}
                  <span className="text-3xs text-muted-foreground/50 opacity-0 group-hover:opacity-100 transition-opacity">
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
                      <button
                        onClick={() => voteOnMessage(i, 'up')}
                        disabled={loading}
                        className={`opacity-0 group-hover:opacity-100 transition-opacity ml-auto disabled:cursor-not-allowed ${
                          feedbackByIdx[i] === 'up'
                            ? 'opacity-100 text-primary'
                            : 'text-muted-foreground hover:text-foreground'
                        }`}
                        title={t('chat.feedback_good')}
                        aria-pressed={feedbackByIdx[i] === 'up'}
                      >
                        <ThumbsUp
                          size={12}
                          className={feedbackByIdx[i] === 'up' ? 'fill-current' : ''}
                        />
                      </button>
                      <button
                        onClick={() => voteOnMessage(i, 'down')}
                        disabled={loading}
                        className={`opacity-0 group-hover:opacity-100 transition-opacity disabled:cursor-not-allowed ${
                          feedbackByIdx[i] === 'down'
                            ? 'opacity-100 text-warn'
                            : 'text-muted-foreground hover:text-foreground'
                        }`}
                        title={t('chat.feedback_bad')}
                        aria-pressed={feedbackByIdx[i] === 'down'}
                      >
                        <ThumbsDown
                          size={12}
                          className={feedbackByIdx[i] === 'down' ? 'fill-current' : ''}
                        />
                      </button>
                      <button
                        onClick={() => regenerateMessage(i)}
                        disabled={regeneratingIdx !== null || loading}
                        className="opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground disabled:opacity-30 disabled:cursor-not-allowed"
                        title={t('chat.regenerate')}
                      >
                        <RefreshCw
                          size={12}
                          className={regeneratingIdx === i ? 'animate-spin' : ''}
                        />
                      </button>
                    </>
                  )}
                  <button
                    onClick={() => copyMessage(msg.text, i)}
                    className={`opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground ${i === lastAiIdx ? '' : 'ml-auto'}`}
                    title={t('chat.copy_response')}
                  >
                    {copiedIdx === i
                      ? <Check size={12} className="text-ok" />
                      : <Copy size={12} />}
                  </button>
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
            {/* Live progress card — renders the REAL process as it happens,
                in true event order (thinking → tool → thinking → …), using
                the SAME visual language as the finished "N steps" timeline.
                Canned phrases appear only in the first seconds before any
                real event has arrived. */}
            <div className="bg-muted rounded-xl px-4 py-3 text-sm rounded-bl-sm max-w-[82%] min-w-[260px]">
              <div className="flex items-center justify-between gap-4 mb-2">
                <span className="inline-flex items-center gap-1.5 text-2xs font-medium text-muted-foreground">
                  <Loader2 size={12} className="animate-spin text-primary" aria-hidden />
                  <span>
                    {liveSteps.length > 0
                      ? `${explodeProcess(liveSteps).length} ${t('chat.steps')}`
                      : t('chat.working')}
                  </span>
                </span>
                {elapsedSec > 0 && (
                  <span className="text-3xs text-muted-foreground/60 tabular-nums">
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
                const exploded = explodeProcess(liveSteps);
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
                            <div className="text-2xs text-muted-foreground/80 whitespace-pre-wrap leading-relaxed">
                              {(step.text || '').replace(/\*\*/g, '')}
                            </div>
                          </TimelineRow>
                        );
                      }
                      return isActiveTool ? (
                        <TimelineRow key={si} marker={ACTIVE_BEACON} last>
                          <div className="text-xs font-medium text-foreground animate-pulse">
                            {step.label}…
                          </div>
                        </TimelineRow>
                      ) : (
                        <TimelineRow
                          key={si}
                          marker={<Check size={12} className="text-primary/70" />}
                          last={false}
                        >
                          <div className="text-2xs font-medium text-muted-foreground">
                            {step.label}
                          </div>
                        </TimelineRow>
                      );
                    })}
                    {/* Trailing activity row: canned phrase only before the
                        first real event; honest "Thinking…" while the last
                        step is a growing thought. */}
                    {!lastIsTool && (
                      <TimelineRow marker={ACTIVE_BEACON} last>
                        <div className="text-xs font-medium text-foreground animate-pulse">
                          {exploded.length === 0
                            ? THINK_PHRASES[thinkIdx]
                            : `${t('chat.thinking_live')}…`}
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
                <RotateCcw size={12} />
                Retry
              </button>
            )}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Follow-up suggestions */}
      {suggestions.length > 0 && !loading && (
        <div className="flex flex-wrap gap-1.5 mt-2 pt-2 border-t border-border flex-shrink-0">
          {suggestions.map((s, i) => (
            <button
              key={i}
              onClick={() => send(s)}
              disabled={loading}
              className="px-3 py-1.5 text-xs rounded-full bg-muted hover:bg-primary/10 hover:text-primary hover:border-primary/30 text-foreground/70 border border-border transition-colors disabled:opacity-50"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {/* Slash-command menu — appears when the input starts with "/" */}
      {slashMatches.length > 0 && !loading && (
        <div className="flex flex-col gap-0.5 mt-2 rounded-lg border border-border bg-card p-1 shadow-sm flex-shrink-0">
          {slashMatches.map((c) => (
            <button
              key={c.name}
              onClick={() => { setInput(''); c.run(); }}
              className="flex items-center gap-2 px-3 py-1.5 text-sm rounded-md text-left text-foreground/80 hover:bg-muted transition-colors"
            >
              <Sparkles size={14} className="text-primary shrink-0" aria-hidden />
              <span className="font-medium">/{c.name}</span>
              <span className="text-2xs text-muted-foreground">{c.label}</span>
            </button>
          ))}
        </div>
      )}

      {/* Input */}
      <div className="flex gap-2 mt-3 flex-shrink-0">
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            e.target.style.height = 'auto';
            e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
          }}
          onKeyDown={handleKeyDown}
          placeholder={`Ask about ${chatSubject}…  (or type / for commands)`}
          rows={1}
          style={{ maxHeight: '120px' }}
          className="flex-1 bg-card text-foreground rounded-xl px-4 py-3 text-sm border border-border focus:border-ring focus:ring-2 focus:ring-ring/20 focus:outline-none resize-none transition-colors placeholder:text-muted-foreground/50"
          disabled={loading}
        />
        {loading ? (
          <button
            onClick={stopGeneration}
            className="px-4 py-3 rounded-lg bg-muted hover:bg-muted/80 text-foreground font-medium text-sm transition-colors border border-border flex items-center gap-1.5 shrink-0"
            title="Stop generating"
          >
            <Square size={14} className="fill-current" />
            Stop
          </button>
        ) : (
          <button
            onClick={() => submit(input)}
            disabled={!input.trim()}
            className="px-4 py-3 rounded-lg bg-primary hover:bg-primary/90 text-primary-foreground font-medium text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5 shrink-0"
          >
            <Send size={14} />
            Send
          </button>
        )}
      </div>
    </div>
  );
}
