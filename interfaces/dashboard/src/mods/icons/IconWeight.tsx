import { type ReactNode } from 'react';
import { LucideProvider } from 'lucide-react';
import { useMods } from '../context';
import { ICON_STROKE } from '../catalogue';

/**
 * Every icon's stroke weight, from one mount point.
 *
 * There are 1,663 icon usages across 225 files and none of them change:
 * lucide reads its defaults from a context that the installed version
 * already ships and nothing here was using. This is the whole feature.
 *
 * It is also the first property a MOD carries that the theme panel does
 * not offer, and that asymmetry is the point. A mod whose every setting
 * is also a chip is a shortcut, not a look — GX mods change things you
 * would never have thought to go and set.
 *
 * `absoluteStrokeWidth` is deliberately not enabled: it holds the stroke
 * at a fixed pixel width as the icon scales, which fights the Size axis.
 * Icons here are meant to grow with everything else.
 */
export function IconWeight({ children }: { children: ReactNode }) {
  const { theme } = useMods();
  return (
    <LucideProvider strokeWidth={ICON_STROKE[theme.icons] ?? ICON_STROKE.regular}>
      {children}
    </LucideProvider>
  );
}
