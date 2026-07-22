/**
 * Banners — the app's rich-notification lane (shared chrome, any feature
 * may use it).
 *
 *   showBanner       a severity-toned card with optional countdown
 *   stagedAction     cancellable pending action (commit AFTER countdown)
 *   undoableAction   execute-then-undo window
 *   position store   where the whole lane renders (user preference)
 */
export { AppBanner, showBanner } from './AppBanner';
export type { BannerAction, BannerOptions } from './AppBanner';
export {
  pendingStagedCount,
  stagedAction,
  undoableAction,
} from './stagedAction';
export type { StagedActionOptions, UndoableActionOptions } from './stagedAction';
export {
  getNotifPosition,
  setNotifPosition,
  useNotifPosition,
} from './position';
export type { NotifPosition } from './position';
