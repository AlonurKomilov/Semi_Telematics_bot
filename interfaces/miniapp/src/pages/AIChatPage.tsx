/**
 * AIChatPage — driver-friendly AI chat for the miniapp.
 *
 * Wraps POST /api/ai/chat with a simple bubble layout. History is
 * loaded once on mount from /api/ai/history (server already filters
 * to current user). Send button is disabled while a request is
 * in flight to prevent double-fires; Telegram haptics on send/receive.
 */

import { memo, useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Icon24SendOutline,
  Icon24DeleteOutline,
  Icon24LockOutline,
} from '@vkontakte/icons';
import { Placeholder } from '@telegram-apps/telegram-ui';
import { apiJSON, classifyError } from '../api/client';
import { Snackbar, type SnackbarKind } from '../components/Snackbar';
import { ListRowsSkeleton } from '../components/Skeleton';
import { haptics } from '../hooks/useTelegram';
import { formatAIResponse } from '../utils/formatAI';

interface Props {
  active: boolean;
  userRole?: string;
}

interface ChatMessage {
  role: 'user' | 'model';
  text: string;
}

interface ChatResponse {
  reply: string;
  suggestions?: string[];
  usage?: Record<string, unknown>;
}

interface HistoryResponse {
  messages: ChatMessage[];
  count: number;
}

function getDriverPlaceholder(role?: string): string {
  if (role === 'driver') return 'Ask about your truck, route, or scorecard…';
  if (role === 'safety') return 'Ask about events, alerts, or coaching…';
  if (role === 'dispatcher') return 'Ask about trucks, routes, or geofences…';
  return 'Ask about your fleet…';
}

function getSuggestedQuestions(role?: string): string[] {
  if (role === 'driver') {
    return [
      'How was my driving today?',
      'Are there any alerts on my truck?',
      'What is my scorecard this week?',
    ];
  }
  if (role === 'safety') {
    return [
      'Which drivers have the most events this week?',
      'Show me critical alerts from yesterday',
      'Who needs coaching?',
    ];
  }
  if (role === 'dispatcher') {
    return [
      'Which trucks are stopped right now?',
      'Show me trucks near a geofence',
      'Any vehicles with low fuel?',
    ];
  }
  return [
    'What is the fleet status today?',
    'Show me critical faults across the fleet',
    'Which trucks need maintenance?',
  ];
}

export const AIChatPage = memo(function AIChatPage({ active, userRole }: Props) {
  const { t } = useTranslation();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [snack, setSnack] = useState<{ kind: SnackbarKind; text: string } | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [permDenied, setPermDenied] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Initial history load — runs once on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await apiJSON<HistoryResponse>('/api/ai/history');
        if (!cancelled) {
          setMessages(data.messages || []);
          if ((data.messages || []).length === 0) {
            setSuggestions(getSuggestedQuestions(userRole));
          }
        }
      } catch (e) {
        const ce = classifyError(e);
        if (!cancelled) {
          if (ce.kind === 'auth') {
            setPermDenied(true);
          } else {
            // Non-fatal — just start with empty history
            setSuggestions(getSuggestedQuestions(userRole));
          }
        }
      } finally {
        if (!cancelled) setLoadingHistory(false);
      }
    })();
    return () => { cancelled = true; };
  }, [userRole]);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (active && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages.length, active]);

  const send = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    haptics.selection();
    setMessages(prev => [...prev, { role: 'user', text: trimmed }]);
    setInput('');
    setSuggestions([]);
    setSending(true);
    try {
      // 90s timeout — the AI agent can take 30-60s when reasoning
      // over live fleet data + alerts + scorecard.  The default
      // ``REQUEST_TIMEOUT_MS`` (30s) was firing mid-reply and
      // surfacing as "No connection" toasts on legitimately-running
      // requests.
      const data = await apiJSON<ChatResponse>('/api/ai/chat', {
        method: 'POST',
        body: JSON.stringify({ message: trimmed }),
        headers: { 'Content-Type': 'application/json' },
        timeoutMs: 90_000,
      });
      setMessages(prev => [...prev, { role: 'model', text: data.reply }]);
      if (data.suggestions && data.suggestions.length > 0) {
        setSuggestions(data.suggestions);
      }
      haptics.success();
    } catch (e) {
      const ce = classifyError(e);
      const errMsg = ce.kind === 'network'
        ? 'No connection — try again when online.'
        : ce.kind === 'auth'
          ? 'Session expired. Re-launch from the bot.'
          : 'AI request failed. Try again.';
      setSnack({ kind: 'error', text: errMsg });
      haptics.error();
      // Roll back the user message so they can edit + retry
      setMessages(prev => prev.slice(0, -1));
      setInput(trimmed);
    } finally {
      setSending(false);
    }
  }, [sending]);

  const onClear = useCallback(async () => {
    if (sending || messages.length === 0) return;
    if (!confirm('Clear conversation history?')) return;
    try {
      await apiJSON('/api/ai/history', { method: 'DELETE' });
      setMessages([]);
      setSuggestions(getSuggestedQuestions(userRole));
      setSnack({ kind: 'success', text: 'History cleared' });
      haptics.success();
    } catch {
      setSnack({ kind: 'error', text: 'Could not clear history' });
    }
  }, [sending, messages.length, userRole]);

  if (permDenied) {
    return (
      <div className="page-content">
        <Placeholder
          header="AI Assistant unavailable"
          description="Your account does not have AI enabled, or you are not signed in."
        >
          <Icon24LockOutline />
        </Placeholder>
      </div>
    );
  }

  return (
    <div className="ai-chat-page" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <div style={{
        padding: '12px 16px', borderBottom: '1px solid var(--tgui--divider, #2a2a2a)',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        flexShrink: 0,
      }}>
        <h2 style={{ margin: 0, fontSize: 'var(--st-text-lg)', fontWeight: 600 }}>AI Assistant</h2>
        {messages.length > 0 && (
          <button
            type="button"
            onClick={onClear}
            disabled={sending}
            aria-label={t('common.cancel')}
            style={{
              background: 'transparent', border: 0, cursor: 'pointer', padding: 6,
              color: 'var(--tgui--hint_color, #999)',
            }}
          >
            <Icon24DeleteOutline width={20} height={20} />
          </button>
        )}
      </div>

      {/* Message list */}
      <div
        ref={scrollRef}
        style={{
          flex: 1, overflowY: 'auto', padding: '12px 16px',
          WebkitOverflowScrolling: 'touch',
        }}
      >
        {loadingHistory && <ListRowsSkeleton count={3} />}

        {!loadingHistory && messages.length === 0 && (
          <div style={{ textAlign: 'center', padding: '24px 8px', color: 'var(--tgui--hint_color, #888)' }}>
            <p style={{ marginBottom: 12, fontSize: 'var(--st-text-base)' }}>
              Ask me anything about your fleet — I can read live truck data, alerts, scorecards, and routes.
            </p>
          </div>
        )}

        {messages.map((m, i) => {
          const isUser = m.role === 'user';
          // User messages stay as literal text (no HTML evaluation —
          // a driver typing "<script>" should see exactly that).
          // Model replies route through formatAIResponse() so the
          // backend's HTML/markdown is rendered properly instead of
          // showing raw "<p>" / "<b>" tags as plain text.
          const bubbleStyle = {
            maxWidth: '78%',
            padding: '8px 12px',
            borderRadius: 14,
            background: isUser
              ? 'var(--tgui--button_color, #2481cc)'
              : 'var(--tgui--secondary_bg_color, #2a2a2a)',
            color: isUser
              ? 'var(--tgui--button_text_color, #fff)'
              : 'var(--tgui--text_color, #fff)',
            whiteSpace: isUser ? ('pre-wrap' as const) : ('normal' as const),
            wordBreak: 'break-word' as const,
            fontSize: 'var(--st-text-base)',
            lineHeight: 1.4,
          };
          return (
            <div
              key={i}
              style={{
                display: 'flex',
                justifyContent: isUser ? 'flex-end' : 'flex-start',
                marginBottom: 8,
              }}
            >
              {isUser ? (
                <div style={bubbleStyle}>{m.text}</div>
              ) : (
                <div
                  className="ai-bubble"
                  style={bubbleStyle}
                  dangerouslySetInnerHTML={{ __html: formatAIResponse(m.text) }}
                />
              )}
            </div>
          );
        })}

        {sending && (
          <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 8 }}>
            <div
              style={{
                padding: '8px 12px', borderRadius: 14,
                background: 'var(--tgui--secondary_bg_color, #2a2a2a)',
                color: 'var(--tgui--hint_color, #999)', fontSize: 'var(--st-text-base)', fontStyle: 'italic',
              }}
            >
              Thinking…
            </div>
          </div>
        )}

        {/* Suggested questions chips (shown when input is empty + no in-flight reply) */}
        {!sending && suggestions.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 12 }}>
            {suggestions.slice(0, 4).map((q, i) => (
              <button
                key={i}
                type="button"
                onClick={() => send(q)}
                style={{
                  padding: '6px 10px', borderRadius: 12, border: 0, fontSize: 'var(--st-text-sm)',
                  background: 'var(--tgui--secondary_bg_color, #2a2a2a)',
                  color: 'var(--tgui--text_color, #fff)', cursor: 'pointer',
                }}
              >
                {q}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Composer */}
      <form
        onSubmit={(e) => { e.preventDefault(); send(input); }}
        style={{
          padding: '8px 12px',
          borderTop: '1px solid var(--tgui--divider, #2a2a2a)',
          display: 'flex', gap: 8, alignItems: 'flex-end',
          flexShrink: 0,
          paddingBottom: 'calc(8px + env(safe-area-inset-bottom, 0px))',
        }}
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={getDriverPlaceholder(userRole)}
          rows={1}
          disabled={sending}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send(input);
            }
          }}
          style={{
            flex: 1, minHeight: 36, maxHeight: 120, padding: '8px 12px',
            borderRadius: 18, border: '1px solid var(--tgui--divider, #444)',
            background: 'var(--tgui--secondary_bg_color, #1a1a1a)',
            color: 'var(--tgui--text_color, #fff)', fontSize: 'var(--st-text-lg)',
            resize: 'none', fontFamily: 'inherit', outline: 'none',
          }}
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          aria-label={t('ai.send')}
          style={{
            width: 40, height: 40, borderRadius: 20, border: 0,
            background: input.trim() && !sending
              ? 'var(--tgui--button_color, #2481cc)'
              : 'var(--tgui--secondary_bg_color, #2a2a2a)',
            color: 'var(--tgui--button_text_color, #fff)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            cursor: input.trim() && !sending ? 'pointer' : 'not-allowed',
            flexShrink: 0,
          }}
        >
          <Icon24SendOutline width={20} height={20} />
        </button>
      </form>

      <Snackbar
        open={snack !== null}
        kind={snack?.kind ?? 'info'}
        text={snack?.text ?? ''}
        onClose={() => setSnack(null)}
      />
    </div>
  );
});
