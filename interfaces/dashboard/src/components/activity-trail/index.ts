/**
 * Activity Trail — the frontend half of `capabilities/activity_trail`.
 *
 * Named for the capability, not for "history", because this app already
 * has a Service History (a vehicle's past services) that means something
 * entirely different.  One search for "activity" now finds the whole
 * stack: the capability, the `activity_events` store, the /activity
 * endpoints, and these components.  What USERS read still says
 * "activity history" — code vocabulary and product wording are allowed
 * to differ; two code names for one concept are not.
 */
export {
  ActivityTrailList, ChangeLines,
  ACTION_LABEL, ENTITY_LABEL, actionLabel, entityLabel, fieldLabel,
  type ActivityEvent, type ActivityChange,
} from './ActivityTrailList';
export { ActivityTrailDialog, ActivityTrailTrigger } from './ActivityTrailDialog';
