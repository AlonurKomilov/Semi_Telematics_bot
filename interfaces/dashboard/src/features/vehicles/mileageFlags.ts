/**
 * Coverage-flag vocabulary for the mileage surfaces — ONE source for
 * the grid's Coverage chips, the vehicle-detail section, and the trips
 * drawer, so a flag never wears different words on different screens.
 */
export type MileageFlag =
  | '' | 'partial' | 'reset' | 'catchup' | 'estimated' | 'device_change' | 'rebase';

export const FLAG_NOTE: Record<string, { label: string; tip: string }> = {
  device_change: {
    label: 'Device change',
    tip: 'This unit reported from more than one telematics device in the range. Miles are summed across them; the odometer columns show the device that drove most of them, so the span alone will not equal the total.',
  },
  estimated: {
    label: 'Estimated',
    tip: 'A range boundary fell inside a data gap — its odometer was interpolated from the readings around it.',
  },
  catchup: {
    label: 'Catch-up days',
    tip: 'The odometer feed went silent for some days and then reported the backlog in one reading — the range total is real, but if the range starts inside such a gap it can include some earlier driving.',
  },
  partial: {
    label: 'Partial',
    tip: 'Odometer history starts inside this range — real miles are at least the number shown.',
  },
  reset: {
    label: 'Odometer reset',
    tip: 'The odometer dropped mid-range (device swap or reset) — miles are summed from daily readings instead.',
  },
  rebase: {
    label: 'Odometer changed scale',
    tip: 'The odometer jumped upward mid-range by more than the truck could physically drive (its reading source changed) — miles are summed from daily readings instead.',
  },
};
