import { Sparkles, ArrowRight } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type { DashboardStats } from '../../types';
import { useShellConfig } from '../../hooks/useShellConfig';

interface AISummaryCardProps {
  stats: DashboardStats;
}

/**
 * Heuristic, deterministic narrative the user can read at a glance —
 * no LLM round-trip required. Clicking through opens the AI assistant
 * with a primed question so they can dig in.
 *
 * Copy adapts to the active persona view so dispatchers/safety/admins
 * don't read like the page is talking to a fleet manager.  An Owner
 * previewing as Safety gets the safety-flavored copy automatically.
 */
function buildNarrative(
  stats: DashboardStats,
  subjectAll: string,
  restingHint: string,
): string[] {
  const lines: string[] = [];
  // Role-neutral key with legacy-alias fallback (pre-rename API).
  const f = stats.vehicles ?? stats.fleet ?? {};
  // Percentages run against the TRUCK bucket — trailers can't move, and
  // counting them read a healthy fleet as "only 16% moving".
  const trucks = f.trucks ?? f;
  const total = trucks.total ?? 0;
  const moving = trucks.moving ?? 0;
  const stopped = trucks.stopped ?? 0;
  const idle = trucks.idle ?? 0;

  const movingPct = total > 0 ? Math.round((moving / total) * 100) : 0;

  if (total > 0) {
    if (movingPct >= 70) {
      lines.push(`${movingPct}% of ${subjectAll} are on the road right now — strong utilization.`);
    } else if (movingPct < 30) {
      lines.push(`Only ${movingPct}% of ${subjectAll} are moving — most are idle or stopped.`);
    } else {
      lines.push(`${moving} of ${total} ${subjectAll} moving · ${idle} idle · ${stopped} stopped.`);
    }
  }

  if ((stats.pending_alerts ?? 0) > 0) {
    lines.push(`${stats.pending_alerts} alert${(stats.pending_alerts ?? 0) > 1 ? 's' : ''} waiting on acknowledgement.`);
  }
  if ((stats.low_fuel ?? 0) > 0) {
    lines.push(`${stats.low_fuel} truck${(stats.low_fuel ?? 0) > 1 ? 's' : ''} running below 20% fuel — plan a fill-up.`);
  }
  if ((stats.faults ?? 0) > 0) {
    lines.push(`${stats.faults} vehicle${(stats.faults ?? 0) > 1 ? 's have' : ' has'} active diagnostic faults.`);
  }
  if ((stats.maintenance_due ?? 0) > 0) {
    lines.push(`${stats.maintenance_due} maintenance task${(stats.maintenance_due ?? 0) > 1 ? 's' : ''} ready to schedule.`);
  }
  const unsafe = (stats.unsafe_parking ?? 0) + (stats.unknown_parking ?? 0);
  if (unsafe > 0) {
    lines.push(`${unsafe} truck${unsafe > 1 ? 's are' : ' is'} parked outside a safe zone.`);
  }

  if (lines.length === 0) {
    lines.push(`Nothing critical is open. ${restingHint}`);
  }
  return lines;
}

export default function AISummaryCard({ stats }: AISummaryCardProps) {
  const navigate = useNavigate();
  const { aiSubjectAll, aiRestingHint } = useShellConfig();
  const narrative = buildNarrative(stats, aiSubjectAll, aiRestingHint);

  return (
    <div className="bg-gradient-to-br from-primary/5 via-card to-card border border-primary/20 rounded-xl p-5 mb-6">
      <div className="flex items-start gap-3">
        <span className="inline-flex items-center justify-center w-9 h-9 rounded-lg bg-primary/15 text-primary shrink-0">
          <Sparkles size={18} />
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2 mb-2">
            <p className="text-sm font-semibold text-foreground">Today's snapshot</p>
            <button
              onClick={() => navigate('/ai/chat?tab=briefing')}
              className="inline-flex items-center gap-1 text-xs text-primary hover:underline shrink-0 py-1 -my-1 min-h-tap"
            >
              Ask AI for more
              <ArrowRight size={12} />
            </button>
          </div>
          <ul className="space-y-1">
            {narrative.map((line, i) => (
              <li key={i} className="text-sm text-foreground/90 flex items-start gap-2">
                <span className="text-primary/60 mt-1.5">·</span>
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
