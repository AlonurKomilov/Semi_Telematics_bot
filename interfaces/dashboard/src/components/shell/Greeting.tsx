import { useTimezone } from '../../hooks/useTimezone';

interface GreetingProps {
  name?: string;
  context?: string;
  className?: string;
}

function timeOfDay(tz: string): string {
  // The user's effective-timezone hour, not the browser's — a driver
  // whose laptop clock is elsewhere still gets greeted by THEIR morning.
  let h: number;
  try {
    h = Number(new Intl.DateTimeFormat('en-US', {
      hour: 'numeric', hour12: false, timeZone: tz,
    }).format(new Date()));
  } catch {
    h = new Date().getHours();
  }
  if (h < 5) return 'Working late';
  if (h < 12) return 'Good morning';
  if (h < 17) return 'Good afternoon';
  if (h < 21) return 'Good evening';
  return 'Working late';
}

export default function Greeting({ name, context, className = '' }: GreetingProps) {
  const tz = useTimezone();
  const first = (name || '').trim().split(/\s+/)[0] || '';
  return (
    <div className={`mb-2 ${className}`}>
      <p className="text-base font-medium text-foreground">
        {timeOfDay(tz)}{first ? `, ${first}` : ''}
      </p>
      {context && (
        <p className="text-xs text-muted-foreground mt-0.5">{context}</p>
      )}
    </div>
  );
}
