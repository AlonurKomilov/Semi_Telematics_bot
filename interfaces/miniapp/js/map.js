/**
 * @deprecated  This file is dead code — the canonical miniapp map is
 * interfaces/miniapp/src/pages/MapPage.tsx, loaded by Vite via index.html →
 * src/main.tsx.  This pre-React implementation is only referenced by
 * js/app.js (also dead).  Safe to delete in a follow-up cleanup PR.
 *
 * Interactive Fleet Map — Leaflet.js with vehicle markers, clustering, popups.
 */
const FleetMap = (() => {
    let _map = null;
    let _clusterGroup = null;
    let _markers = {};          // vehicleId -> marker
    let _geofenceLayer = null;
    let _refreshTimer = null;
    const REFRESH_INTERVAL = 30000; // 30 seconds

    /** Initialize the map. */
    function init() {
        if (_map) return;

        _map = L.map('map', {
            zoomControl: true,
            attributionControl: true,
        }).setView([39.8283, -98.5795], 5); // Center of USA

        // OpenStreetMap tiles (free)
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap contributors',
            maxZoom: 19,
        }).addTo(_map);

        // Marker cluster group
        _clusterGroup = L.markerClusterGroup({
            maxClusterRadius: 50,
            spiderfyOnMaxZoom: true,
            showCoverageOnHover: false,
            disableClusteringAtZoom: 14,
        });
        _map.addLayer(_clusterGroup);

        // Geofence layer
        _geofenceLayer = L.layerGroup().addTo(_map);

        // Fix Leaflet rendering in hidden containers
        setTimeout(() => _map.invalidateSize(), 200);
    }

    /** Create a colored div icon for a truck marker. */
    function createMarkerIcon(status) {
        const emoji = '🚛';
        return L.divIcon({
            className: '',
            html: `<div class="truck-marker ${status}">${emoji}</div>`,
            iconSize: [32, 32],
            iconAnchor: [16, 16],
            popupAnchor: [0, -20],
        });
    }

    /** Build popup HTML for a vehicle. */
    function popupHTML(props) {
        const speed = props.speed_mph != null ? `${Math.round(props.speed_mph)} mph` : '—';
        const fuel = props.fuel_percent != null ? `${props.fuel_percent}%` : '—';
        const updated = props.updated_at ? new Date(props.updated_at).toLocaleTimeString() : '—';

        return `
            <div class="popup-name">${escapeHtml(props.name)}</div>
            <span class="status-badge ${props.status}">${props.status}</span>
            <div style="margin-top:8px">
                <div class="popup-row">
                    <span class="popup-label">Speed</span>
                    <span>${speed}</span>
                </div>
                <div class="popup-row">
                    <span class="popup-label">Fuel</span>
                    <span>${fuel}</span>
                </div>
                <div class="popup-row">
                    <span class="popup-label">Company</span>
                    <span>${escapeHtml(props.company || '—')}</span>
                </div>
                <div class="popup-row">
                    <span class="popup-label">Address</span>
                    <span>${escapeHtml(truncate(props.address || '—', 50))}</span>
                </div>
                <div class="popup-row">
                    <span class="popup-label">Updated</span>
                    <span>${updated}</span>
                </div>
            </div>
        `;
    }

    /** Fetch vehicle positions and update markers. */
    async function loadVehicles() {
        try {
            const data = await Auth.apiJSON('/api/map/vehicles');
            const features = data.features || [];

            // Track which IDs are present
            const activeIds = new Set();

            features.forEach(f => {
                const id = f.properties.id;
                const coords = f.geometry.coordinates; // [lng, lat]
                const props = f.properties;
                activeIds.add(id);

                if (_markers[id]) {
                    // Update existing marker
                    _markers[id].setLatLng([coords[1], coords[0]]);
                    _markers[id].setIcon(createMarkerIcon(props.status));
                    _markers[id].setPopupContent(popupHTML(props));
                } else {
                    // Create new marker
                    const marker = L.marker([coords[1], coords[0]], {
                        icon: createMarkerIcon(props.status),
                    }).bindPopup(popupHTML(props));

                    marker.on('click', () => showTruckSheet(props));

                    _markers[id] = marker;
                    _clusterGroup.addLayer(marker);
                }
            });

            // Remove stale markers
            Object.keys(_markers).forEach(id => {
                if (!activeIds.has(id)) {
                    _clusterGroup.removeLayer(_markers[id]);
                    delete _markers[id];
                }
            });

            // Fit bounds on first load
            if (features.length > 0 && _clusterGroup.getLayers().length > 0) {
                const bounds = _clusterGroup.getBounds();
                if (bounds.isValid()) {
                    _map.fitBounds(bounds, { padding: [40, 40], maxZoom: 12 });
                }
            }

            updateVehicleCount(features.length);
        } catch (e) {
            console.error('Failed to load vehicles:', e);
        }
    }

    /** Load and render geofences. */
    async function loadGeofences() {
        try {
            const data = await Auth.apiJSON('/api/map/geofences');
            _geofenceLayer.clearLayers();

            (data.features || []).forEach(f => {
                const props = f.properties;
                const style = {
                    color: '#5eaaf0',
                    weight: 2,
                    opacity: 0.6,
                    fillOpacity: 0.1,
                };

                if (f.geometry.type === 'Polygon') {
                    const coords = f.geometry.coordinates[0].map(c => [c[1], c[0]]);
                    L.polygon(coords, style)
                        .bindTooltip(props.name)
                        .addTo(_geofenceLayer);
                } else if (f.geometry.type === 'Point' && props.type === 'circle') {
                    L.circle([f.geometry.coordinates[1], f.geometry.coordinates[0]], {
                        ...style,
                        radius: props.radius_meters,
                    }).bindTooltip(props.name)
                      .addTo(_geofenceLayer);
                }
            });
        } catch (e) {
            console.error('Failed to load geofences:', e);
        }
    }

    /** Show truck detail in bottom sheet. */
    function showTruckSheet(props) {
        const sheet = document.getElementById('truck-sheet');
        const content = document.getElementById('truck-sheet-content');

        const speed = props.speed_mph != null ? `${Math.round(props.speed_mph)} mph` : '—';
        const fuel = props.fuel_percent != null ? `${props.fuel_percent}%` : '—';

        content.innerHTML = `
            <div class="card-header">
                <span class="card-title">${escapeHtml(props.name)}</span>
                <span class="status-badge ${props.status}">${props.status}</span>
            </div>
            <div class="card-detail">
                <span>🏢 ${escapeHtml(props.company || '—')}</span>
                <span>⚡ ${speed}</span>
                <span>⛽ ${fuel}</span>
            </div>
            <div class="card-subtitle" style="margin-top:8px">
                📍 ${escapeHtml(props.address || 'Unknown location')}
            </div>
        `;

        sheet.classList.remove('hidden');

        // Close on handle tap
        sheet.querySelector('.sheet-handle').onclick = () => {
            sheet.classList.add('hidden');
        };
    }

    /** Update the vehicle count badge. */
    function updateVehicleCount(count) {
        const btn = document.querySelector('[data-page="map"]');
        if (btn) btn.textContent = `🗺 Map (${count})`;
    }

    /** Start auto-refresh. */
    function startRefresh() {
        stopRefresh();
        _refreshTimer = setInterval(loadVehicles, REFRESH_INTERVAL);
    }

    /** Stop auto-refresh. */
    function stopRefresh() {
        if (_refreshTimer) {
            clearInterval(_refreshTimer);
            _refreshTimer = null;
        }
    }

    /** Resize the map (call when page becomes visible). */
    function resize() {
        if (_map) {
            setTimeout(() => _map.invalidateSize(), 100);
        }
    }

    /** Full initialization: load data + start refresh. */
    async function start() {
        init();
        resize();
        await Promise.all([loadVehicles(), loadGeofences()]);
        startRefresh();
    }

    // ── Utility ────────────────────────────────────────

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function truncate(str, max) {
        return str.length > max ? str.substring(0, max) + '…' : str;
    }

    return { init, start, resize, stopRefresh, startRefresh, loadVehicles };
})();
