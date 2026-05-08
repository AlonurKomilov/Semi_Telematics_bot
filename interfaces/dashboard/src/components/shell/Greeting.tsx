interface GreetingProps {
  name?: string;
  context?: string;
  className?: string;
}

function timeOfDay(): string {
  const h = new Date().getHours();
  if (h < 5) return 'Working late';
  if (h < 12) return 'Good morning';
  if (h < 17) return 'Good afternoon';
  if (h < 21) return 'Good evening';
  return 'Working late';
}

export default function Greeting({ name, context, className = '' }: GreetingProps) {
  const first = (name || '').trim().split(/\s+/)[0] || '';
  return (
    <div className={`mb-2 ${className}`}>
      <p className="text-base font-medium text-foreground">
        {timeOfDay()}{first ? `, ${first}` : ''}
      </p>
      {context && (
        <p className="text-xs text-muted-foreground mt-0.5">{context}</p>
      )}
    </div>
  );
}
