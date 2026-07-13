/**
 * Built-in `chart` artifact — bar / line via recharts, coloured with the
 * app's `--chart-*` tokens (design.md: charts can't use classes, so
 * `chartColor(n)` is the sanctioned way a colour reaches the DOM).
 * Registered at module load via the artifacts barrel.
 */
import {
  ResponsiveContainer, BarChart, Bar, LineChart, Line,
  XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts';
import { chartColor } from '../../../lib/status';
import { registerArtifact } from './registry';
import { isChart, type Artifact } from './types';

function ChartArtifactView({ artifact }: { artifact: Artifact }) {
  if (!isChart(artifact)) return null;
  const { chart, data, xKey, series, title } = artifact;
  return (
    <div className="mt-2">
      {title && (
        <div className="mb-1 text-2xs font-medium text-muted-foreground">{title}</div>
      )}
      <ResponsiveContainer width="100%" height={220}>
        {chart === 'line' ? (
          <LineChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey={xKey} tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
            <YAxis tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
            <Tooltip />
            {series.length > 1 && <Legend />}
            {series.map((s, i) => (
              <Line key={s.key} type="monotone" dataKey={s.key} name={s.label ?? s.key}
                    stroke={chartColor(i + 1)} strokeWidth={2} dot={false} />
            ))}
          </LineChart>
        ) : (
          <BarChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey={xKey} tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
            <YAxis tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
            <Tooltip />
            {series.length > 1 && <Legend />}
            {series.map((s, i) => (
              <Bar key={s.key} dataKey={s.key} name={s.label ?? s.key} fill={chartColor(i + 1)} />
            ))}
          </BarChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}

registerArtifact('chart', (a) => <ChartArtifactView artifact={a} />);

export default ChartArtifactView;
