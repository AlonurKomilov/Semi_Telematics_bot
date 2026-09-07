/**
 * The lights this app ships — one `.css` file each, listed here with
 * the numbers the file has to agree with (`mods/shader.test.ts` holds
 * the two equal).
 *
 * Resource, not engine: `mods/shader.ts` defines what a light is and
 * the band it may move in. `flat` has no file on purpose — every
 * multiplier 1 is the shipped light, and the absence of a rule IS that.
 */
import type { ShaderPack } from '../../shader';

export const SHADER_PACKS: readonly ShaderPack[] = [
  { id: 'flat', label: 'Flat', description: 'The light this app was drawn in',
    lift: 1, spread: 1, strength: 1, elevate: 0 },
  { id: 'soft', label: 'Soft', description: 'A lower sun — longer shadows, softer edges',
    lift: 1.7, spread: 1.9, strength: 0.75, elevate: 1 },
  { id: 'studio', label: 'Studio', description: 'Overhead and close — short shadows, crisp edges',
    lift: 0.6, spread: 0.5, strength: 1.7, elevate: 1 },
];

export const SHADER_IDS = SHADER_PACKS.map((s) => s.id);

export const shaderPackById = (id: string): ShaderPack | undefined =>
  SHADER_PACKS.find((s) => s.id === id);
