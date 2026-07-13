/**
 * Cross-feature bridge contract: the shape and router-state key used to
 * hand a pre-filled NEW work order to WorkOrderForm from another surface
 * (a fault code, a failed inspection item, …).
 *
 * The payload rides react-router ``location.state`` — the same pattern
 * VehicleFaults uses to hand a message into the AI chat route — so it
 * carries structured, possibly-long text (a defect description) cleanly
 * without stuffing it into the URL.  WorkOrderForm reads
 * ``location.state[WO_PREFILL_STATE_KEY]`` on mount in create mode and
 * seeds the form; nothing in the save path changes because the form
 * already spreads its whole state into the POST body.
 *
 * The navigation itself (plus the duplicate-guard confirm) lives in
 * ``useWorkOrderBridge`` — every entry point goes through that hook so
 * the "already open?" check is applied uniformly.
 *
 * Trade-off: a hard refresh of the pre-filled create page loses the
 * seed (state doesn't survive reload) — acceptable for a one-shot
 * "create from this" action; the operator re-triggers from the source.
 */

/** The subset of work-order fields a bridge can pre-fill.  ``complaint``
 *  (the 3C "what was reported") is the natural home for the fault /
 *  defect description. */
export interface WorkOrderPrefill {
  vehicle_name?: string;
  vehicle_id?: string;
  vehicle_type?: string;
  company_code?: string;
  complaint?: string;
  /** '' | 'scheduled' | 'non_scheduled' | 'emergency' */
  repair_priority?: string;
}

/** Router state key WorkOrderForm looks for. */
export const WO_PREFILL_STATE_KEY = 'woPrefill';
