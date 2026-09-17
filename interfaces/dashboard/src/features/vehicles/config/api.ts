/**
 * The /vehicles/config wire — vehicle source precedence + auto-pilot.
 * Feature-owned: the panel that edits vehicle settings imports from
 * the vehicles feature, not from integrations.
 */
import { apiJSON } from '../../../api/client';

export interface PrecedenceField {
  key: string;
  label: string;
  primary: string;
}

export interface LifecycleSource {
  key: string;
  /** Only verbs whose MECHANISM exists — datatruck has no inactivate
   *  path, so it never carries the flag and no dead switch renders. */
  verbs: Record<string, boolean>;
}

/** Live readings — the `vehicle_state` entity.  A reading group moves
 *  whole with its clock; `__newest__` is a selectable rule the server
 *  labels for us, never a provider id. */
export interface StateReadingsConfig {
  sources: string[];
  fields: PrecedenceField[];
  source_labels: Record<string, string>;
  applies_when: string;
}

export interface SourcePrecedence {
  sources: string[];
  fields: PrecedenceField[];
  lifecycle?: { sources: LifecycleSource[] };
  state?: StateReadingsConfig;
}

export async function getVehiclesConfig(): Promise<SourcePrecedence> {
  return apiJSON<SourcePrecedence>('/vehicles/config');
}

export async function putVehiclesConfig(
  primary: Record<string, string>,
  lifecycle?: Record<string, Record<string, boolean>>,
  state?: Record<string, string>,
): Promise<SourcePrecedence> {
  return apiJSON<SourcePrecedence>('/vehicles/config', {
    method: 'PUT',
    // `lifecycle` / `state` omitted = untouched server-side, so a
    // precedence-only save stays byte-identical.
    body: {
      primary,
      ...(lifecycle ? { lifecycle } : {}),
      ...(state ? { state } : {}),
    },
  });
}
