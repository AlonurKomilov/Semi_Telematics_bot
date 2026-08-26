/**
 * A recharts bar whose corners follow the Corners setting AND stay
 * readable.
 *
 * Pass it as `shape`, never as `radius`. The `radius` prop is one static
 * tuple for the whole series, so it cannot know how thick any individual
 * bar turned out — and a 6px bar with a 16px corner is a lozenge, not a
 * rounded bar. `shape` is handed the computed rect, which is the only
 * place the clamp can be applied honestly.
 *
 * A CSS string is not an option here: recharts' `getRectanglePath`
 * branches on `radius === +radius`, so `'var(--radius)'` draws square
 * corners and says nothing.
 */
import { Rectangle } from 'recharts';
import { clampRadius } from '@/lib/radius';

type Corners = 'top' | 'right' | 'all';

export function RoundedBar(
  props: { corners?: Corners; radiusPx?: number } & Record<string, unknown>,
) {
  const { corners = 'top', radiusPx = 10, ...rest } = props;
  const t = clampRadius(radiusPx, Number(rest.width) || 0, Number(rest.height) || 0);
  const radius: [number, number, number, number] =
    corners === 'top' ? [t, t, 0, 0]
      : corners === 'right' ? [0, t, t, 0]
        : [t, t, t, t];
  return <Rectangle {...rest} radius={radius} />;
}
