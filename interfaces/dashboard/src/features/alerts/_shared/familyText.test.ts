import { describe, expect, it } from 'vitest';

import { FEATURE_GROUPS } from '../../notifications/delivery/alertRoutingConstants';
import { familyText } from './components';

// The Board's TYPE_FAMILY map mirrors FEATURE_GROUPS (the delivery
// config's product taxonomy) plus the pipeline's spelling variants
// (fault/faults, doc_expiry, …).  This guard keeps the mirror honest:
// a type added to the routing taxonomy must get a Board family too,
// and the two must agree on the family name.
describe('familyText mirrors FEATURE_GROUPS', () => {
  it('covers every taxonomy type with the same family label', () => {
    for (const group of FEATURE_GROUPS) {
      for (const type of group.types) {
        expect(familyText(type), `type "${type}"`).toBe(group.label);
      }
    }
  });

  it('falls back to Other for unknown types', () => {
    expect(familyText('no_such_type')).toBe('Other');
  });
});
