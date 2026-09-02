import type L from 'leaflet';
import { MAP_STATUS } from './physics';

const HALO = '#fff', SHADOW = 'rgba(0,0,0,.45)';

/** The dashboard's markers: an arrow while moving, a dot when not. */
export function makeIcon(Leaf: typeof L, color: string, warn: boolean, speedMph: number, heading?: number | null) {
  if (speedMph > 0) {
    const size = 18, half = 9;
    const pts = `${half},2 ${size - 1},${size - 2} 1,${size - 2}`;
    const rot = typeof heading === 'number' ? heading : 0;
    return Leaf.divIcon({
      className: '', iconSize: [size, size], iconAnchor: [half, half],
      html: `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" `
          + `style="overflow:visible;filter:drop-shadow(0 1px 2px ${SHADOW})">`
          + `<polygon points="${pts}" fill="${color}" stroke="${HALO}" stroke-width="1.5" transform="rotate(${rot},${half},${half})"/></svg>`,
    });
  }
  const ring = warn ? `<div style="position:absolute;inset:-3px;border-radius:50%;border:2px solid ${MAP_STATUS.danger}"></div>` : '';
  return Leaf.divIcon({
    className: '', iconSize: [14, 14], iconAnchor: [7, 7],
    html: `<div style="position:relative;width:14px;height:14px">${ring}`
        + `<div style="position:absolute;inset:0;border-radius:50%;background:${color};border:2px solid ${HALO};box-shadow:0 1px 3px ${SHADOW}"></div></div>`,
  });
}
