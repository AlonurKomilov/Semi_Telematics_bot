import type { CSSProperties } from 'react';

export interface PillarValue {
  /** Current subtotal points. */
  subtotal: number;
  /** Maximum points possible for this pillar (the cap). */
  cap: number;
  /** False when the rules engine had no signal — render the arc dim. */
  has_data: boolean;
}

export interface PillarRingProps {
  safety: PillarValue;
  efficiency: PillarValue;
  compliance: PillarValue;
  /** Composite total (rendered in the centre). */
  total: number;
  /** CSS colour string for the centre score — drives grade colouring. */
  tierColor?: string;
  /** Outer SVG side length in CSS pixels. Default 220. */
  size?: number;
  /** Stroke width per arc. Default 14. */
  stroke?: number;
}

interface ArcProps {
  cx: number;
  cy: number;
  r: number;
  stroke: number;
  pct: number;
  color: string;
  dim: boolean;
  label: string;
}

function Arc({ cx, cy, r, stroke, pct, color, dim, label }: ArcProps) {
  const c = 2 * Math.PI * r;
  const filled = c * Math.max(0, Math.min(1, pct));
  return (
    <g aria-label={label}>
      {/* Track */}
      <circle
        cx={cx}
        cy={cy}
        r={r}
        fill="none"
        stroke="var(--tg-theme-secondary-bg-color, rgba(255,255,255,0.08))"
        strokeWidth={stroke}
      />
      {/* Filled portion */}
      <circle
        cx={cx}
        cy={cy}
        r={r}
        fill="none"
        stroke={color}
        strokeWidth={stroke}
        strokeDasharray={`${filled} ${c - filled}`}
        strokeDashoffset={c / 4}
        strokeLinecap="round"
        transform={`rotate(-90 ${cx} ${cy})`}
        opacity={dim ? 0.35 : 1}
        style={{ transition: 'stroke-dasharray 0.5s ease' }}
      />
    </g>
  );
}

/**
 * Concentric scorecard ring: outer = safety, middle = efficiency,
 * inner = compliance.  Each arc fills proportional to ``subtotal/cap``.
 * Pillars with ``has_data === false`` render dimmed so the user
 * understands why the arc looks short.
 *
 * A colour legend row below the ring labels each pillar with its colour
 * and a "No data" note when has_data is false (finding #1, #18).
 */
export function PillarRing({
  safety,
  efficiency,
  compliance,
  total,
  tierColor,
  size = 220,
  stroke = 14,
}: PillarRingProps) {
  const cx = size / 2;
  const cy = size / 2;
  // Three concentric arcs, each separated by stroke + 4px gap.
  const rOuter = size / 2 - stroke / 2 - 2;
  const rMid   = rOuter - stroke - 4;
  const rInner = rMid   - stroke - 4;

  const pct = (p: PillarValue) => (p.cap > 0 ? p.subtotal / p.cap : 0);

  const labelStyle: CSSProperties = {
    fill: 'var(--tg-theme-text-color, currentColor)',
    fontSize: 14,
    fontWeight: 500,
    textAnchor: 'middle',
  };
  const totalStyle: CSSProperties = {
    fill: tierColor ?? 'var(--tg-theme-text-color, currentColor)',
    fontSize: 44,
    fontWeight: 700,
    textAnchor: 'middle',
  };

  // Pillar arc colours match the dashboard's PILLAR_META so the
  // same driver sees consistent colour cues on web and phone.
  const SAFETY_COLOR     = '#ef4444';  // red
  const EFFICIENCY_COLOR = '#22c55e';  // green
  const COMPLIANCE_COLOR = '#3b82f6';  // blue

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={`Composite score ${total} out of 100`}
        className="pillar-ring"
      >
        {/* Outer ring — Safety */}
        <Arc cx={cx} cy={cy} r={rOuter} stroke={stroke}
          pct={pct(safety)} color={SAFETY_COLOR} dim={!safety.has_data}
          label={`Safety ${safety.subtotal} of ${safety.cap}`} />
        {/* Middle — Efficiency */}
        <Arc cx={cx} cy={cy} r={rMid} stroke={stroke}
          pct={pct(efficiency)} color={EFFICIENCY_COLOR} dim={!efficiency.has_data}
          label={`Efficiency ${efficiency.subtotal} of ${efficiency.cap}`} />
        {/* Inner — Compliance */}
        <Arc cx={cx} cy={cy} r={rInner} stroke={stroke}
          pct={pct(compliance)} color={COMPLIANCE_COLOR} dim={!compliance.has_data}
          label={`Compliance ${compliance.subtotal} of ${compliance.cap}`} />
        {/* Centre score */}
        <text x={cx} y={cy + 6} style={totalStyle}>{total}</text>
        <text x={cx} y={cy + 30} style={labelStyle}>/ 100</text>
      </svg>

      {/* Colour legend (finding #1, #18) */}
      <div className="pillar-legend">
        {([
          { label: 'Safety',     color: SAFETY_COLOR,     p: safety },
          { label: 'Efficiency', color: EFFICIENCY_COLOR, p: efficiency },
          { label: 'Compliance', color: COMPLIANCE_COLOR, p: compliance },
        ] as const).map(({ label, color, p }) => (
          <div key={label} className="pillar-legend__item" style={{ opacity: p.has_data ? 1 : 0.45 }}>
            <span className="pillar-legend__dot" style={{ background: color }} />
            <span className="pillar-legend__name">{label}</span>
            {!p.has_data && <span className="pillar-legend__nodata">No data</span>}
          </div>
        ))}
      </div>
    </div>
  );
}
