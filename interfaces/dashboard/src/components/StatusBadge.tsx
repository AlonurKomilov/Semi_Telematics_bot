interface StatusBadgeProps {
  status: string;
}

export default function StatusBadge({ status }: StatusBadgeProps) {
  const map: Record<string, string> = {
    moving:   'bg-green-500/20 text-green-400',
    idle:     'bg-yellow-500/20 text-yellow-400',
    stopped:  'bg-red-500/20 text-red-400',
    Off:      'bg-gray-500/20 text-gray-400',
    On:       'bg-green-500/20 text-green-400',
    Idle:     'bg-yellow-500/20 text-yellow-400',
    active:   'bg-green-500/20 text-green-400',
    inactive: 'bg-red-500/20 text-red-400',
  };
  const cls = map[status] || 'bg-gray-500/20 text-gray-400';
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>
      {status}
    </span>
  );
}
