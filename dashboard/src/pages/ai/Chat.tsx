import { useState, useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { apiJSON } from '../../api/client';
import type { AIChatMessage, AIChatResponse, AIHistoryResponse, AIModel, AIModelsResponse } from '../../types';
import { formatAIResponse } from '../../utils/formatAI';

export default function Chat() {
  const location = useLocation();
  const [messages, setMessages] = useState<AIChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [models, setModels] = useState<AIModel[]>([]);
  const [currentModel, setCurrentModel] = useState('');
  const [accountDefault, setAccountDefault] = useState('');
  const [isAdmin, setIsAdmin] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [modelSwitching, setModelSwitching] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const modelRef = useRef<HTMLDivElement>(null);

  // Load conversation history + models on mount
  useEffect(() => {
    apiJSON<AIHistoryResponse>('/ai/history')
      .then((d) => setMessages(d.messages || []))
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

  // Handle initial message from Summary page navigation
  useEffect(() => {
    const state = location.state as { initialMessage?: string } | null;
    if (state?.initialMessage) {
      send(state.initialMessage);
      // Clear the state so it doesn't re-send on re-render
      window.history.replaceState({}, document.title);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-scroll on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  async function send(text: string) {
    if (!text.trim() || loading) return;
    const userMsg: AIChatMessage = { role: 'user', text: text.trim() };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setSuggestions([]);
    setLoading(true);
    setError('');

    try {
      const data = await apiJSON<AIChatResponse>('/ai/chat', {
        method: 'POST',
        body: { message: text.trim() },
      });
      const aiMsg: AIChatMessage = { role: 'model', text: data.reply };
      setMessages((prev) => [...prev, aiMsg]);
      setSuggestions(data.suggestions || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to get response');
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

  async function clearChat() {
    await apiJSON('/ai/history', { method: 'DELETE' }).catch(() => {});
    setMessages([]);
    setSuggestions([]);
    setError('');
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-6rem)]">
      {/* Header */}
      <div className="flex items-center justify-between mb-4 flex-shrink-0">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold">🤖 AI Fleet Assistant</h1>
          {/* Model selector — all users can pick their preferred model */}
          {currentModel && (
            <div className="relative" ref={modelRef}>
              <button
                onClick={() => setModelOpen(!modelOpen)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-full border transition-colors bg-gray-800 border-gray-600 hover:border-blue-500 text-gray-300 cursor-pointer"
                title="Switch AI model"
              >
                <span className="max-w-[140px] truncate">
                  {models.find((m) => m.name === currentModel)?.display || currentModel}
                </span>
                <svg className={`w-3 h-3 transition-transform ${modelOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              {modelOpen && (
                <div className="absolute left-0 top-full mt-1 z-50 w-72 max-h-80 overflow-y-auto rounded-lg border border-gray-700 bg-gray-900 shadow-xl">
                  {Object.entries(
                    models.reduce<Record<string, AIModel[]>>((acc, m) => {
                      (acc[m.category] ??= []).push(m);
                      return acc;
                    }, {})
                  ).map(([category, items]) => (
                    <div key={category}>
                      <div className="px-3 py-1.5 text-[10px] uppercase tracking-wider text-gray-500 bg-gray-800/50 sticky top-0">
                        {category}
                      </div>
                      {items.map((m) => (
                        <button
                          key={m.name}
                          onClick={() => switchModel(m.name)}
                          disabled={modelSwitching || m.name === currentModel}
                          className={`w-full text-left px-3 py-2 text-sm transition-colors ${
                            m.name === currentModel
                              ? 'bg-blue-600/20 text-blue-300'
                              : 'text-gray-300 hover:bg-gray-800'
                          } disabled:opacity-50`}
                        >
                          <div className="flex items-center justify-between">
                            <span className="truncate">{m.display}</span>
                            <span className="flex items-center gap-1 ml-2 flex-shrink-0">
                              {m.name === accountDefault && isAdmin && (
                                <span className="text-[10px] text-yellow-500">default</span>
                              )}
                              {m.name === currentModel && (
                                <span className="text-[10px] text-blue-400">active</span>
                              )}
                            </span>
                          </div>
                          {isAdmin && m.cost_per_request != null && (
                            <span className="text-[10px] text-gray-500">${m.cost_per_request}/req</span>
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
        {messages.length > 0 && (
          <button
            onClick={clearChat}
            className="px-3 py-1.5 text-sm rounded bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
          >
            Clear Chat
          </button>
        )}
      </div>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto space-y-3 pr-2 min-h-0">
        {messages.length === 0 && !loading && (
          <div className="text-center text-gray-500 mt-20">
            <p className="text-4xl mb-3">🤖</p>
            <p className="text-lg font-medium">AI Fleet Assistant</p>
            <p className="text-sm mt-1">Ask anything about your fleet — vehicles, faults, fuel, events, maintenance.</p>
            <div className="mt-6 flex flex-wrap justify-center gap-2">
              {[
                'How is my fleet doing today?',
                'Which trucks have active faults?',
                'Show me fuel costs this month',
                'Any overdue maintenance tasks?',
              ].map((q) => (
                <button
                  key={q}
                  onClick={() => send(q)}
                  className="px-3 py-2 text-sm rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 border border-gray-700 transition-colors"
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
              <div className="max-w-[80%] rounded-xl px-4 py-3 text-sm whitespace-pre-wrap bg-blue-600/30 text-blue-100 rounded-br-sm">
                {msg.text}
              </div>
            ) : (
              <div
                className="max-w-[80%] rounded-xl px-4 py-3 text-sm bg-gray-800 text-gray-200 rounded-bl-sm ai-response"
                dangerouslySetInnerHTML={{ __html: formatAIResponse(msg.text) }}
              />
            )}
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="bg-gray-800 rounded-xl px-4 py-3 text-sm text-gray-400 rounded-bl-sm">
              <span className="inline-flex gap-1">
                <span className="animate-bounce" style={{ animationDelay: '0ms' }}>●</span>
                <span className="animate-bounce" style={{ animationDelay: '150ms' }}>●</span>
                <span className="animate-bounce" style={{ animationDelay: '300ms' }}>●</span>
              </span>
            </div>
          </div>
        )}

        {error && (
          <div className="flex justify-center">
            <p className="text-red-400 text-sm bg-red-400/10 px-3 py-2 rounded">{error}</p>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Suggestions */}
      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-2 mt-2 flex-shrink-0">
          {suggestions.map((s, i) => (
            <button
              key={i}
              onClick={() => send(s)}
              disabled={loading}
              className="px-3 py-1.5 text-xs rounded-full bg-gray-800 hover:bg-gray-700 text-gray-300 border border-gray-700 transition-colors disabled:opacity-50"
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
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your fleet..."
          rows={1}
          className="flex-1 bg-gray-800 text-gray-100 rounded-lg px-4 py-3 text-sm border border-gray-700 focus:border-blue-500 focus:outline-none resize-none"
          disabled={loading}
        />
        <button
          onClick={() => send(input)}
          disabled={loading || !input.trim()}
          className="px-5 py-3 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Send
        </button>
      </div>
    </div>
  );
}
