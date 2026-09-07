import type { Mod } from '../../catalogue';

/**
 * Wall — a display read from across the room. The largest scale the
 * slider offers, bold glyphs, and the page entrance on: on a screen
 * nobody is clicking, a route that slides in is how a person twenty feet
 * away notices it changed.
 */
export const wall: Mod = {
  id: 'wall', label: 'Wall', accent: 'blue',  radius: 'rounded', size: 1.45,
  icons: 'bold', entrance: true,
  description: 'A display read from across the room'
};
