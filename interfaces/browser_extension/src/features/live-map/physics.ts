/**
 * The dashboard Live Map's motion model, lifted out so it can be tested
 * and reused: a truck glides from its last fix to the new one over the
 * time between fixes, instead of teleporting every poll.
 */
import type { MapVehicleFeature, MapVehicleProperties, VehicleStatus } from './types';

export const MAP_STATUS = { ok: '#22c55e', warn: '#f59e0b', danger: '#ef4444' } as const;

export function shortestAngleDiff(from: number, to: number): number {
  let diff = ((to - from) % 360 + 360) % 360;
  if (diff > 180) diff -= 360;
  return diff;
}
export function bearingBetween(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLng = toRad(lng2 - lng1);
  const y = Math.sin(dLng) * Math.cos(toRad(lat2));
  const x = Math.cos(toRad(lat1)) * Math.sin(toRad(lat2))
          - Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(dLng);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}
export function distMetres(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const mPerDegLat = 111_111;
  const mPerDegLng = 111_111 * Math.cos(((lat1 + lat2) / 2) * (Math.PI / 180));
  const dy = (lat2 - lat1) * mPerDegLat, dx = (lng2 - lng1) * mPerDegLng;
  return Math.sqrt(dx * dx + dy * dy);
}
export function vehicleStatus(f: MapVehicleFeature): VehicleStatus {
  const p = f.properties;
  if (p.status === 'moving' || p.status === 'idle' || p.status === 'stopped') return p.status;
  if ((p.speed_mph || 0) > 0) return 'moving';
  if (p.engine_state === 'On' || p.engine_state === 'Idle') return 'idle';
  return 'stopped';
}
export function statusColor(s: VehicleStatus): string {
  return s === 'moving' ? MAP_STATUS.ok : s === 'idle' ? MAP_STATUS.warn : MAP_STATUS.danger;
}
export function hasLowLevelWarning(p: MapVehicleProperties): boolean {
  return (p.fuel_percent != null && p.fuel_percent < 15) || (p.def_percent != null && p.def_percent < 15);
}

export interface Phys {
  lat: number; lng: number;
  fromLat: number; fromLng: number; toLat: number; toLng: number;
  startMs: number; duration: number; lastFixMs: number;
  headingDeg: number; targetHeading: number;
  isMoving: boolean; engineState: string;
}

/** Fold one live fix into a vehicle's physics.  Returns the new state
 *  and whether it just started or just stopped moving — the two moments
 *  the icon has to change. */
export function applyFix(
  prev: Phys | undefined, lat: number, lng: number, speedMph: number,
  heading: number | null, nowMs: number,
): { phys: Phys; started: boolean; stopped: boolean } {
  const nowMoving = speedMph > 0;
  const h = heading ?? 0;
  if (!prev) {
    return {
      phys: { lat, lng, fromLat: lat, fromLng: lng, toLat: lat, toLng: lng, startMs: nowMs,
              duration: 0, lastFixMs: nowMs, headingDeg: h, targetHeading: h,
              isMoving: nowMoving, engineState: 'Off' },
      started: nowMoving, stopped: false,
    };
  }
  const wasMoving = prev.isMoving;
  const p = { ...prev, isMoving: nowMoving };
  if (nowMoving) {
    const moved = distMetres(p.toLat, p.toLng, lat, lng);
    p.targetHeading = moved > 2 ? bearingBetween(p.toLat, p.toLng, lat, lng) : h;
    p.fromLat = p.lat; p.fromLng = p.lng; p.toLat = lat; p.toLng = lng;
    p.startMs = nowMs;
    p.duration = Math.min(8000, Math.max(1500, nowMs - p.lastFixMs));
    p.lastFixMs = nowMs;
  } else {
    p.lat = p.fromLat = p.toLat = lat; p.lng = p.fromLng = p.toLng = lng;
    p.lastFixMs = nowMs;
  }
  return { phys: p, started: nowMoving && !wasMoving, stopped: !nowMoving && wasMoving };
}

/** Where the marker should be drawn at time ``ts`` — the tween. */
export function positionAt(p: Phys, ts: number): { lat: number; lng: number; done: boolean } {
  const t = p.duration > 0 ? Math.min(1, (ts - p.startMs) / p.duration) : 1;
  return { lat: p.fromLat + (p.toLat - p.fromLat) * t, lng: p.fromLng + (p.toLng - p.fromLng) * t, done: t >= 1 };
}
