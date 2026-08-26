/**
 * Device-identity events — the wire row, the key map, and how to name
 * the truck a question is about.
 *
 * Split out of DeviceEventsCard because a component file that also
 * exports helpers loses fast refresh for the whole module
 * (react-refresh/only-export-components).  These three are what the
 * card's tests need to reach without rendering it.
 */

export interface DeviceEvent {
  id: number;
  registry_id: number | null;
  vehicle_id: string;
  vehicle_name: string;
  kind: string;
  old_value: string;
  new_value: string;
  observed_at: string;
  status: string;
  resolution: string;
  resolved_at: string;
}

export interface EventsResponse {
  events: DeviceEvent[];
  open_count: number;
}

/** ``device_event_log.kind`` → the callout key that renders it.
 *
 *  The event vocabulary predates the callouts lane and is stored in
 *  the log's rows, so it is MAPPED here rather than renamed — the
 *  backend keeps saying ``vin_change`` and the reader sees one shape
 *  of statement across the whole page.  Mirrors
 *  ``EVENT_CALLOUT_KEYS`` in features/vehicles/callouts.py. */
export const EVENT_CALLOUT_KEY: Record<string, string> = {
  vin_change: 'vehicle.vin_changed',
  gateway_swap: 'vehicle.gateway_swapped',
  odo_rebase: 'vehicle.odometer_rebased',
};

/**
 * What to CALL the truck this event is about.
 *
 * The registry unit, resolved through ``registry_id`` — never the
 * event's stored ``vehicle_name``.  That name is the provider's label
 * captured at ingest and it does not have to agree with ours: a live
 * account had an event named "128" whose ``registry_id`` pointed at
 * unit 6862, and another named "254" pointing at unit 253.  The
 * buttons act on ``registry_id``.  A control that names one truck and
 * edits another is how someone splits the wrong unit's history —
 * irreversibly, and believing they did something else.
 *
 * So the label is taken from the same place the action is: whatever
 * the registry row says, which is also what the vehicle list on the
 * same page shows, so one truck reads as one truck.
 *
 * Falls back to the provider name only when no registry row matches
 * (an event predating registry stamping, or a row since removed) —
 * there is nothing better to show, and a blank subject would be worse
 * than an imperfect one.
 */
export function subjectUnit(
  e: Pick<DeviceEvent, 'registry_id' | 'vehicle_name' | 'vehicle_id'>,
  unitByRegistryId: Map<number, string>,
): string {
  const known = e.registry_id == null
    ? undefined : unitByRegistryId.get(e.registry_id);
  return known || e.vehicle_name || e.vehicle_id;
}

