import { useEffect, useState } from 'react';
import { apiJSON } from '../../api/client';

interface WeatherVehicle {
  name: string;
  company: string;
  temp_f: number | null;
  temp_c: number | null;
  baro_inhg: number | null;
  temp_time: string | null;
  location: string;
}

interface WeatherSummary {
  avg_f: number;
  min_f: number;
  max_f: number;
  freezing_count: number;
  hot_count: number;
  reporting_count: number;
}

interface WeatherResponse {
  vehicles: WeatherVehicle[];
  count: number;
  summary: WeatherSummary;
}

function tempColor(f: number | null): string {
  if (f === null) return 'text-gray-600';
  if (f <= 32) return 'text-blue-400';
  if (f <= 50) return 'text-cyan-400';
  if (f <= 75) return 'text-green-400';
  if (f <= 95) return 'text-yellow-400';
  return 'text-red-400';
}

function tempBg(f: number | null): string {
  if (f === null) return '';
  if (f <= 32) return 'bg-blue-500/10';
  if (f <= 95) return '';
  return 'bg-red-500/10';
}

export default function Weather() {
  const [vehicles, setVehicles] = useState<WeatherVehicle[]>([]);
  const [summary, setSummary] = useState<WeatherSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    apiJSON<WeatherResponse>('/fleet/weather')
      .then((d) => {
        setVehicles(d.vehicles || []);
        setSummary(d.summary || null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, []);

  if (error && vehicles.length === 0) return <p className="text-red-400">{error}</p>;

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">🌡️ Fleet Weather</h1>

      {/* Summary cards */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
          <div className="bg-gray-800 rounded-lg p-4">
            <div className="text-xs text-gray-500">Average</div>
            <div className={`text-2xl font-bold ${tempColor(summary.avg_f)}`}>{summary.avg_f}°F</div>
          </div>
          <div className="bg-gray-800 rounded-lg p-4">
            <div className="text-xs text-gray-500">Range</div>
            <div className="text-lg font-bold text-gray-200">{summary.min_f}° – {summary.max_f}°F</div>
          </div>
          <div className="bg-gray-800 rounded-lg p-4">
            <div className="text-xs text-gray-500">Freezing (≤32°F)</div>
            <div className={`text-2xl font-bold ${summary.freezing_count > 0 ? 'text-blue-400' : 'text-gray-600'}`}>{summary.freezing_count}</div>
          </div>
          <div className="bg-gray-800 rounded-lg p-4">
            <div className="text-xs text-gray-500">Hot (≥95°F)</div>
            <div className={`text-2xl font-bold ${summary.hot_count > 0 ? 'text-red-400' : 'text-gray-600'}`}>{summary.hot_count}</div>
          </div>
        </div>
      )}

      {loading && <p className="text-gray-500">Loading...</p>}

      {/* Vehicle grid */}
      {!loading && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
          {vehicles.map((v) => (
            <div key={v.name} className={`rounded-lg border border-gray-800 p-4 ${tempBg(v.temp_f)}`}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium text-gray-200">🚛 {v.name}</span>
                {v.company && <span className="text-xs text-gray-500">{v.company}</span>}
              </div>
              {v.temp_f !== null ? (
                <div className={`text-3xl font-bold ${tempColor(v.temp_f)}`}>
                  {v.temp_f}°F
                  <span className="text-sm font-normal text-gray-500 ml-2">{v.temp_c}°C</span>
                </div>
              ) : (
                <div className="text-lg text-gray-600">No data</div>
              )}
              {v.baro_inhg !== null && (
                <div className="text-xs text-gray-500 mt-1">{v.baro_inhg} inHg</div>
              )}
              {v.location && <div className="text-xs text-gray-500 mt-1 truncate">{v.location}</div>}
              {v.temp_time && (
                <div className="text-xs text-gray-600 mt-1">{new Date(v.temp_time).toLocaleString()}</div>
              )}
            </div>
          ))}
        </div>
      )}

      {!loading && vehicles.length === 0 && (
        <p className="text-gray-500 text-center mt-8">No weather data available</p>
      )}
    </div>
  );
}
