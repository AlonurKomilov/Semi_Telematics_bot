import { useTranslation } from 'react-i18next';
import { ClipboardList, Send, CheckCircle2, Circle, type LucideIcon } from 'lucide-react';
import type { PTIInspectionDetail } from '../../types';

/**
 * Status timeline rendered at the top of the inspection drawer.
 *
 * Three load-bearing transitions: Assigned → Submitted → Reviewed.
 * "In progress" isn't a separate stamp on the row (driver edits flip
 * the status on first-touch but we don't persist the touch time), so
 * folding it into the path between Assigned and Submitted keeps the
 * UI honest about what we actually know.
 *
 * For inspections still en-route to a milestone, the node renders
 * muted with a hollow circle.  Reached milestones show the wall-clock
 * timestamp and, for review, the reviewer's display name.
 */

function _formatTs(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString();
}

function _formatDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString();
}

interface NodeProps {
  reached: boolean;
  Icon: LucideIcon;
  label: string;
  detail: string;
  subtle?: string;
}

function TimelineNode({ reached, Icon, label, detail, subtle }: NodeProps) {
  const iconClass = reached
    ? 'text-primary'
    : 'text-muted-foreground/50';
  const labelClass = reached
    ? 'font-medium text-foreground'
    : 'text-muted-foreground';
  return (
    <div className="flex-1 min-w-0">
      <div className="flex items-center gap-1.5">
        {reached ? (
          <Icon size={14} className={iconClass} />
        ) : (
          <Circle size={14} className={iconClass} />
        )}
        <span className={`text-xs ${labelClass}`}>{label}</span>
      </div>
      {detail && (
        <p className="text-[11px] text-muted-foreground mt-0.5 truncate">
          {detail}
        </p>
      )}
      {subtle && (
        <p className="text-[11px] text-muted-foreground/80 truncate">
          {subtle}
        </p>
      )}
    </div>
  );
}

interface Props {
  inspection: PTIInspectionDetail;
}

export function StatusTimeline({ inspection: ins }: Props) {
  const { t } = useTranslation();

  const submitted = !!ins.submitted_at;
  const reviewed = ins.status === 'reviewed';
  const dueDate = ins.due_by ?? ins.scheduled_for;

  // Connector between nodes: filled if the right-hand milestone is
  // reached, otherwise muted.
  const connectorBase = 'h-px flex-shrink-0 self-center w-6';

  return (
    <div className="px-5 py-3 border-b border-border">
      <div className="flex items-start gap-1.5">
        <TimelineNode
          reached
          Icon={ClipboardList}
          label={t('inspections.timeline.assigned')}
          detail={_formatTs(ins.created_at)}
          subtle={!submitted && dueDate
            ? t('inspections.timeline.due', { date: _formatDate(dueDate) })
            : undefined}
        />
        <div className={`${connectorBase} ${submitted ? 'bg-primary/60' : 'bg-border'}`} />
        <TimelineNode
          reached={submitted}
          Icon={Send}
          label={t('inspections.timeline.submitted')}
          detail={submitted ? _formatTs(ins.submitted_at) : t('inspections.timeline.pending')}
          subtle={submitted && ins.driver_name
            ? t('inspections.timeline.by', { name: ins.driver_name })
            : undefined}
        />
        <div className={`${connectorBase} ${reviewed ? 'bg-primary/60' : 'bg-border'}`} />
        <TimelineNode
          reached={reviewed}
          Icon={CheckCircle2}
          label={t('inspections.timeline.reviewed')}
          detail={reviewed ? _formatTs(ins.reviewed_at) : t('inspections.timeline.pending')}
          subtle={reviewed && ins.reviewed_by_name
            ? t('inspections.timeline.by', { name: ins.reviewed_by_name })
            : undefined}
        />
      </div>
    </div>
  );
}
