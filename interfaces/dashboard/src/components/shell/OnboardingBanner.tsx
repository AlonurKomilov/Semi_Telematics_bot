import { usePreference } from '../../preferences';
import { Link } from 'react-router-dom';
import {
  CheckCircle2,
  CircleDashed,
  X,
  Truck,
  Users,
  Trophy,
  MapPin,
  Sparkles,
} from 'lucide-react';
import { toneClasses, toneText } from '../../lib/status';

interface OnboardingStep {
  id: string;
  label: string;
  hint: string;
  href: string;
  done: boolean;
}

interface OnboardingBannerProps {
  vehicleCount: number;
  userCount: number;
  geofenceCount?: number;
  scorecardRulesCustomized?: boolean;
  onDismiss?: () => void;
}


export default function OnboardingBanner({
  vehicleCount,
  userCount,
  geofenceCount = 0,
  scorecardRulesCustomized = false,
  onDismiss,
}: OnboardingBannerProps) {
  // Dismissal is a per-user preference (synced — being re-taught the same
  // banner on a second browser is exactly what this prevents).
  const { value: hidden, setValue: setHidden } = usePreference('onboarding.dismissed');

  const steps: OnboardingStep[] = [
    {
      id: 'samsara',
      label: 'Connect Samsara',
      hint: vehicleCount > 0
        ? `${vehicleCount} vehicle${vehicleCount !== 1 ? 's' : ''} synced`
        : 'Add a company with your Samsara API key',
      href: '/companies',
      done: vehicleCount > 0,
    },
    {
      id: 'team',
      label: 'Invite your team',
      hint: userCount > 1
        ? `${userCount} member${userCount !== 1 ? 's' : ''}`
        : 'Drivers, dispatchers, safety managers',
      href: '/invites',
      done: userCount > 1,
    },
    {
      id: 'rules',
      label: 'Tune scorecard rules',
      hint: scorecardRulesCustomized
        ? 'Customised'
        : 'Defaults work — tune for your team',
      href: '/scorecard-rules',
      done: scorecardRulesCustomized,
    },
    {
      id: 'geofences',
      label: 'Set up geofences',
      hint: geofenceCount > 0
        ? `${geofenceCount} zone${geofenceCount !== 1 ? 's' : ''}`
        : 'Mark yards, fuel stops, customer sites',
      href: '/geofences',
      done: geofenceCount > 0,
    },
  ];

  const completed = steps.filter((s) => s.done).length;
  const allDone = completed === steps.length;

  if (hidden || allDone) return null;

  const dismiss = () => {
    try {
    } catch { /* ignore */ }
    setHidden(true);
    onDismiss?.();
  };

  const ICONS = [Truck, Users, Trophy, MapPin] as const;

  return (
    <div className="bg-gradient-to-br from-primary/10 via-card to-card border border-primary/30 rounded-xl p-5 mb-6">
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center justify-center w-9 h-9 rounded-lg bg-primary/15 text-primary">
            <Sparkles size={18} />
          </span>
          <div>
            <p className="text-sm font-semibold text-foreground">Welcome to 4truck</p>
            <p className="text-xs text-muted-foreground">
              {completed} of {steps.length} setup steps complete — finish the rest to unlock the full dashboard.
            </p>
          </div>
        </div>
        <button
          onClick={dismiss}
          aria-label="Dismiss onboarding"
          className="text-muted-foreground hover:text-foreground p-1"
        >
          <X size={16} />
        </button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {steps.map((s, i) => {
          const Icon = ICONS[i];
          return (
            <Link
              key={s.id}
              to={s.href}
              className={`flex items-start gap-2 p-3 rounded-lg border transition group ${
                s.done
                  ? `${toneClasses('ok')} text-muted-foreground`
                  : 'bg-card border-border hover:border-primary/40 hover:bg-card/80'
              }`}
            >
              <span
                className={`shrink-0 mt-0.5 ${s.done ? toneText('ok') : 'text-muted-foreground group-hover:text-primary'}`}
              >
                {s.done ? (
                  <CheckCircle2 size={16} />
                ) : (
                  <CircleDashed size={16} />
                )}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <Icon size={12} className="text-muted-foreground shrink-0" />
                  <p
                    className={`text-sm font-medium truncate ${s.done ? 'line-through' : 'text-foreground'}`}
                  >
                    {s.label}
                  </p>
                </div>
                <p className="text-xs text-muted-foreground mt-0.5 truncate">{s.hint}</p>
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
