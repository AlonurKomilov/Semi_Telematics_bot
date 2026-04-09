import { useEffect, useRef, useState } from 'react';
import { apiJSON } from '../../api/client';
import type { GeofenceFeature, GeofencesResponse } from '../../types';
import type L from 'leaflet';

export default function Geofences() {
  const mapRef = useRef<HTMLDivElement>(null);
  const leafletMap = useRef<L.Map | null>(null);
  const [geofences, setGeofences] = useState<GeofenceFeature[]>([]);
  const [selected, setSelected] = useState<GeofenceFeature | null>(null);

  useEffect(() => {
    const loadLeaflet = (): Promise<typeof L> =>
      new Promise((resolve) => {
        if (window.L) return resolve(window.L);
        const s = document.createElement('script');
        s.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
        s.onload = () => resolve(window.L);
        document.head.appendChild(s);
      });

    loadLeaflet().then(async (Leaf) => {
      if (!mapRef.current || leafletMap.current) return;
      const map = Leaf.map(mapRef.current).setView([39.8, -98.5], 5);
      Leaf.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap',
      }).addTo(map);
      leafletMap.current = map;

      try {
        const data = await apiJSON<GeofencesResponse>('/map/geofences');
        const features = data.features || [];
        setGeofences(features);
        const group = Leaf.layerGroup().addTo(map);
        features.forEach((f) => {
          let layer: L.Layer | null = null;
          if (f.geometry.type === 'Polygon') {
            const coords = (f.geometry.coordinates as [number, number][][])[0].map(
              ([lng, lat]: [number, number]) => [lat, lng] as L.LatLngTuple,
            );
            layer = Leaf.polygon(coords, { color: '#3b82f6', weight: 2, fillOpacity: 0.15 });
          } else if (f.geometry.type === 'Point' && f.properties?.radius) {
            const [lng, lat] = f.geometry.coordinates as [number, number];
            layer = Leaf.circle([lat, lng], {
              radius: f.properties.radius,
              color: '#3b82f6',
              weight: 2,
              fillOpacity: 0.15,
            });
          }
          if (layer) {
            layer.on('click', () => setSelected(f));
            layer.bindPopup(f.properties?.name || 'Geofence');
            layer.addTo(group);
          }
        });
      } catch { /* ignore */ }
    });

    return () => {
      if (leafletMap.current) { leafletMap.current.remove(); leafletMap.current = null; }
    };
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Geofences ({geofences.length})</h1>
      <div className="flex gap-4">
        <div ref={mapRef} className="flex-1 h-[calc(100vh-12rem)] rounded-xl border border-gray-800 z-0" />

        {/* Details panel */}
        <div className="w-72 shrink-0">
          {selected ? (
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-lg font-semibold truncate">{selected.properties?.name || 'Geofence'}</h2>
                <button onClick={() => setSelected(null)} className="text-gray-500 hover:text-white text-sm">✕</button>
              </div>
              <dl className="space-y-2 text-sm">
                <div>
                  <dt className="text-gray-400">Type</dt>
                  <dd className="capitalize">{selected.geometry.type === 'Point' ? 'Circle' : 'Polygon'}</dd>
                </div>
                {selected.properties?.radius && (
                  <div>
                    <dt className="text-gray-400">Radius</dt>
                    <dd>{(selected.properties.radius * 0.000621371).toFixed(2)} mi ({selected.properties.radius.toLocaleString()} m)</dd>
                  </div>
                )}
                {selected.geometry.type === 'Polygon' && (
                  <div>
                    <dt className="text-gray-400">Vertices</dt>
                    <dd>{((selected.geometry.coordinates as [number, number][][])[0] || []).length}</dd>
                  </div>
                )}
              </dl>
            </div>
          ) : (
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 text-center text-gray-500 text-sm">
              <p className="text-2xl mb-2">📍</p>
              <p>Click a geofence on the map to view details</p>
            </div>
          )}

          {/* Geofence list */}
          {geofences.length > 0 && (
            <div className="mt-4 bg-gray-900 border border-gray-800 rounded-xl p-3 max-h-96 overflow-y-auto">
              <h3 className="text-xs text-gray-400 uppercase tracking-wide mb-2">All Geofences</h3>
              <ul className="space-y-1">
                {geofences.map((f, i) => (
                  <li key={i}>
                    <button
                      onClick={() => setSelected(f)}
                      className={`w-full text-left px-2 py-1.5 rounded text-sm transition ${
                        selected === f ? 'bg-blue-600/20 text-blue-400' : 'text-gray-300 hover:bg-gray-800'
                      }`}
                    >
                      {f.properties?.name || `Geofence ${i + 1}`}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
