import { isPermissionRow, permissionRow } from './roleViewTarget';
/** Preview hints carry shell preferences across origins, never actor permissions. */
export const PREVIEW_ROLE_PARAM = 'view_role';
export const PREVIEW_TIER_PARAM = 'view_tier';
export const PREVIEW_ROW_PARAM = 'view_row';

export function readPreviewNavigation(href: string, allowedRoles: readonly string[]) {
  const params = new URL(href).searchParams;
  const role = params.get(PREVIEW_ROLE_PARAM);
  if (!role || !allowedRoles.includes(role)) return null;
  const row = params.get(PREVIEW_ROW_PARAM);
  // Empty row explicitly means the member's own dashboard on arrival.
  if (row === '') return { role, permissionKey: '' };
  if (row !== null) return isPermissionRow(role, row) ? { role, permissionKey: row } : null;
  // Existing links carry the old manager/employee hint.
  const tier = params.get(PREVIEW_TIER_PARAM);
  if (tier !== 'manager' && tier !== 'employee') return null;
  return { role, permissionKey: permissionRow(role, tier === 'manager') };
}

export function previewNavigationUrl(href: string, host: string, role: string, permissionKey: string) {
  const url = new URL(href);
  url.protocol = 'https:';
  url.host = host;
  url.searchParams.set(PREVIEW_ROLE_PARAM, role);
  url.searchParams.delete(PREVIEW_TIER_PARAM);
  url.searchParams.set(PREVIEW_ROW_PARAM, permissionKey);
  return url.href;
}

export function clearPreviewNavigation(href: string) {
  const url = new URL(href);
  url.searchParams.delete(PREVIEW_ROLE_PARAM);
  url.searchParams.delete(PREVIEW_TIER_PARAM);
  url.searchParams.delete(PREVIEW_ROW_PARAM);
  return url.pathname + url.search + url.hash;
}
