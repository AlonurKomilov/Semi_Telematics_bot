// AI Assistant feature — type contracts.
//
// Owns every AI-related shape on the frontend: chat messages, tier
// choices, model metadata, usage rows.  Other modules (Billing,
// Settings, system_dashboard) import from here — not from the global
// types/index.ts — so the AI feature stays the single source of truth
// for its own data.
//
// types/index.ts re-exports these symbols for backwards-compat with
// older import paths (`from '../../types'`); new code should import
// directly from this file.

export interface AIUsage {
  prompt_tokens: number;
  reply_tokens: number;
  total_tokens: number;
  thinking_tokens?: number;
}

export interface AIChatMessage {
  role: 'user' | 'model';
  text: string;
  /** Client-side timestamp — not persisted to backend */
  timestamp?: Date;
  /**
   * ISO timestamp from the backend (``created_at``).  Present only
   * on messages loaded from history — the dashboard parses this into
   * the live ``timestamp`` Date so a 3-day-old reply doesn't render
   * with the browser's current time.
   */
  ts?: string;
  /** Token usage from the backend — only present on model messages */
  usage?: AIUsage;
}

export interface AIChatResponse {
  reply: string;
  suggestions: string[];
  usage?: AIUsage;
}

export interface AISummaryResponse {
  summary: string;
  suggestions: string[];
  usage?: AIUsage;
}

export interface AIDiagnoseResponse {
  diagnosis: string;
  vehicle: string;
}

/** A real tier — one of the three model groups the router routes
 *  inside.  Distinct from ``AITierChoice`` because a specific model
 *  can only belong to one of these three (not to "auto"). */
export type AITier = 'fast' | 'thinking' | 'reasoning';

/** The set of values the user can pick + persist as their tier
 *  preference.  ``"auto"`` resolves to one of the three real tiers
 *  at request time from the prompt's classified category. */
export type AITierChoice = AITier | 'auto';

export interface AIModel {
  name: string;
  display: string;
  description: string;
  category: string;
  /**
   * Upstream maker — "Google", "Anthropic", "OpenAI", "Meta",
   * "DeepSeek", "Alibaba", "Moonshot", "Mistral", "xAI", "MiniMax",
   * "Zhipu", or "Other".  Used by the model picker to group entries
   * by who built them.
   */
  maker: string;
  /** Tier this model belongs to.  Null for vision-only models. */
  tier: AITier | null;
  vision: boolean;
  cost_per_request: number | null;
}

export interface AIModelsResponse {
  models: AIModel[];
  current_text: string;
  current_vision: string;
  account_default: string;
  is_admin: boolean;
}

export interface AITierOption {
  name: AITierChoice;
  /** Display label ("Fast", "Thinking", "Reasoning", "Auto"). */
  label: string;
  /** Emoji icon picked server-side so adding a tier doesn't require a frontend change. */
  icon: string;
  description: string;
  /** Number of models in the tier's fallback chain.  ``0`` for ``"auto"``
   *  since auto doesn't have its own chain — it routes to one of the
   *  three real tiers per request. */
  model_count: number;
}

export interface AITierResponse {
  current_tier: AITierChoice;
  /** Null when current_tier is ``"auto"`` — the model only gets picked
   *  at request time from the prompt category. */
  current_model: string | null;
  current_model_display: string | null;
  tiers: AITierOption[];
}

export interface AITierSwitchResponse {
  ok: boolean;
  tier: AITierChoice;
  /** Null when switching to ``"auto"`` (no model is pre-cached). */
  resolved_model: string | null;
  resolved_model_display: string | null;
}

export interface AIHistoryResponse {
  messages: AIChatMessage[];
  count: number;
}
