/**
 * The projection, checked against a camera taken off a real Google
 * Maps tab (the I-40 view near Clarksville, Arkansas) rather than
 * against itself.
 */
import { describe, it, expect } from 'vitest';

import { LABEL_MIN_ZOOM, MAX_LAT, cameraDrawable, cameraFromUrl, isStreetView, isVisible, latToWorldY, lngToWorldX, project, sameCamera, showsLabels, worldSize } from './projection';

describe('Web Mercator, the arithmetic every slippy map shares', () => {
  it('puts null island in the middle of the world at every zoom', () => {
    for (const z of [0, 5, 12, 21]) {
      expect(lngToWorldX(0, z)).toBeCloseTo(worldSize(z) / 2, 6);
      expect(latToWorldY(0, z)).toBeCloseTo(worldSize(z) / 2, 6);
    }
  });

  it('spans the globe in 256 pixels at zoom 0', () => {
    expect(worldSize(0)).toBe(256);
    expect(lngToWorldX(-180, 0)).toBeCloseTo(0, 6);
    expect(lngToWorldX(180, 0)).toBeCloseTo(256, 6);
  });

  it('clamps the poles instead of returning infinity', () => {
    // One NaN in a transform hides EVERY marker on the layer, not just
    // the bad one — so a pole must produce a number.
    for (const lat of [90, -90, 89.9, -89.9]) {
      expect(Number.isFinite(latToWorldY(lat, 12))).toBe(true);
    }
    expect(latToWorldY(90, 12)).toBeCloseTo(latToWorldY(MAX_LAT, 12), 6);
  });

  it('is north-up: a higher latitude is a smaller y', () => {
    expect(latToWorldY(40, 12)).toBeLessThan(latToWorldY(35, 12));
  });
});

describe('placing a vehicle on somebody else\'s map', () => {
  // The camera in the screenshot the owner sent.
  const camera = { lat: 35.500878, lng: -93.724251, zoom: 12 };
  const viewport = { width: 1000, height: 800 };

  it('puts the camera coordinate dead centre', () => {
    const p = project({ lat: camera.lat, lng: camera.lng }, camera, viewport);
    expect(p.x).toBeCloseTo(500, 6);
    expect(p.y).toBeCloseTo(400, 6);
  });

  it('puts a point east and north up and to the right', () => {
    const p = project({ lat: 35.55, lng: -93.65 }, camera, viewport);
    expect(p.x).toBeGreaterThan(500);
    expect(p.y).toBeLessThan(400);
  });

  it('scales with zoom — one zoom step doubles the offset', () => {
    const near = project({ lat: 35.55, lng: -93.65 }, camera, viewport);
    const zoomed = project({ lat: 35.55, lng: -93.65 }, { ...camera, zoom: 13 }, viewport);
    expect(zoomed.x - 500).toBeCloseTo((near.x - 500) * 2, 4);
    expect(zoomed.y - 400).toBeCloseTo((near.y - 400) * 2, 4);
  });

  it('a degree of longitude at zoom 12 is the width Mercator says it is', () => {
    // 360° spans 256·2^12 px, so 1° is that / 360.
    const oneDegree = worldSize(12) / 360;
    const p = project({ lat: camera.lat, lng: camera.lng + 1 }, camera, viewport);
    expect(p.x - 500).toBeCloseTo(oneDegree, 4);
  });
});

describe('what is worth drawing', () => {
  const viewport = { width: 1000, height: 800 };
  it('keeps a marker whose icon overhangs the edge', () => {
    expect(isVisible({ x: -20, y: 400 }, viewport)).toBe(true);
    expect(isVisible({ x: 1010, y: 400 }, viewport)).toBe(true);
  });
  it('drops one that is properly off screen', () => {
    expect(isVisible({ x: -500, y: 400 }, viewport)).toBe(false);
    expect(isVisible({ x: 500, y: 2000 }, viewport)).toBe(false);
  });
});

describe('reading the camera out of Google\'s own URL', () => {
  it('reads a plain map view', () => {
    expect(cameraFromUrl('https://www.google.com/maps/@35.500878,-93.724251,12z'))
      .toEqual({ lat: 35.500878, lng: -93.724251, zoom: 12 });
  });

  it('reads it out of a search or a directions view too', () => {
    expect(cameraFromUrl('https://www.google.com/maps/search/truck+stop/@35.5,-93.7,9.5z/data=!3m1')?.zoom)
      .toBe(9.5);
    expect(cameraFromUrl('https://www.google.com/maps/dir/A/B/@40.1,-74.2,7z')?.lat).toBe(40.1);
  });

  it('returns null before Google has written a camera', () => {
    expect(cameraFromUrl('https://www.google.com/maps')).toBeNull();
    expect(cameraFromUrl('https://www.google.com/maps/search/?api=1&query=35.5,-93.7')).toBeNull();
  });

  it('refuses coordinates outside the world rather than drawing nonsense', () => {
    expect(cameraFromUrl('https://www.google.com/maps/@99,-93.7,12z')).toBeNull();
    expect(cameraFromUrl('https://www.google.com/maps/@35.5,-200,12z')).toBeNull();
    expect(cameraFromUrl('https://www.google.com/maps/@35.5,-93.7,99z')).toBeNull();
  });

  it('knows Street View is a photograph, not a map', () => {
    const sv = 'https://www.google.com/maps/@35.5,-93.7,3a,75y,180h,90t/data=!3m6';
    expect(isStreetView(sv)).toBe(true);
    expect(isStreetView('https://www.google.com/maps/@35.5,-93.7,12z')).toBe(false);
  });
});

describe('when a redraw is worth doing', () => {
  const a = { lat: 35.5, lng: -93.7, zoom: 12 };
  it('ignores a rounding change Google writes on its own timer', () => {
    expect(sameCamera(a, { ...a, lat: a.lat + 1e-9 })).toBe(true);
  });
  it('notices a real pan or a zoom step', () => {
    expect(sameCamera(a, { ...a, lat: 35.6 })).toBe(false);
    expect(sameCamera(a, { ...a, zoom: 13 })).toBe(false);
  });
  it('treats absence as a change worth acting on', () => {
    expect(sameCamera(a, null)).toBe(false);
    expect(sameCamera(null, null)).toBe(true);
  });
});

describe('a name beside every truck stops being information', () => {
  it('drops the labels on a national view, where a hundred pills overlap into a block', () => {
    expect(showsLabels(5)).toBe(false);
    expect(showsLabels(LABEL_MIN_ZOOM - 0.5)).toBe(false);
  });
  it('brings them back once there is room', () => {
    expect(showsLabels(LABEL_MIN_ZOOM)).toBe(true);
    expect(showsLabels(14)).toBe(true);
  });
});

describe('cameraFromUrl — the satellite form', () => {
  // The owner's own screenshot: satellite view of the eastern US, a
  // 900px-tall map, 1966123 metres of ground in view at latitude 37.
  const url = 'https://www.google.com/maps/@37.3283564,-86.6378989,1966123m/data=!3m1!1e3';

  it('turns metres of visible ground into a zoom, given the viewport height', () => {
    const cam = cameraFromUrl(url, 900);
    expect(cam).not.toBeNull();
    expect(cam!.lat).toBeCloseTo(37.3283564, 6);
    expect(cam!.lng).toBeCloseTo(-86.6378989, 6);
    // log2(156543.03 · cos(37.33°) · 900 / 1966123) ≈ 5.83 — the whole
    // eastern seaboard, which is what the screenshot shows.
    expect(cam!.zoom).toBeGreaterThan(5.5);
    expect(cam!.zoom).toBeLessThan(6.2);
  });

  it('is not a camera without a viewport height to measure against', () => {
    expect(cameraFromUrl(url)).toBeNull();
    expect(cameraFromUrl(url, 0)).toBeNull();
  });

  it('rounds the derived zoom so an altitude jitter is not a redraw', () => {
    const a = cameraFromUrl(url, 900)!, b = cameraFromUrl(url.replace('1966123m', '1966125m'), 900)!;
    expect(a.zoom).toBe(b.zoom);
  });

  it('still reads the map form exactly as before', () => {
    expect(cameraFromUrl('https://www.google.com/maps/@35.5,-93.7,12z', 900)).toEqual({ lat: 35.5, lng: -93.7, zoom: 12 });
  });

  it('refuses a tilted 3D pose and Street View', () => {
    expect(cameraFromUrl('https://www.google.com/maps/@37.3,-86.6,1000a,35y,90h,60t/data=!3m1!1e3', 900)).toBeNull();
    expect(cameraFromUrl('https://www.google.com/maps/@37.3,-86.6,3a,75y,90h,90t/data=!3m6!1e1', 900)).toBeNull();
  });
});

describe('cameraDrawable', () => {
  it('knows a map or satellite camera from a view it cannot draw', () => {
    expect(cameraDrawable('https://www.google.com/maps/@35.5,-93.7,12z')).toBe(true);
    expect(cameraDrawable('https://www.google.com/maps/@37.3,-86.6,1966123m/data=!3m1!1e3')).toBe(true);
    expect(cameraDrawable('https://www.google.com/maps/@37.3,-86.6,1000a,35y,90h,60t/data=!3m1!1e3')).toBe(false);
    expect(cameraDrawable('https://www.google.com/maps/@37.3,-86.6,3a,75y,90h,90t/data=!3m6!1e1')).toBe(false);
    expect(cameraDrawable('https://www.google.com/maps')).toBe(false);
  });
});
