import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from 'recharts';

interface Vehicle {
  vehicle_name: string;
  cpm: number;
}

interface Props {
  vehicles: Vehicle[];
  avgCpm: number | null;
}

export default function CpmChart({ vehicles, avgCpm }: Props) {
  const top = vehicles.slice(0, 15);
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart
        data={top.map((v) => ({ name: v.vehicle_name, cpm: v.cpm }))}
        margin={{ top: 4, right: 20, left: 0, bottom: 40 }}
      >
        <XAxis dataKey="name" tick={{ fill: '#9ca3af', fontSize: 11 }} angle={-35} textAnchor="end" interval={0} />
        <YAxis tick={{ fill: '#6b7280', fontSize: 11 }} tickFormatter={(v) => `$${v.toFixed(2)}`} />
        <Tooltip
          formatter={(v) => [`$${(v as number).toFixed(3)}`, 'CPM']}
          contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', color: 'var(--foreground)' }}
        />
        {avgCpm != null && <ReferenceLine y={avgCpm} stroke="#6b7280" strokeDasharray="4 4" label={{ value: 'avg', fill: '#6b7280', fontSize: 11 }} />}
        <Bar dataKey="cpm" radius={[4, 4, 0, 0]}>
          {top.map((v) => (
            <Cell key={v.vehicle_name} fill={v.cpm > 0.6 ? '#ef4444' : v.cpm > 0.4 ? '#eab308' : '#22c55e'} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
