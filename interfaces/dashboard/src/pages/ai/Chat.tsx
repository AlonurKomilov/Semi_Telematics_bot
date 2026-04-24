import { useState, useEffect, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Bot, ChevronDown, Send, Trash2, Copy, Check, RefreshCw, Sparkles, MessageSquare } from 'lucide-react';
import { apiJSON, apiJSONAI } from '../../api/client';
import { useAuth } from '../../context/AuthContext';
import type { AIChatMessage, AIChatResponse, AIHistoryResponse, AISummaryResponse, AIModel, AIModelsResponse } from '../../types';
import { formatAIResponse } from '../../utils/formatAI';

// Extended message type with client-side timestamp
interface LocalMessage extends AIChatMessage {
  timestamp: Date;
}

const LOADING_STATUSES = [
  'Checking live fleet data\u2026',
  'Analyzing vehicle status\u2026',
  'Running diagnostics\u2026',
  'Calculating metrics\u2026',
  'Gathering insights\u2026',
];

function getSuggestedQuestions(role?: string): string[] {
  switch (role) {
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
    default:
      return [
        'How is my fleet doing today?',
        'Which trucks have active faults?',
        'Show me fuel costs this month',
        'Any overdue maintenance tasks?',
      ];
  }
}

export default function Chat() {
  const location = useLocation();
  const navigate = useNavigate();
  const { user } = useAuth();

  // ── Tab state ────────────────────────────────────────────────
  const [activeTab, setActiveTab] = useState<'chat' | 'briefing'>(() =>
    new URLSearchParams(location.search).get('tab') === 'briefing' ? 'briefing' : 'chat'
  );

  // ── Chat state ───────────────────────────────────────────────
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [statusIdx, setStatusIdx] = useState(0);
  const [error, setError] = useState('');
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  const [clearConfirm, setClearConfirm] = useState(false);
  const clearConfirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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
      .then((d) => setMessages((d.messages || []).map(m => ({ ...m, timestamp: new Date() }))))
      .catch(() => {});
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

  // Cycle loading status message every 2 s while a request is in flight
  useEffect(() => {
    if (!loading) { setStatusIdx(0); return; }
    const t = setInterval(() => setStatusIdx(i => (i + 1) % LOADING_STATUSES.length), 2000);
    return () => clearInterval(t);
  }, [loading]);

  // Sync tab with URL query param (?tab=briefing)
  useEffect(() => {
    const tab = new URLSearchParams(location.search).get('tab');
    setActiveTab(tab === 'briefing' ? 'briefing' : 'chat');
  }, [location.search]);

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
    setActiveTab('chat');

    try {
      const data = await apiJSONAI<AIChatResponse>('/ai/chat', {
        method: 'POST',
        body: { message: text.trim() },
      });
      const aiMsg: LocalMessage = { role: 'model', text: data.reply, timestamp: new Date() };
      setMessages((prev) => [...prev, aiMsg]);
      setSuggestions(data.suggestions || []);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to get response';
      if (msg.includes('429') || msg.toLowerCase().includes('rate limit') || msg.toLowerCase().includes('too many')) {
        setError('Too many messages \u2014 please wait a moment before sending again.');
      } else {
        setError(msg);
      }
    } finally {
      setLoading(false);
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
    await apiJSON('/ai/history', { method: 'DELETE' }).catch(() => {});
    setMessages([]);
    setSuggestions([]);
    setError('');
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

  function copyMessage(text: string, idx: number) {
    navigator.clipboard.writeText(text).catch(() => {});
    setCopiedIdx(idx);
    setTimeout(() => setCopiedIdx(null), 1500);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
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


  const suggestedQuestions = getSuggestedQuestions(user?.role);

  return (
    <div className="flex flex-col h-[calc(100vh-6rem)]">
      {/* ── Header ────────────────────────────────────────────── */}
      <div className="flex items-center justify-between mb-3 flex-shrink-0">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Bot size={24} className="text-primary" />
            AI Assistant
          </h1>
          {/* Model selector — all users can pick their preferred model */}
          {currentModel && (
            <div className="relative" ref={modelRef}>
              <button
                onClick={() => setModelOpen(!modelOpen)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-full border transition-colors bg-muted border-border hover:border-ring text-foreground/80 cursor-pointer"
                title="Switch AI model"
              >
                <span className="max-w-[140px] truncate">
                  {models.find((m) => m.name === currentModel)?.display || currentModel}
                </span>
                <ChevronDown size={12} className={`transition-transform shrink-0 ${modelOpen ? 'rotate-180' : ''}`} />
              </button>
              {modelOpen && (
                <div className="absolute left-0 top-full mt-1 z-50 w-72 max-h-80 overflow-y-auto rounded-lg border border-border bg-card shadow-xl">
                  {Object.entries(
                    models.reduce<Record<string, AIModel[]>>((acc, m) => {
                      (acc[m.category] ??= []).push(m);
                      return acc;
                    }, {})
                  ).map(([category, items]) => (
                    <div key={category}>
                      <div className="px-3 py-1.5 text-[10px] uppercase tracking-wider text-muted-foreground bg-muted/50 sticky top-0">
                        {category}
                      </div>
                      {items.map((m) => (
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
                                <span className="text-[10px] text-yellow-500">default</span>
                              )}
                              {m.name === currentModel && (
                                <span className="text-[10px] text-primary">active</span>
                              )}
                            </span>
                          </div>
                          {isAdmin && m.cost_per_request != null && (
                            <span className="text-[10px] text-muted-foreground">${m.cost_per_request}/req</span>
                          )}
                        </button>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Clear button — only shown in Chat tab when there are messages */}
        {activeTab === 'chat' && messages.length > 0 && (
          <button
            onClick={handleClearClick}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg transition-colors ${
              clearConfirm
                ? 'bg-destructive/15 text-destructive border border-destructive/30 hover:bg-destructive/25'
                : 'bg-muted hover:bg-muted/80 text-muted-foreground'
            }`}
          >
            <Trash2 size={13} />
            {clearConfirm ? 'Confirm clear?' : 'Clear'}
          </button>
        )}
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
          Chat
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
          Fleet Briefing
        </button>
      </div>

      {/* ══ CHAT TAB ════════════════════════════════════════════ */}
      {activeTab === 'chat' && (
        <>
          {/* Messages area */}
          <div className="flex-1 overflow-y-auto space-y-3 pr-2 min-h-0">
            {messages.length === 0 && !loading && (
              <div className="text-center text-muted-foreground mt-16">
                <Bot size={40} className="mx-auto mb-3 text-primary/40" />
                <p className="text-lg font-medium">AI Assistant</p>
                <p className="text-sm mt-1">
                  {user?.role === 'driver'
                    ? 'Ask anything about your assigned truck.'
                    : 'Ask anything about your fleet \u2014 vehicles, faults, fuel, events, maintenance.'}
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
                  <div className="max-w-[80%]">
                    <div className="rounded-xl px-4 py-3 text-sm whitespace-pre-wrap bg-primary/20 text-foreground rounded-br-sm">
                      {msg.text}
                    </div>
                    <p className="text-[10px] text-muted-foreground mt-0.5 text-right">
                      {msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </p>
                  </div>
                ) : (
                  <div className="max-w-[80%] group">
                    <div
                      className="rounded-xl px-4 py-3 text-sm bg-muted text-foreground/90 rounded-bl-sm ai-response"
                      dangerouslySetInnerHTML={{ __html: formatAIResponse(msg.text) }}
                    />
                    <div className="flex items-center gap-2 mt-0.5">
                      <p className="text-[10px] text-muted-foreground">
                        {msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </p>
                      <button
                        onClick={() => copyMessage(msg.text, i)}
                        className="opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground"
                        title="Copy response"
                      >
                        {copiedIdx === i
                          ? <Check size={11} className="text-green-500" />
                          : <Copy size={11} />}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}

            {loading && (
              <div className="flex justify-start">
                <div className="bg-muted rounded-xl px-4 py-3 text-sm text-muted-foreground rounded-bl-sm">
                  <span className="inline-flex gap-1 mr-2">
                    <span className="animate-bounce" style={{ animationDelay: '0ms' }}>&#9679;</span>
                    <span className="animate-bounce" style={{ animationDelay: '150ms' }}>&#9679;</span>
                    <span className="animate-bounce" style={{ animationDelay: '300ms' }}>&#9679;</span>
                  </span>
                  {LOADING_STATUSES[statusIdx]}
                </div>
              </div>
            )}

            {error && (
              <div className="flex justify-center">
                <p className="text-destructive text-sm bg-destructive/10 px-3 py-2 rounded-lg">{error}</p>
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Follow-up suggestions */}
          {suggestions.length > 0 && !loading && (
            <div className="flex flex-wrap gap-2 mt-2 flex-shrink-0">
              {suggestions.map((s, i) => (
                <button
                  key={i}
                  onClick={() => send(s)}
                  disabled={loading}
                  className="px-3 py-1.5 text-xs rounded-full bg-muted hover:bg-muted/80 text-foreground/80 border border-border transition-colors disabled:opacity-50"
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
              placeholder={user?.role === 'driver' ? 'Ask about your truck\u2026' : 'Ask about your fleet\u2026'}
              rows={1}
              style={{ maxHeight: '120px' }}
              className="flex-1 bg-muted text-foreground rounded-lg px-4 py-3 text-sm border border-border focus:border-ring focus:ring-2 focus:ring-ring/20 focus:outline-none resize-none transition-colors"
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
              <h2 className="text-lg font-semibold">
                {user?.role === 'driver' ? 'My Truck Briefing' : 'Fleet Briefing'}
              </h2>
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
              <p className="text-lg font-medium">
                {user?.role === 'driver' ? 'My Truck Briefing' : 'Fleet Status Briefing'}
              </p>
              <p className="text-sm mt-1">
                {user?.role === 'driver'
                  ? "Get a quick summary of your truck's current status, health, and any issues."
                  : 'Generate an executive summary of your fleet status, health, events, and recommendations.'}
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
                Analyzing your {user?.role === 'driver' ? 'truck' : 'fleet'}\u2026
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
