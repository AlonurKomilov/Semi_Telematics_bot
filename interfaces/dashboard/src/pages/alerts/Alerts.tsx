/**
 * Re-export of the Pattern B alerts page.
 *
 * The implementation lives at ``features/alerts/Alerts.tsx``; this
 * file is a thin re-export so the router import path
 * (``pages/alerts/Alerts``) keeps working without touching
 * ``router.tsx``.
 */
export { default } from '../../features/alerts/Alerts';
