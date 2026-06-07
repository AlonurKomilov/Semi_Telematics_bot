// Friendly display names for internal ai_usage.request_type labels.
// The backend strips ``*_turn`` observability rows from billing
// queries; this map turns the remaining raw action strings (which
// exist for routing/telemetry, not customers) into something
// customer-facing UIs can show without leaking implementation detail.
//
// Multiple internal labels can map to the same display name — see
// ``rollupByDisplayLabel`` for how those collapse into a single row.

export interface AiUsageByKey {
  requests: number;
  tokens: number;
}

export const AI_TYPE_LABELS: Record<string, string> = {
  question:            'AI Chat',
  chat:                'AI Chat',           // legacy action, pre-question rename
  summary:             'Fleet Briefing',
  diagnosis:           'Fault Diagnosis',
  bot_diagnosis:       'Fault Diagnosis (Bot)',
  proactive_diagnosis: 'Proactive Alert Analysis',
  vision:              'Camera Check',
  vision_text:         'Image Q&A',
  parking_analysis:    'Parking Safety',
};

/**
 * Convert an internal action label (e.g. "bot_diagnosis") to its
 * customer-facing display name.  Unknown labels — including ones
 * added by future code paths — get title-cased so "some_new_action"
 * shows as "Some New Action" instead of leaking raw snake_case.
 */
export function aiTypeLabel(raw: string): string {
  return AI_TYPE_LABELS[raw] ??
    raw.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

/**
 * Roll up by_type entries by their friendly display label, summing
 * requests + tokens.  Multiple internal labels collapse to a single
 * row when they share a display name (question + chat → "AI Chat";
 * diagnosis + bot_diagnosis → "Fault Diagnosis (Bot)" stays separate
 * because they have different display names).  Returns rows sorted
 * by request count desc, ready to render.
 */
export function rollupByDisplayLabel(
  byType: Record<string, AiUsageByKey>,
): Array<[string, AiUsageByKey]> {
  const merged: Record<string, AiUsageByKey> = {};
  for (const [raw, stats] of Object.entries(byType)) {
    const label = aiTypeLabel(raw);
    if (!merged[label]) merged[label] = { requests: 0, tokens: 0 };
    merged[label].requests += stats.requests;
    merged[label].tokens += stats.tokens;
  }
  return Object.entries(merged).sort((a, b) => b[1].requests - a[1].requests);
}
