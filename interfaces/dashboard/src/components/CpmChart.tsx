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
        <XAxis dataKey="name" tick={{ fill: 'var(--muted-foreground)', fontSize: 11 }} angle={-35} textAnchor="end" interval={0} />
        <YAxis tick={{ fill: 'var(--muted-foreground)', fontSize: 11 }} tickFormatter={(v) => `$${v.toFixed(2)}`} />
        <Tooltip
          formatter={(v) => [`$${(v as number).toFixed(3)}`, 'CPM']}
          contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', color: 'var(--foreground)' }}
        />
        {avgCpm != null && <ReferenceLine y={avgCpm} stroke="var(--muted-foreground)" strokeDasharray="4 4" label={{ value: 'avg', fill: 'var(--muted-foreground)', fontSize: 11 }} />}
        <Bar dataKey="cpm" radius={[4, 4, 0, 0]}>
          {top.map((v) => (
            // Higher cost-per-mile is worse → danger/warn/ok status tokens.
            <Cell key={v.vehicle_name} fill={v.cpm > 0.6 ? 'var(--danger)' : v.cpm > 0.4 ? 'var(--warn)' : 'var(--ok)'} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
