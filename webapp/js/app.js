/**
 * App — SPA router and page initialization.
 */
const App = (() => {
    let _currentPage = 'map';

    /** Initialize the app. */
    async function init() {
        showLoading(true);

        // Authenticate
        const ok = await Auth.init();
        if (!ok) {
            showLoading(false);
            document.getElementById('auth-error').classList.remove('hidden');
            return;
        }

        // Set up navigation
        document.querySelectorAll('.nav-btn').forEach(btn => {
            btn.addEventListener('click', () => navigate(btn.dataset.page));
        });

        // Handle hash changes
        window.addEventListener('hashchange', () => {
            const page = location.hash.replace('#', '') || 'map';
            navigate(page, false);
        });

        // Initial page from hash
        const startPage = location.hash.replace('#', '') || 'map';
        await navigate(startPage, false);

        showLoading(false);

        // Apply Telegram theme colors if available
        applyTelegramTheme();
    }

    /** Navigate to a page. */
    async function navigate(page, updateHash = true) {
        const validPages = ['map', 'trucks', 'alerts'];
        if (!validPages.includes(page)) page = 'map';
        if (page === _currentPage && document.querySelector('.page.active')) return;

        _currentPage = page;

        // Update nav buttons
        document.querySelectorAll('.nav-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.page === page);
        });

        // Show correct page
        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        const el = document.getElementById(`page-${page}`);
        if (el) el.classList.add('active');

        if (updateHash) {
            location.hash = page;
        }

        // Page-specific init
        switch (page) {
            case 'map':
                await FleetMap.start();
                break;
            case 'trucks':
                await loadTrucks();
                break;
            case 'alerts':
                await loadAlerts();
                break;
        }
    }

    /** Load truck list. */
    async function loadTrucks() {
        const list = document.getElementById('truck-list');
        list.innerHTML = '<div class="empty-state"><div class="spinner"></div></div>';

        try {
            const data = await Auth.apiJSON('/api/fleet/vehicles');
            const vehicles = data.vehicles || [];

            if (vehicles.length === 0) {
                list.innerHTML = `
                    <div class="empty-state">
                        <div class="emoji">🚛</div>
                        <p>No vehicles found</p>
                    </div>`;
                return;
            }

            list.innerHTML = vehicles.map(v => {
                const speed = v.speed_mph != null ? `${Math.round(v.speed_mph)} mph` : '—';
                const fuel = v.fuel_percent != null ? `${v.fuel_percent}%` : '—';
                const state = v.engine_state === 'On'
                    ? (v.speed_mph > 2 ? 'moving' : 'idle')
                    : 'stopped';

                return `
                    <div class="card" onclick="App.focusTruck('${escapeAttr(v.name)}')">
                        <div class="card-header">
                            <span class="card-title">🚛 ${escapeHtml(v.name)}</span>
                            <span class="status-badge ${state}">${state}</span>
                        </div>
                        <div class="card-subtitle">${escapeHtml(v.company || '')}</div>
                        <div class="card-detail">
                            <span>⚡ ${speed}</span>
                            <span>⛽ ${fuel}</span>
                            ${v.fault_count ? `<span>⚠️ ${v.fault_count} faults</span>` : ''}
                        </div>
                        <div class="card-subtitle" style="margin-top:4px">
                            📍 ${escapeHtml(truncate(v.address || 'Unknown', 60))}
                        </div>
                    </div>`;
            }).join('');

            // Search filter
            const search = document.getElementById('truck-search');
            search.oninput = () => {
                const q = search.value.toLowerCase();
                list.querySelectorAll('.card').forEach(card => {
                    const text = card.textContent.toLowerCase();
                    card.style.display = text.includes(q) ? '' : 'none';
                });
            };
        } catch (e) {
            list.innerHTML = `
                <div class="empty-state">
                    <div class="emoji">⚠️</div>
                    <p>Failed to load vehicles</p>
                </div>`;
            console.error('Truck list error:', e);
        }
    }

    /** Load alerts. */
    async function loadAlerts() {
        const list = document.getElementById('alert-list');
        list.innerHTML = '<div class="empty-state"><div class="spinner"></div></div>';

        try {
            const data = await Auth.apiJSON('/api/alerts/pending');
            const alerts = data.alerts || [];

            if (alerts.length === 0) {
                list.innerHTML = `
                    <div class="empty-state">
                        <div class="emoji">✅</div>
                        <p>No pending alerts</p>
                    </div>`;
                return;
            }

            list.innerHTML = alerts.map(a => `
                <div class="card alert-card" data-id="${a.id || ''}">
                    <div class="alert-type">${escapeHtml(a.alert_type || 'Alert')}</div>
                    <div class="card-header">
                        <span class="card-title">${escapeHtml(a.vehicle_name || a.alert_key || 'Unknown')}</span>
                    </div>
                    <div class="card-subtitle">${escapeHtml(a.message || '')}</div>
                    <div class="alert-actions">
                        <button class="btn btn-primary" onclick="App.ackAlert(${a.id})">
                            ✅ Acknowledge
                        </button>
                    </div>
                </div>
            `).join('');
        } catch (e) {
            list.innerHTML = `
                <div class="empty-state">
                    <div class="emoji">⚠️</div>
                    <p>Failed to load alerts</p>
                </div>`;
            console.error('Alerts error:', e);
        }
    }

    /** Acknowledge an alert. */
    async function ackAlert(alertId) {
        try {
            await Auth.apiFetch(`/api/alerts/${alertId}/acknowledge`, { method: 'POST' });
            // Reload alerts
            await loadAlerts();
        } catch (e) {
            console.error('Ack failed:', e);
        }
    }

    /** Focus a truck on the map. */
    function focusTruck(name) {
        navigate('map');
        // The map will reload and show all trucks — future: zoom to specific truck
    }

    /** Show/hide loading overlay. */
    function showLoading(show) {
        document.getElementById('loading').classList.toggle('hidden', !show);
    }

    /** Apply Telegram theme if available. */
    function applyTelegramTheme() {
        if (!Auth.isMiniApp()) return;
        const tg = window.Telegram.WebApp;
        if (tg.themeParams) {
            const root = document.documentElement.style;
            const tp = tg.themeParams;
            if (tp.bg_color) root.setProperty('--tg-theme-bg-color', tp.bg_color);
            if (tp.text_color) root.setProperty('--tg-theme-text-color', tp.text_color);
            if (tp.hint_color) root.setProperty('--tg-theme-hint-color', tp.hint_color);
            if (tp.link_color) root.setProperty('--tg-theme-link-color', tp.link_color);
            if (tp.button_color) root.setProperty('--tg-theme-button-color', tp.button_color);
            if (tp.button_text_color) root.setProperty('--tg-theme-button-text-color', tp.button_text_color);
            if (tp.secondary_bg_color) root.setProperty('--tg-theme-secondary-bg-color', tp.secondary_bg_color);
        }
    }

    // ── Utility ────────────────────────────────────────

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function escapeAttr(str) {
        return str.replace(/'/g, "\\'").replace(/"/g, '&quot;');
    }

    function truncate(str, max) {
        return str.length > max ? str.substring(0, max) + '…' : str;
    }

    return { init, navigate, ackAlert, focusTruck };
})();

// Boot
document.addEventListener('DOMContentLoaded', () => App.init());
