import { describe, expect, it } from 'vitest';

import { FEATURE_CATALOG } from '../../../config/featureCatalog';
import en from '../../../locales/en.json';
import { familyText } from './components';

// The Board's TYPE_FAMILY map names each alert type's owning FEATURE as
// the feature catalog names it (English nav label — feature names stay
// English product vocabulary in all locales).  This guard keeps the
// mirror honest: the family shown on the Board must be the label of a
// real catalog feature, never a persona/role word like "Safety"
// (PERSONA.md — role words are not features).
const TYPE_TO_FEATURE_ID: Record<string, string> = {
  faults: 'vehicles',
  health: 'vehicles',
  fuel: 'vehicles',
  events: 'events',
  camera: 'cameras',
  parking: 'parking',
  geofence: 'geofences',
  scorecard: 'scorecards',
  maintenance: 'maintenance',
  documents: 'drivers',
};

function catalogLabel(featureId: string): string {
  const feature = FEATURE_CATALOG.find((f) => f.id === featureId);
  expect(feature, `feature "${featureId}" in catalog`).toBeDefined();
  const key = feature!.labelKey.replace(/^nav\./, '');
  const label = (en as { nav: Record<string, string> }).nav[key];
  expect(label, `en label for "${feature!.labelKey}"`).toBeTruthy();
  return label;
}

describe('familyText mirrors the feature catalog', () => {
  it('names every alert type after its owning catalog feature', () => {
    for (const [type, featureId] of Object.entries(TYPE_TO_FEATURE_ID)) {
      expect(familyText(type), `type "${type}"`).toBe(catalogLabel(featureId));
    }
  });

  it('keeps pipeline spelling variants in the same family', () => {
    expect(familyText('fault')).toBe(familyText('faults'));
    expect(familyText('event')).toBe(familyText('events'));
    expect(familyText('safety_events')).toBe(familyText('events'));
    expect(familyText('doc_expiry')).toBe(familyText('documents'));
  });

  it('homes owner housekeeping types on System, unknowns on Other', () => {
    expect(familyText('samsara_sync')).toBe('System');
    expect(familyText('reescalate')).toBe('System');
    expect(familyText('no_such_type')).toBe('Other');
  });
});
