/**
 * Auth module — handles Telegram Mini App + standalone browser auth.
 */
const Auth = (() => {
    let _token = null;
    let _user = null;

    /** Determine the API base URL (same origin). */
    function apiBase() {
        return window.location.origin;
    }

    /** Check if running inside Telegram Mini App. */
    function isMiniApp() {
        return !!(window.Telegram && window.Telegram.WebApp &&
                  window.Telegram.WebApp.initData);
    }

    /** Initialize auth — call once on startup. */
    async function init() {
        if (isMiniApp()) {
            // Expand Mini App to full height
            const tg = window.Telegram.WebApp;
            tg.expand();
            tg.ready();

            try {
                const resp = await fetch(`${apiBase()}/api/auth/telegram`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ init_data: tg.initData }),
                });

                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(err.detail || 'Auth failed');
                }

                const data = await resp.json();
                _token = data.access_token;
                _user = data.user;
                return true;
            } catch (e) {
                console.error('Auth error:', e);
                return false;
            }
        } else {
            // Standalone browser mode — check for stored token
            _token = sessionStorage.getItem('st_token');
            if (_token) {
                try {
                    const resp = await apiFetch('/api/health');
                    if (resp.ok) {
                        _user = JSON.parse(sessionStorage.getItem('st_user') || '{}');
                        return true;
                    }
                } catch (_) { /* token expired */ }
                sessionStorage.removeItem('st_token');
                sessionStorage.removeItem('st_user');
                _token = null;
            }
            return false;
        }
    }

    /** Make an authenticated API request. */
    async function apiFetch(path, options = {}) {
        const headers = options.headers || {};
        if (_token) {
            headers['Authorization'] = `Bearer ${_token}`;
        }
        return fetch(`${apiBase()}${path}`, { ...options, headers });
    }

    /** Get a JSON response from API. */
    async function apiJSON(path, options = {}) {
        const resp = await apiFetch(path, options);
        if (!resp.ok) {
            throw new Error(`API ${resp.status}: ${resp.statusText}`);
        }
        return resp.json();
    }

    function getToken() { return _token; }
    function getUser() { return _user; }
    function isAuthenticated() { return !!_token; }

    return { init, apiFetch, apiJSON, getToken, getUser, isAuthenticated, isMiniApp };
})();
