import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate } from 'react-router-dom';
import { Bot, ChevronDown, Send, Trash2, Copy, Check, RefreshCw, Sparkles, MessageSquare, Pencil, Download, RotateCcw } from 'lucide-react';
import { apiJSON, apiJSONAI, apiStreamChat } from '../../api/client';
import { useShellConfig } from '../../hooks/useShellConfig';
import type { AIChatMessage, AIChatResponse, AIHistoryResponse, AISummaryResponse, AIModel, AIModelsResponse, AIUsage } from '../../types';
import { formatAIResponse } from '../../utils/formatAI';

// Extended message type with client-side timestamp
interface LocalMessage extends AIChatMessage {
  timestamp: Date;
  usage?: AIUsage;
}

// No hardcoded loading messages — we show real tool activity from the stream

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
        'Show current fuel status',
        'Any vehicles in unusual locations?',
        'Which routes are active today?',
      ];
    case 'safety':
      return [
        'Any critical faults today?',
        'Show safety events this week',
        'Which drivers had harsh braking?',
        'Trucks with stop engine lights?',
      ];
    case 'admin':
    case 'owner':
      return [
        'Give me a status briefing for the operation',
        'Which trucks have active faults?',
        'Show me fuel costs this month',
        'Any overdue maintenance tasks?',
      ];
    default:
      return [
        'How are my vehicles doing today?',
        'Which trucks have active faults?',
        'Show me fuel costs this month',
        'Any overdue maintenance tasks?',
      ];
  }
}

export default function Chat() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const { activeView, isDriverView, briefingLabel, chatSubject } = useShellConfig();

  // ── Tab state ────────────────────────────────────────────────
  const [activeTab, setActiveTab] = useState<'chat' | 'briefing'>(() =>
    new URLSearchParams(location.search).get('tab') === 'briefing' ? 'briefing' : 'chat'
  );

  // ── Chat state ───────────────────────────────────────────────
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  const [clearConfirm, setClearConfirm] = useState(false);
  const clearConfirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Streaming state ──────────────────────────────────────────
  /** Tool labels shown while streaming (e.g. "Checking fault codes") */
  const [toolActivity, setToolActivity] = useState<string[]>([]);
  /** Last message that failed — shown in retry button */
  const [lastFailed, setLastFailed] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  /** Cycling index for pre-tool thinking phrases */
  const [thinkIdx, setThinkIdx] = useState(0);

  // ── Model state ──────────────────────────────────────────────
  const [models, setModels] = useState<AIModel[]>([]);
  const [currentModel, setCurrentModel] = useState('');
  const [accountDefault, setAccountDefault] = useState('');
  const [isAdmin, setIsAdmin] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [modelSwitching, setModelSwitching] = useState(false);

  // ── Briefing state ───────────────────────────────────────────
  const [briefing, setBriefing] = useState('');
  const [briefingTime, setBriefingTime] = useState<Date | null>(null);
  const [briefingSuggestions, setBriefingSuggestions] = useState<string[]>([]);
  const [briefingLoading, setBriefingLoading] = useState(false);
  const [briefingError, setBriefingError] = useState('');

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const modelRef = useRef<HTMLDivElement>(null);

  // Load conversation history + models on mount
  useEffect(() => {
    apiJSON<AIHistoryResponse>('/ai/history')
      .then((d) => setMessages((d.messages || []).map(m => ({
        ...m,
        // Use the backend timestamp when present so loaded scrollback
        // shows the real send time, not "12:34 PM" stamped at page load.
        timestamp: m.ts ? new Date(m.ts) : new Date(),
      }))))
      .catch((e: unknown) => {
        // Don't swallow silently — an empty chat after history failed
        // to load is indistinguishable from a genuine first-visit
        // empty state, which is confusing.  Surface it.
        const msg = e instanceof Error ? e.message : String(e);
        setError(`Couldn't load chat history: ${msg}`);
      });
    apiJSON<AIModelsResponse>('/ai/models')
      .then((d) => {
        setModels(d.models || []);
        setCurrentModel(d.current_text || '');
        setAccountDefault(d.account_default || '');
        setIsAdmin(d.is_admin ?? false);
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

  // Auto-scroll on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  // Cycle through thinking phrases before first tool fires
  const THINK_PHRASES = [
    'Reading your question\u2026',
    'Figuring out what data I need\u2026',
    'Preparing the right query\u2026',
    'Connecting to telematics\u2026',
  ];
  useEffect(() => {
    if (!loading || toolActivity.length > 0) return;
    setThinkIdx(0);
    const t = setInterval(() => setThinkIdx((i) => (i + 1) % THINK_PHRASES.length), 1800);
    return () => clearInterval(t);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, toolActivity.length]);

  // Sync tab with URL query param (?tab=briefing)
  useEffect(() => {
    const tab = new URLSearchParams(location.search).get('tab');
    setActiveTab(tab === 'briefing' ? 'briefing' : 'chat');
  }, [location.search]);

  // Auto-load Fleet Briefing when switching to that tab (if not already loaded)
  useEffect(() => {
    if (activeTab === 'briefing' && !briefing && !briefingLoading && !briefingError) {
      generateBriefing();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  // Close model dropdown on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (modelRef.current && !modelRef.current.contains(e.target as Node)) {
        setModelOpen(false);
      }
    }
    if (modelOpen) document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [modelOpen]);

  // Cleanup clear-confirm timeout on unmount
  useEffect(() => {
    return () => { if (clearConfirmTimer.current) clearTimeout(clearConfirmTimer.current); };
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
    setLastFailed(null);
    setActiveTab('chat');

    // Cancel any in-flight request
    abortRef.current?.abort();
    const abort = new AbortController();
    abortRef.current = abort;

    try {
      let finalReply = '';
      let finalSuggestions: string[] = [];
      let finalUsage: AIUsage | undefined;

      await apiStreamChat(
        text.trim(),
        (event) => {
          if (event.type === 'tool') {
            setToolActivity((prev) => [...prev, event.label]);
          } else if (event.type === 'done') {
            finalReply = event.reply;
            finalSuggestions = event.suggestions || [];
            finalUsage = event.usage as unknown as AIUsage | undefined;
          } else if (event.type === 'error') {
            throw new Error(event.message);
          }
        },
        abort.signal,
      );

      if (!finalReply) throw new Error('No response received');
      const aiMsg: LocalMessage = { role: 'model', text: finalReply, timestamp: new Date(), usage: finalUsage };
      setMessages((prev) => [...prev, aiMsg]);
      setSuggestions(finalSuggestions);
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') return;
      const msg = e instanceof Error ? e.message : 'Failed to get response';
      if (msg.includes('429') || msg.toLowerCase().includes('rate limit') || msg.toLowerCase().includes('too many')) {
        setError('Too many messages \u2014 please wait a moment before sending again.');
      } else {
        setError(msg);
      }
      setLastFailed(text.trim());
    } finally {
      setLoading(false);
      setToolActivity([]);
      inputRef.current?.focus();
    }
  }

  async function switchModel(modelName: string) {
    if (modelSwitching) return;
    setModelSwitching(true);
    try {
      await apiJSON('/ai/user-model', {
        method: 'PUT',
        body: { model_name: modelName },
      });
      setCurrentModel(modelName);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to switch model');
    } finally {
      setModelSwitching(false);
      setModelOpen(false);
    }
  }

  async function clearChat() {
    abortRef.current?.abort();
    await apiJSON('/ai/history', { method: 'DELETE' }).catch(() => {});
    setMessages([]);
    setSuggestions([]);
    setError('');
    setLastFailed(null);
    setToolActivity([]);
    setClearConfirm(false);
  }

  function handleClearClick() {
    if (!clearConfirm) {
      setClearConfirm(true);
      if (clearConfirmTimer.current) clearTimeout(clearConfirmTimer.current);
      clearConfirmTimer.current = setTimeout(() => setClearConfirm(false), 3000);
    } else {
      clearChat();
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
      send(input);
    }
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

  function exportChat() {
    const lines = messages.map((m) => {
      const time = m.timestamp.toLocaleString();
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

  // ── Briefing functions ───────────────────────────────────────
  async function generateBriefing() {
    setBriefingLoading(true);
    setBriefingError('');
    try {
      const data = await apiJSONAI<AISummaryResponse>('/ai/summary', { method: 'POST' });
      setBriefing(data.summary);
      setBriefingTime(new Date());
      setBriefingSuggestions(data.suggestions || []);
    } catch (e) {
      setBriefingError(e instanceof Error ? e.message : 'Failed to generate briefing');
    } finally {
      setBriefingLoading(false);
    }
  }

  function switchTab(tab: 'chat' | 'briefing') {
    navigate(`/ai/chat${tab === 'briefing' ? '?tab=briefing' : ''}`, { replace: true });
  }


  const suggestedQuestions = getSuggestedQuestions(activeView);

  return (
    <div className="flex flex-col h-[calc(100vh-6rem)]">
      {/* ── Header ────────────────────────────────────────────── */}
      <div className="flex items-center justify-between mb-4 flex-shrink-0">
        <h1 className="text-xl font-semibold flex items-center gap-2">
          <Bot size={20} className="text-primary" />
          AI Assistant
        </h1>

        {/* Right-side controls: model selector + export + clear */}
        <div className="flex items-center gap-2">
          {/* Model selector */}
          {currentModel && (
            <div className="relative" ref={modelRef}>
              <button
                onClick={() => setModelOpen(!modelOpen)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border transition-colors bg-muted border-border hover:border-ring text-foreground/80 cursor-pointer"
                title={t('chat.switch_model')}
              >
                <span className="max-w-[140px] truncate">
                  {models.find((m) => m.name === currentModel)?.display || currentModel}
                </span>
                <ChevronDown size={12} className={`transition-transform shrink-0 ${modelOpen ? 'rotate-180' : ''}`} />
              </button>
              {modelOpen && (
                <div className="absolute right-0 top-full mt-1 z-50 w-72 max-h-80 overflow-y-auto rounded-lg border border-border bg-card shadow-xl">
                  {(() => {
                    // Group by upstream maker (Google, Anthropic, OpenAI, …) and
                    // render in a stable order: flagship/frontier first, then
                    // the rest alphabetical with "Other" at the bottom.  This
                    // replaces the legacy gemini/maas grouping which exposed
                    // backend API shape rather than who built each model.
                    const MAKER_ORDER = [
                      'Google', 'Anthropic', 'OpenAI', 'Meta',
                      'DeepSeek', 'Alibaba', 'Moonshot', 'Mistral',
                      'xAI', 'MiniMax', 'Zhipu',
                    ];
                    const grouped = models.reduce<Record<string, AIModel[]>>((acc, m) => {
                      (acc[m.maker || 'Other'] ??= []).push(m);
                      return acc;
                    }, {});
                    const orderedMakers = [
                      ...MAKER_ORDER.filter((mk) => grouped[mk]?.length),
                      ...Object.keys(grouped)
                        .filter((mk) => !MAKER_ORDER.includes(mk) && mk !== 'Other')
                        .sort(),
                      ...(grouped['Other']?.length ? ['Other'] : []),
                    ];
                    return orderedMakers.map((maker) => (
                      <div key={maker}>
                        <div className="px-3 py-1.5 text-3xs uppercase tracking-wider text-muted-foreground bg-muted/50 sticky top-0">
                          {maker}
                        </div>
                        {grouped[maker].map((m) => (
                          <button
                            key={m.name}
                            onClick={() => switchModel(m.name)}
                            disabled={modelSwitching || m.name === currentModel}
                            className={`w-full text-left px-3 py-2 text-sm transition-colors ${
                              m.name === currentModel
                                ? 'bg-primary/15 text-primary'
                                : 'text-foreground/80 hover:bg-muted'
                            } disabled:opacity-50`}
                          >
                            <div className="flex items-center justify-between">
                              <span className="truncate">{m.display}</span>
                              <span className="flex items-center gap-1 ml-2 flex-shrink-0">
                                {m.name === accountDefault && isAdmin && (
                                  <span className="text-3xs text-yellow-500">{t('chat.model_default')}</span>
                                )}
                                {m.name === currentModel && (
                                  <span className="text-3xs text-primary">{t('chat.model_active')}</span>
                                )}
                              </span>
                            </div>
                            {m.description && (
                              <span className="text-3xs text-muted-foreground block truncate">{m.description}</span>
                            )}
                          </button>
                        ))}
                      </div>
                    ));
                  })()}
                </div>
              )}
            </div>
          )}

          {/* Separator */}
          {activeTab === 'chat' && messages.length > 0 && (
            <div className="w-px h-5 bg-border" />
          )}

          {/* Export + Clear — only in Chat tab with messages */}
          {activeTab === 'chat' && messages.length > 0 && (
            <>
              <button
                onClick={exportChat}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg transition-colors bg-muted hover:bg-muted/80 text-muted-foreground border border-border"
                title={t('chat.export_conversation')}
              >
                <Download size={14} />
                {t('chat.export')}
              </button>
              <button
                onClick={handleClearClick}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border transition-colors ${
                  clearConfirm
                    ? 'bg-destructive/15 text-destructive border-destructive/30 hover:bg-destructive/25'
                    : 'bg-muted hover:bg-muted/80 text-muted-foreground border-border'
                }`}
              >
                <Trash2 size={14} />
                {clearConfirm ? t('chat.confirm_clear') : t('chat.clear')}
              </button>
            </>
          )}
        </div>
      </div>

      {/* ── Tabs ──────────────────────────────────────────────── */}
      <div className="flex gap-1 mb-3 border-b border-border flex-shrink-0">
        <button
          onClick={() => switchTab('chat')}
          className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
            activeTab === 'chat'
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          <MessageSquare size={14} />
          {t('chat.tab_chat')}
        </button>
        <button
          onClick={() => switchTab('briefing')}
          className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
            activeTab === 'briefing'
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          <Sparkles size={14} />
          {briefingLabel}
        </button>
      </div>

      {/* ══ CHAT TAB ════════════════════════════════════════════ */}
      {activeTab === 'chat' && (
        <>
          {/* Messages area */}
          <div className="flex-1 overflow-y-auto space-y-4 pr-1 min-h-0">
            {messages.length === 0 && !loading && (
              <div className="text-center text-muted-foreground mt-16">
                <Bot size={40} className="mx-auto mb-3 text-primary/40" />
                <p className="text-lg font-medium">{t('chat.title')}</p>
                <p className="text-sm mt-1">
                  {isDriverView
                    ? t('chat.ask_driver')
                    : `Ask anything about ${chatSubject} \u2014 vehicles, faults, fuel, events, maintenance.`}
                </p>
                <div className="mt-6 flex flex-wrap justify-center gap-2">
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
                        {msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="max-w-[82%] group">
                    <div
                      className="rounded-2xl px-4 py-3 text-sm bg-card border border-border text-foreground rounded-bl-none ai-response shadow-sm"
                      dangerouslySetInnerHTML={{ __html: formatAIResponse(msg.text) }}
                    />
                    <div className="flex items-center gap-2 mt-1">
                      <span className="text-3xs text-muted-foreground/60">
                        {models.find((m) => m.name === currentModel)?.display || currentModel}
                      </span>
                      <button
                        onClick={() => copyMessage(msg.text, i)}
                        className="opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground ml-auto"
                        title={t('chat.copy_response')}
                      >
                        {copiedIdx === i
                          ? <Check size={12} className="text-ok" />
                          : <Copy size={12} />}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}

            {loading && (
              <div className="flex justify-start">
                <div className="bg-muted rounded-xl px-4 py-3 text-sm rounded-bl-sm max-w-[82%] min-w-[220px]">
                  {/* Completed steps */}
                  {toolActivity.slice(0, toolActivity.length > 0 ? toolActivity.length - 1 : 0).map((label, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs text-muted-foreground/70 mb-1.5">
                      <Check size={12} className="text-primary/70 flex-shrink-0" />
                      <span>{label}</span>
                    </div>
                  ))}
                  {/* Active step */}
                  <div className="flex items-center gap-2">
                    <span className="inline-flex gap-0.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce" style={{ animationDelay: '0ms' }} />
                      <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce" style={{ animationDelay: '150ms' }} />
                      <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce" style={{ animationDelay: '300ms' }} />
                    </span>
                    <span className="text-foreground font-medium">
                      {toolActivity.length > 0
                        ? `${toolActivity[toolActivity.length - 1]}\u2026`
                        : THINK_PHRASES[thinkIdx]}
                    </span>
                  </div>
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
              placeholder={`Ask about ${chatSubject}\u2026`}
              rows={1}
              style={{ maxHeight: '120px' }}
              className="flex-1 bg-card text-foreground rounded-xl px-4 py-3 text-sm border border-border focus:border-ring focus:ring-2 focus:ring-ring/20 focus:outline-none resize-none transition-colors placeholder:text-muted-foreground/50"
              disabled={loading}
            />
            <button
              onClick={() => send(input)}
              disabled={loading || !input.trim()}
              className="px-4 py-3 rounded-lg bg-primary hover:bg-primary/90 text-primary-foreground font-medium text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5 shrink-0"
            >
              <Send size={14} />
              Send
            </button>
          </div>
        </>
      )}

      {/* ══ BRIEFING TAB ════════════════════════════════════════ */}
      {activeTab === 'briefing' && (
        <div className="flex-1 overflow-y-auto min-h-0">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-lg font-semibold">{briefingLabel}</h2>
              {briefingTime && (
                <p className="text-xs text-muted-foreground mt-0.5">
                  Generated at {briefingTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </p>
              )}
            </div>
            <button
              onClick={generateBriefing}
              disabled={briefingLoading}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary hover:bg-primary/90 text-primary-foreground font-medium text-sm transition-colors disabled:opacity-50"
            >
              <RefreshCw size={14} className={briefingLoading ? 'animate-spin' : ''} />
              {briefingLoading ? 'Generating\u2026' : briefing ? 'Refresh' : 'Generate'}
            </button>
          </div>

          {briefingError && <p className="text-destructive mb-4 text-sm">{briefingError}</p>}

          {!briefing && !briefingLoading && !briefingError && (
            <div className="text-center text-muted-foreground mt-16">
              <Sparkles size={40} className="mx-auto mb-3 text-primary/40" />
              <p className="text-lg font-medium">{briefingLabel}</p>
              <p className="text-sm mt-1">
                {isDriverView
                  ? "Get a quick summary of your truck's current status, health, and any issues."
                  : `Generate an executive summary of ${chatSubject} — status, health, events, and recommendations.`}
              </p>
              <button
                onClick={generateBriefing}
                className="mt-6 px-6 py-3 rounded-lg bg-primary hover:bg-primary/90 text-primary-foreground font-medium transition-colors"
              >
                Generate Briefing
              </button>
            </div>
          )}

          {briefingLoading && (
            <div className="flex justify-center mt-16">
              <div className="text-muted-foreground text-sm flex items-center gap-2">
                <span className="inline-flex gap-1">
                  <span className="animate-bounce" style={{ animationDelay: '0ms' }}>&#9679;</span>
                  <span className="animate-bounce" style={{ animationDelay: '150ms' }}>&#9679;</span>
                  <span className="animate-bounce" style={{ animationDelay: '300ms' }}>&#9679;</span>
                </span>
                Analyzing {chatSubject}\u2026
              </div>
            </div>
          )}

          {briefing && !briefingLoading && (
            <>
              <div
                className="bg-muted rounded-xl p-6 text-sm text-foreground/90 leading-relaxed ai-response"
                dangerouslySetInnerHTML={{ __html: formatAIResponse(briefing) }}
              />
              {briefingSuggestions.length > 0 && (
                <div className="mt-4 flex flex-wrap gap-2">
                  <span className="text-xs text-muted-foreground self-center mr-1">Follow-up:</span>
                  {briefingSuggestions.map((s, i) => (
                    <button
                      key={i}
                      onClick={() => {
                        switchTab('chat');
                        setTimeout(() => send(s), 0);
                      }}
                      className="px-3 py-1.5 text-xs rounded-full bg-card text-foreground/80 border border-border hover:bg-muted transition-colors"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
