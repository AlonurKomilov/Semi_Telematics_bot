/**
 * The offer — a centred card over a blurred page: one idea, two
 * honest buttons.  Rides the Dialog primitive so focus trap, Escape,
 * aria-modal and scroll lock come from the one sanctioned backdrop
 * (whose overlay already carries backdrop-blur).
 *
 * Three ways out, three different verdicts: Show me runs the tour,
 * Skip is final (this person answered), Escape/outside-click merely
 * snoozes — closing a window is not answering a question.
 */
import { useTranslation } from 'react-i18next';
import { Lightbulb } from 'lucide-react';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { TourSpec } from './types';

export default function SpotlightIntro({
  tour,
  onShowMe,
  onSkip,
  onSnooze,
}: {
  tour: TourSpec;
  onShowMe: () => void;
  onSkip: () => void;
  onSnooze: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onSnooze(); }}>
      <DialogContent size="lg" showCloseButton={false}>
        <DialogHeader>
          <div className="flex items-center gap-2">
            <span className="flex size-8 items-center justify-center rounded-md bg-info-bg">
              <Lightbulb className="size-4.5 text-info" />
            </span>
            <DialogTitle>{t(`spotlight.${tour.key}.title`)}</DialogTitle>
          </div>
          <DialogDescription className="pt-1">
            {t(`spotlight.${tour.key}.body`)}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <button
            type="button"
            onClick={onSkip}
            className="inline-flex items-center rounded-md px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground transition min-h-tap"
          >
            {t('spotlight.labels.skip')}
          </button>
          <button
            type="button"
            // Dialog focuses the first focusable, which is Skip — and a
            // focus ring on Skip at open reads as EMPHASIS on skipping
            // (visible in the first live screenshot).  The offer's
            // primary action takes initial focus instead; Skip stays
            // one Tab (or Escape) away.
            autoFocus
            onClick={onShowMe}
            className="inline-flex items-center rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary-hover transition min-h-tap"
          >
            {t('spotlight.labels.show_me')}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
