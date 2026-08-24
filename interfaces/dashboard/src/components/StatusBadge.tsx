import { Badge } from './ui/badge';
import { cn } from '../lib/utils';
import { statusClasses } from '../lib/status';

export default function StatusBadge({ status }: { status: string }) {
  // ``statusClasses`` funnels every domain status string (moving /
  // idle / pending / overdue / completed / etc.) through the shared
  // ``statusTone`` map → ``toneClasses`` recipe.  Adds entries (or
  // change a colour) in ``lib/status.ts`` — never invent a colour
  // here.  See dashboard/CLAUDE.md.
  const classes = statusClasses(status);
  // Corners come from the base Badge, which carries the themed
  // ``rounded-md`` (``--radius``) so Sharp / Rounded / Pill all reach
  // this badge.  It used to hard-code ``rounded-4xl`` — a step this
  // project's radius scale never defined, so the class compiled to
  // nothing and the corners silently fell back to square.
  // Snake-case statuses (``in_progress``, ``due_soon``) become two
  // words when rendered — otherwise the badge reads as a slug rather
  // than English ("due_soon" → "due soon").  Case is preserved as the
  // caller passed it so the existing lowercase styling carries through.
  const label = status.replace(/_/g, ' ');
  return (
    <Badge variant="outline" className={cn('text-xs font-medium', classes)}>
      {label}
    </Badge>
  );
}
