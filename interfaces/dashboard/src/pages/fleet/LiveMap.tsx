import { useEffect, useRef, useState } from 'react';
import { apiJSON } from '../../api/client';
import type { MapVehicleFeature, MapVehiclesResponse, MapVehicleProperties } from '../../types';
import type L from 'leaflet';

const TILE_URL = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const TILE_ATTR = '&copy; OpenStreetMap contributors';
const REFRESH_MS = 30000;

function statusColor(v: MapVehicleFeature | MapVehicleProperties): string {
  const props = 'properties' in v ? v.properties : v;
  if ((props.speed_mph || 0) > 0) return '#22c55e';
  if (props.engine_state === 'On') return '#eab308';
  return '#ef4444';
}

export default function LiveMap() {
  const mapRef = useRef<HTMLDivElement>(null);
  const leafletMap = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const [vehicles, setVehicles] = useState<MapVehicleFeature[]>([]);
  const [selected, setSelected] = useState<MapVehicleProperties | null>(null);
  const [statusFilter, setStatusFilter] = useState('all');
  const [search, setSearch] = useState('');

  // Dynamically load Leaflet CSS + JS if not present
  useEffect(() => {
    const id = 'leaflet-css';
    if (!document.getElementById(id)) {
      const link = document.createElement('link');
      link.id = id;
      link.rel = 'stylesheet';
      link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
      document.head.appendChild(link);
    }

    const loadLeaflet = (): Promise<typeof L> =>
      new Promise((resolve) => {
        if (window.L) return resolve(window.L);
        const s = document.createElement('script');
        s.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
        s.onload = () => resolve(window.L);
        document.head.appendChild(s);
      });

    let timer: ReturnType<typeof setInterval>;
    loadLeaflet().then((Leaf) => {
      if (!mapRef.current || leafletMap.current) return;
      const map = Leaf.map(mapRef.current).setView([39.8, -98.5], 5);
      Leaf.tileLayer(TILE_URL, { attribution: TILE_ATTR }).addTo(map);
      leafletMap.current = map;
      layerRef.current = Leaf.layerGroup().addTo(map);
      loadVehicles(Leaf);
      timer = setInterval(() => loadVehicles(Leaf), REFRESH_MS);
    });

    return () => {
      clearInterval(timer);
      if (leafletMap.current) { leafletMap.current.remove(); leafletMap.current = null; }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function loadVehicles(Leaf: typeof L) {
    try {
      const data = await apiJSON<MapVehiclesResponse>('/map/vehicles');
      const features = data.features || [];
      setVehicles(features);
      if (!layerRef.current) return;
      layerRef.current.clearLayers();
      features.forEach((f) => {
        const [lng, lat] = f.geometry.coordinates;
        const color = statusColor(f);
        const icon = Leaf.divIcon({
          className: '',
          html: `<div style="width:14px;height:14px;border-radius:50%;background:${color};border:2px solid #fff;"></div>`,
          iconSize: [14, 14],
          iconAnchor: [7, 7],
        });
        Leaf.marker([lat, lng], { icon })
          .on('click', () => setSelected(f.properties))
          .addTo(layerRef.current!);
      });
    } catch { /* ignore refresh errors */ }
  }

  function vehicleStatus(f: MapVehicleFeature): string {
    const p = f.properties;
    if ((p.speed_mph || 0) > 0) return 'moving';
    if (p.engine_state === 'On') return 'idle';
    return 'stopped';
  }

  const filtered = vehicles.filter((f: MapVehicleFeature) => {
    if (statusFilter !== 'all' && vehicleStatus(f) !== statusFilter) return false;
    if (search && !f.properties.name.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const counts: Record<string, number> = { all: vehicles.length, moving: 0, idle: 0, stopped: 0 };
  vehicles.forEach((f: MapVehicleFeature) => { counts[vehicleStatus(f)]++; });

  return (
    <div className="flex h-[calc(100vh-8rem)] gap-4">
      {/* Map */}
      <div ref={mapRef} className="flex-1 rounded-xl overflow-hidden border border-gray-800 z-0" />

      {/* Side panel */}
      <div className="w-80 bg-gray-900 border border-gray-800 rounded-xl overflow-y-auto shrink-0">
        <div className="p-4 border-b border-gray-800 space-y-3">
          <h2 className="font-semibold">Vehicles ({filtered.length})</h2>
          <input
            type="text"
            placeholder="Search vehicles..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm placeholder-gray-500 focus:outline-none focus:border-blue-500"
          />
          <div className="flex gap-1">
            {[['all', 'All'], ['moving', 'Moving'], ['idle', 'Idle'], ['stopped', 'Stopped']].map(
              ([key, label]) => (
                <button
                  key={key}
                  onClick={() => setStatusFilter(key)}
                  className={`text-xs px-2 py-1 rounded ${statusFilter === key ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'}`}
                >
                  {label} ({counts[key]})
                </button>
              ),
            )}
          </div>
        </div>
        {selected ? (
          <div className="p-4 space-y-2 text-sm">
            <button onClick={() => setSelected(null)} className="text-blue-400 hover:underline text-xs">← Back</button>
            <h3 className="font-bold text-lg">{selected.name}</h3>
            <p className="text-gray-400">{selected.address || 'Unknown location'}</p>
            <p>Speed: {selected.speed_mph ?? 0} mph</p>
            <p>Engine: {selected.engine_state || 'Off'}</p>
            {selected.fuel_percent != null && <p>Fuel: {Math.round(selected.fuel_percent)}%</p>}
            <p>Company: {selected.company || '—'}</p>
          </div>
        ) : (
          <div className="divide-y divide-gray-800">
            {filtered.map((f) => {
              const p = f.properties;
              return (
                <button
                  key={p.id || p.name}
                  onClick={() => {
                    setSelected(p);
                    const [lng, lat] = f.geometry.coordinates;
                    leafletMap.current?.setView([lat, lng], 14);
                  }}
                  className="w-full text-left px-4 py-3 hover:bg-gray-800 transition text-sm"
                >
                  <div className="flex items-center gap-2">
                    <span
                      className="w-2.5 h-2.5 rounded-full shrink-0"
                      style={{ background: statusColor(f) }}
                    />
                    <span className="font-medium truncate">{p.name}</span>
                  </div>
                  <p className="text-xs text-gray-500 ml-5 truncate">{p.address || '—'}</p>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
