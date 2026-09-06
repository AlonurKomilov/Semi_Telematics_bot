import { type ReactNode } from 'react';
import { IconPackProvider, ICON_WEIGHTS, type IconWeightName } from '../../lib/icons';
import { useMods } from '../context';

/**
 * Every icon's pack and weight, from one mount point.
 *
 * There are 1,663 icon usages across 225 files and none of them name a
 * pack or a weight: both arrive through the context `lib/icons` installs
 * here. That is the whole feature.
 *
 * Weight was the first property a MOD carried that the theme panel did
 * not offer, and that asymmetry was the point — a mod whose every
 * setting is also a chip is a shortcut, not a look. The pack is the
 * second half of the same axis: what the glyphs ARE, next to how
 * heavily they are drawn.
 *
 * `absoluteStrokeWidth` stays off: it holds lucide's stroke at a fixed
 * pixel width as the icon scales, which fights the Size axis. Icons
 * here grow with everything else.
 */
const isWeight = (v: unknown): v is IconWeightName =>
  ICON_WEIGHTS.includes(v as IconWeightName);

export function IconWeight({ children }: { children: ReactNode }) {
  const { theme } = useMods();
  return (
    <IconPackProvider
      pack={theme.iconPack ?? 'lucide'}
      weight={isWeight(theme.icons) ? theme.icons : 'regular'}
    >
      {children}
    </IconPackProvider>
  );
}
