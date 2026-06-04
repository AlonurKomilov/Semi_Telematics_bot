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
  // The base Badge component hard-codes ``rounded-4xl`` (fixed
  // 32px) which ignores the user's Corners theme setting (Sharp /
  // Rounded / Pill).  Overriding to ``rounded-md`` here maps the
  // badge corners onto the ``--radius`` CSS variable so the Status
  // badge matches the rest of the themed surface — sharp goes sharp,
  // pill goes pill.
  // Snake-case statuses (``in_progress``, ``due_soon``) become two
  // words when rendered — otherwise the badge reads as a slug rather
  // than English ("due_soon" → "due soon").  Case is preserved as the
  // caller passed it so the existing lowercase styling carries through.
  const label = status.replace(/_/g, ' ');
  return (
    <Badge variant="outline" className={cn('text-xs font-medium rounded-md', classes)}>
      {label}
    </Badge>
  );
}
