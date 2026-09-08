/**
 * Mods is a SERVICE in the permissions matrix — one View row, granted per
 * role — and this is its flag. Owner's call (2026-09-08): not account-wide,
 * no config verb, nothing flows through it; it exists so a role can be
 * withheld the personal look the way any channel is withheld.
 *
 * What "withheld" means, and every door reads it from here:
 *   - the top-bar palette, the avatar-menu item and the profile card are
 *     not rendered;
 *   - `/mods` redirects, like any gated route;
 *   - every `mods.*` preference is reset to its default (`ModsLock`), so
 *     the app looks and sounds as it ships — a stored dark-purple look
 *     must not outlive the grant that allowed it.
 */
export const MODS_PERMISSION = 'can_view_mods';
