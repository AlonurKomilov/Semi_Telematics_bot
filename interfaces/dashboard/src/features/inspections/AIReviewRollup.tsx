import { useTranslation } from 'react-i18next';
import { Sparkles } from 'lucide-react';
import type { PTIInspectionDetail, PTIInspectionItem } from '../../types';
import { toneClasses } from '../../lib/status';
import { parseVerdict, isFlagged, type AIVerdict } from './aiVerdict';

/**
 * AI review roll-up banner for the inspection detail drawer.
 *
 * Aggregates the per-photo verdicts the driver's Mini App captured at
 * inspection time and surfaces the signal a fleet reviewer most needs:
 *
 *   1. How many photos the AI checked, how many it flagged.
 *   2. The highest-value disagreement — a photo the AI flagged on an
 *      item the driver marked OK ("driver said OK, AI sees damage").
 *
 * Renders nothing when no photo on the inspection has an AI verdict
 * (AI disabled, or a pre-feature inspection), so the drawer stays
 * clean for accounts not using the feature.
 */

interface Props {
  inspection: PTIInspectionDetail;
}

const OK_ITEM_STATUSES = new Set(['ok', 'na', 'pending']);

export function AIReviewRollup({ inspection: ins }: Props) {
  const { t } = useTranslation();
  const media = ins.media ?? [];
  const items: PTIInspectionItem[] = ins.items ?? [];

  // Collect verdicts + remember which item each came from.
  const reviewed: Array<{ verdict: AIVerdict; itemId: number | null }> = [];
  for (const m of media) {
    const v = parseVerdict(m);
    if (v) reviewed.push({ verdict: v, itemId: m.item_id });
  }
  if (reviewed.length === 0) return null;

  const flaggedCount = reviewed.filter(r => isFlagged(r.verdict)).length;

  // Disagreements: AI flagged a photo on an item the driver marked OK.
  const itemById = new Map(items.map(it => [it.id, it]));
  const disagreements: Array<{ label: string; summary: string }> = [];
  for (const r of reviewed) {
    if (!isFlagged(r.verdict)) continue;
    const it = r.itemId != null ? itemById.get(r.itemId) : undefined;
    if (it && OK_ITEM_STATUSES.has((it.status || '').toLowerCase())) {
      disagreements.push({
        label: it.label,
        summary: r.verdict.summary || '',
      });
    }
  }

  // Banner surface only (bg + border) — the heading/body keep the
  // default foreground, so we reach for the tone's fill/border tokens
  // directly rather than the full text-tinting soft-pill recipe.
  const tone = flaggedCount > 0
    ? 'border-warn-bd bg-warn-bg'
    : 'border-ok-bd bg-ok-bg';

  return (
    <div className={`mx-5 mt-4 rounded-lg border p-3 ${tone}`}>
      <div className="flex items-center gap-2 mb-1">
        <Sparkles size={16} className="text-primary" />
        <span className="text-sm font-semibold">{t('inspections.ai.title')}</span>
      </div>
      <p className="text-xs text-muted-foreground">
        {t('inspections.ai.summary', {
          checked: reviewed.length,
          flagged: flaggedCount,
        })}
      </p>

      {disagreements.length > 0 && (
        <div className="mt-2 space-y-1.5">
          {disagreements.map((d, i) => (
            <div
              key={i}
              className={`text-xs rounded border px-2 py-1.5 ${toneClasses('danger')}`}
            >
              <span className="font-medium">
                {t('inspections.ai.disagreement', { item: d.label })}
              </span>
              {d.summary && <span className="block opacity-90 mt-0.5">{d.summary}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
