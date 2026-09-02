import { describe, it, expect } from 'vitest';
import { directionsUrl, isGoogleMapsUrl, openInGoogleMaps, searchUrl } from './googleMaps';
import { setActiveTab, tabCalls } from '../../test/setup';

describe('Google hand-off — drive their page, never draw on it', () => {
  it('builds the documented URL API, not a scraped one', () => {
    expect(searchUrl(41.5, -87.4)).toBe('https://www.google.com/maps/search/?api=1&query=41.500000,-87.400000');
    expect(directionsUrl(41.5, -87.4)).toContain('/maps/dir/?api=1&destination=');
  });
  it('recognises a Google Maps tab on any Google domain', () => {
    expect(isGoogleMapsUrl('https://www.google.com/maps/@41,-87,12z')).toBe(true);
    expect(isGoogleMapsUrl('https://www.google.co.uk/maps')).toBe(true);
    expect(isGoogleMapsUrl('https://www.google.com/search?q=x')).toBe(false);
  });
  it('re-uses an open Google Maps tab so the user keeps their view', async () => {
    setActiveTab({ id: 7, url: 'https://www.google.com/maps/@1,1,5z' });
    await openInGoogleMaps(searchUrl(41, -87));
    expect(tabCalls.update).toHaveLength(1);
    expect(tabCalls.create).toHaveLength(0);
  });
  it('opens a new tab when the active one is not Google Maps', async () => {
    setActiveTab({ id: 3, url: 'https://mail.google.com/' });
    await openInGoogleMaps(searchUrl(41, -87));
    expect(tabCalls.create).toHaveLength(1);
    expect(tabCalls.update).toHaveLength(0);
  });
});
