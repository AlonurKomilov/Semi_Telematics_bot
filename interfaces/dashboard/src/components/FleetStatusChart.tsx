import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts';

interface Props {
  moving: number;
  idle: number;
  stopped: number;
}

export default function FleetStatusChart({ moving, idle, stopped }: Props) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <PieChart>
        <Pie
          data={[
            { name: 'Moving',  value: moving  || 0 },
            { name: 'Idle',    value: idle    || 0 },
            { name: 'Stopped', value: stopped || 0 },
          ]}
          cx="50%"
          cy="50%"
          innerRadius={55}
          outerRadius={85}
          paddingAngle={3}
          dataKey="value"
        >
          <Cell fill="#22c55e" />
          <Cell fill="#eab308" />
          <Cell fill="#ef4444" />
        </Pie>
        <Tooltip
          contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8, color: '#f9fafb' }}
        />
        <Legend iconType="circle" iconSize={10} />
      </PieChart>
    </ResponsiveContainer>
  );
}
