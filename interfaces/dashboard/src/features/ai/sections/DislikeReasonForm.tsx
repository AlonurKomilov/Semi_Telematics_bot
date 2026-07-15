import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';
import { Tip } from '../../../components/tooltip';
import { apiJSON } from '../../../api/client';

// "Why was this bad?" follow-up that opens after a thumbs-down click.
// The thumbs-down POST already fired and flipped had_reask; this form
// is supplementary signal — captures WHICH failure mode the user hit
// so the operator dashboard can break down dislikes by category
// instead of seeing one undifferentiated "thumbs-down rate".
//
// Six reason categories + optional 500-char note.  Each reason maps
// to a different remediation pipeline server-side:
//   inaccurate     → review the tool result
//   off_topic      → review the prompt classifier / routing layer
//   incomplete     → review the scope (truck filter, vehicle list)
//   hallucinated   → strongest signal — model is making things up
//   vague          → model used too few tools or summarised too much
//   unjust_refusal → automatic candidate for the heuristic detector
//   other          → free-text only, no category

export type DislikeReason =
  | 'inaccurate'
  | 'off_topic'
  | 'incomplete'
  | 'hallucinated'
  | 'vague'
  | 'unjust_refusal'
  | 'other';

const REASON_KEYS: readonly DislikeReason[] = [
  'inaccurate',
  'off_topic',
  'incomplete',
  'hallucinated',
  'vague',
  'unjust_refusal',
] as const;

interface Props {
  /** Closes the panel without sending a reason — had_reask stays
   *  flipped from the initial click; the form just dismisses. */
  onSkip: () => void;
  /** Successful reason submission — parent collapses the form +
   *  remembers it was submitted so a second open doesn't duplicate. */
  onSubmitted: () => void;
}

export function DislikeReasonForm({ onSkip, onSubmitted }: Props) {
  const { t } = useTranslation();
  const [reason, setReason] = useState<DislikeReason | null>(null);
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    if (!reason && !note.trim()) {
      // Both empty — same as skipping.
      onSkip();
      return;
    }
    setSubmitting(true);
    try {
      await apiJSON('/ai/feedback/thumbs-down', {
        method: 'POST',
        body: {
          reason: reason ?? 'other',
          note: note.trim() || null,
        },
      });
      onSubmitted();
    } catch {
      // Network blip — still close so the user isn't stuck.  The
      // initial thumbs-down POST already landed; only the reason
      // is lost.
      onSubmitted();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mt-2 rounded-lg border border-border bg-muted/40 p-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-foreground/80">
          {t('chat.dislike_form.title')}
        </span>
        <Tip label={t('chat.dislike_form.close')}>
          <button
            onClick={onSkip}
            className="text-muted-foreground hover:text-foreground"
            aria-label={t('chat.dislike_form.close')}
          >
            <X size={14} />
          </button>
        </Tip>
      </div>

      <div className="flex flex-wrap gap-1.5 mb-2">
        {REASON_KEYS.map((key) => (
          <button
            key={key}
            onClick={() => setReason((cur) => (cur === key ? null : key))}
            className={`px-2.5 py-1 text-2xs rounded-md border transition-colors ${
              reason === key
                ? 'bg-primary text-primary-foreground border-primary'
                : 'bg-card text-foreground/80 border-border hover:border-ring'
            }`}
          >
            {t(`chat.dislike_form.reason.${key}`)}
          </button>
        ))}
      </div>

      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value.slice(0, 500))}
        placeholder={t('chat.dislike_form.note_placeholder')}
        rows={2}
        className="w-full text-xs resize-none rounded-md border border-border bg-card px-2.5 py-1.5 focus:border-ring focus:outline-none placeholder:text-muted-foreground"
      />

      <div className="flex items-center justify-end gap-2 mt-2">
        <button
          onClick={onSkip}
          disabled={submitting}
          className="text-2xs text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
        >
          {t('chat.dislike_form.skip')}
        </button>
        <button
          onClick={submit}
          disabled={submitting || (!reason && !note.trim())}
          className="px-3 py-1 text-xs rounded-md bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {submitting
            ? t('chat.dislike_form.sending')
            : t('chat.dislike_form.send')}
        </button>
      </div>
    </div>
  );
}
