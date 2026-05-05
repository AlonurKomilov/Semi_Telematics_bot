/**
 * useUnitSystem — reactive hook that returns the user's current unit
 * preference and re-renders when it changes.
 */

import { useEffect, useState } from 'react';
import { getUnitSystem, type UnitSystem } from '../utils/units';

export function useUnitSystem(): UnitSystem {
  const [units, setUnits] = useState<UnitSystem>(getUnitSystem);

  useEffect(() => {
    const onChange = () => setUnits(getUnitSystem());
    window.addEventListener('st-units-change', onChange);
    window.addEventListener('storage', onChange);
    return () => {
      window.removeEventListener('st-units-change', onChange);
      window.removeEventListener('storage', onChange);
    };
  }, []);

  return units;
}
