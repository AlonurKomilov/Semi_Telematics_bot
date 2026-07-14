import type { PoiIconSpec } from '@/config/poiLayers';

/**
 * Renders a POI layer icon in React UI (panel rows, group headers,
 * nearest-POI lists): built-in layers carry a lucide component
 * (token/`color`-coloured like every other icon); custom layers carry
 * the user-picked emoji string from the DB (user content — the one
 * sanctioned non-lucide case, see PoiIconSpec).
 *
 * Map markers/cluster bubbles can't take React components — they use
 * `poiIconHtml()` in usePoiLayers for the same dispatch as raw SVG.
 */
export default function PoiIcon({ icon, size, className, color, onClick }: {
  icon: PoiIconSpec;
  size: 12 | 14 | 16 | 18 | 20 | 24;
  className?: string;
  /** Literal map colour (from config) — used in map-legend contexts. */
  color?: string;
  onClick?: () => void;
}) {
  if (typeof icon === 'string') {
    return (
      <span
        className={className}
        style={{ fontSize: size, lineHeight: 1 }}
        onClick={onClick}
      >
        {icon}
      </span>
    );
  }
  const Icon = icon;
  return (
    <Icon
      size={size}
      className={className}
      style={color ? { color } : undefined}
      onClick={onClick}
    />
  );
}
