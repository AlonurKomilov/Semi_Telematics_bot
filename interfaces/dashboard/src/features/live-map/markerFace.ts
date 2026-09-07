/**
 * What a vehicle marker should look like, and when that has to be rebuilt.
 *
 * Two feeds describe the same truck at different ages: the thirty-second
 * list carries a status word and fuel levels, the five-second poll
 * carries motion.  Where they disagree the fresher one is right, and
 * saying so in one place is what keeps the map from arguing with itself
 * — the list used to stamp its own status back over the fast poll's,
 * redrawing a moving truck as parked once every thirty seconds for as
 * long as it drove.
 *
 * Kept out of LiveMap.tsx so both rules can be tested without a map, a
 * browser or a fleet.
 */
import { MAP_STATUS } from '../../config/mapColors';

/** The three words a vehicle's status can be.  Declared here, with the
 *  rules that read it, rather than in the page that renders it. */
export type VehicleStatus = 'moving' | 'idle' | 'stopped';

/** The status word the list gives, as a colour. */
export function statusColour(status: VehicleStatus): string {
  if (status === 'moving') return MAP_STATUS.ok;
  if (status === 'idle') return MAP_STATUS.warn;
  return MAP_STATUS.danger;
}

/** Motion, as the fast poll knows it.  Only the two fields that decide
 *  a face, so a caller need not hold a whole physics record. */
export interface MotionFacts {
  isMoving: boolean;
  /** The provider's own word, refreshed by the list: 'On' | 'Idle' | 'Off'. */
  engineState: string;
}

/**
 * The face a truck should be wearing right now: its colour, and whether
 * it is drawn as an arrow or a dot.
 *
 * With no motion known yet — a truck the fast poll has not reached — the
 * list is all there is, and it decides.
 */
export function faceOf(
  status: VehicleStatus, motion: MotionFacts | undefined,
): { colour: string; moving: boolean } {
  if (!motion) return { colour: statusColour(status), moving: status === 'moving' };
  if (motion.isMoving) return { colour: MAP_STATUS.ok, moving: true };
  const idling = motion.engineState === 'On' || motion.engineState === 'Idle';
  return { colour: idling ? MAP_STATUS.warn : MAP_STATUS.danger, moving: false };
}

/**
 * What an icon actually depends on — and what it does NOT.
 *
 * Leaflet's `setIcon` throws the marker's element away and builds a new
 * one.  The map called it for every truck on every thirty-second
 * refresh, so a hundred trucks meant a hundred elements destroyed and
 * rebuilt twice a minute for pictures that were, almost always,
 * identical.  Comparing this first turns that into "only the ones that
 * changed".
 *
 * Heading is deliberately absent.  A moving truck's arrow is turned in
 * place by writing one attribute on the polygon that is already there;
 * folding the heading in here would rebuild the element every frame,
 * which is the opposite of the point.
 */
export function iconSignature(colour: string, warn: boolean, moving: boolean): string {
  return moving ? `arrow:${colour}` : `dot:${colour}:${warn ? 'warn' : 'plain'}`;
}
