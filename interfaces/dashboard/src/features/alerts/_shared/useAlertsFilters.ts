/**
 * Alerts filter state — URL params are the single source of truth.
 *
 * Why URL params (vs Context or god-prop)?
 *   • Sections become genuinely independent: every section that needs
 *     a filter value calls this hook and reads/writes the URL.  No
 *     shared object to thread through props or wrap in a Provider.
 *   • Bookmarkable / shareable: a Safety user can paste a URL filtered
 *     to "harsh_event critical last 7 days" into chat.
 *   • Debug-friendly: "Why is this filter set this way?" is answered
 *     by reading the address bar.
 *
 * First-load logic: if NONE of the recognised filter params are in
 * the URL, the hook writes the active persona's defaults via
 * ``setSearchParams(defaults, { replace: true })`` — preserves the
 * back button.  If ANY filter param is present (e.g. a bookmarked
 * deep link), defaults are NOT applied: the URL wins.
 *
 * Compound update semantics preserved from the pre-refactor Alerts
 * page: changing any filter resets ``page`` to 1, and changing
 * ``ackState`` ALSO clears the selection set (selection lives in
 * AlertsSelectionContext — see useAlertsSelection).  Without those
 * resets, "Page 7 of 22" stays meaningless after a filter narrows
 * the result set, and ack'd-into-history alerts stay "selected".
 */
import { useCallback, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useShellConfig } from '../../../hooks/useShellConfig';
import {
  resolveFilterDefaults,
  type AlertAckState,
  type AlertSeverity,
  type AlertType,
  type FilterDefaults,
} from '../personaConfig';

const FILTER_PARAM_KEYS = [
  'typeFilter',
  'severityFilter',
  'ackState',
  'vehicleSearch',
  'days',
  'page',
] as const;

type FilterParamKey = (typeof FILTER_PARAM_KEYS)[number];

export interface AlertsFiltersAPI {
  typeFilter: AlertType;
  severityFilter: AlertSeverity;
  ackState: AlertAckState;
  vehicleSearch: string;
  days: number;
  page: number;
  setTypeFilter: (v: AlertType) => void;
  setSeverityFilter: (v: AlertSeverity) => void;
  setAckState: (v: AlertAckState) => void;
  setVehicleSearch: (v: string) => void;
  setDays: (v: number) => void;
  setPage: (v: number) => void;
  /** True when the view is NARROWED beyond this persona's defaults —
   *  i.e. the user actively picked a type / severity / vehicle.  Callers
   *  use it to tell "nothing matches your filters" apart from a genuine
   *  all-clear: a Safety persona lands on ``typeFilter='safety_events'``
   *  by default, so "not 'all'" alone would misread their default view as
   *  filtered and they'd never see the real all-clear. */
  narrowed: boolean;

  /** Restore persona defaults — wipes URL filters then writes them. */
  resetToDefaults: () => void;
}

function readDefaults(params: URLSearchParams, defaults: FilterDefaults): {
  typeFilter: AlertType;
  severityFilter: AlertSeverity;
  ackState: AlertAckState;
  vehicleSearch: string;
  days: number;
  page: number;
} {
  const typeFilter = (params.get('typeFilter') as AlertType) || defaults.typeFilter;
  const severityFilter =
    (params.get('severityFilter') as AlertSeverity) || defaults.severityFilter;
  const ackState = (params.get('ackState') as AlertAckState) || defaults.ackState;
  const vehicleSearch = params.get('vehicleSearch') ?? defaults.vehicleSearch;
  const daysRaw = parseInt(params.get('days') ?? '', 10);
  const days = Number.isFinite(daysRaw) && daysRaw > 0 ? daysRaw : defaults.days;
  const pageRaw = parseInt(params.get('page') ?? '', 10);
  const page = Number.isFinite(pageRaw) && pageRaw >= 1 ? pageRaw : 1;
  return { typeFilter, severityFilter, ackState, vehicleSearch, days, page };
}

/**
 * Persona defaults written over ``prev``, PRESERVING any param this hook
 * doesn't own.
 *
 * Building a fresh URLSearchParams here silently dropped every foreign
 * param — including ``?alertId``, which the notification bell links with.
 * Landing on /alerts from the bell writes defaults on first load, so the
 * deep link was erased a tick after arriving and the alert never opened.
 * Only the keys below are ours to overwrite.
 */
function writeDefaults(
  defaults: FilterDefaults,
  prev?: URLSearchParams,
): URLSearchParams {
  const next = new URLSearchParams(prev);
  next.delete('vehicleSearch');   // re-added below only when non-empty
  next.set('typeFilter', defaults.typeFilter);
  next.set('severityFilter', defaults.severityFilter);
  next.set('ackState', defaults.ackState);
  if (defaults.vehicleSearch) next.set('vehicleSearch', defaults.vehicleSearch);
  next.set('days', String(defaults.days));
  next.set('page', '1');
  return next;
}

export function useAlertsFilters(): AlertsFiltersAPI {
  const [params, setParams] = useSearchParams();
  const { persona } = useShellConfig();
  const defaults = resolveFilterDefaults(persona);

  // First-load default writer.  Runs once per persona; if the URL has
  // NO filter params at all, write the persona's defaults via replace
  // (preserves back-button).  We guard with a ref so a re-render
  // doesn't re-trigger after a user-driven URL change clears the
  // params back to zero (unusual but possible).
  const wroteDefaultsRef = useRef(false);
  useEffect(() => {
    if (wroteDefaultsRef.current) return;
    const hasAnyFilterParam = FILTER_PARAM_KEYS.some((k) => params.has(k));
    if (hasAnyFilterParam) {
      wroteDefaultsRef.current = true;
      return;
    }
    setParams((prev) => writeDefaults(defaults, prev), { replace: true });
    wroteDefaultsRef.current = true;
    // We intentionally do NOT include `defaults` in deps — they change
    // by persona, but a persona switch SHOULDN'T overwrite a URL the
    // user is already viewing.  resetToDefaults() is the explicit
    // affordance for that.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona]);

  const state = readDefaults(params, defaults);

  // Generic setter — writes one key + resets page to 1 (unless we're
  // setting page itself).  Other compound effects (selection clear on
  // ackState change) are handled at the call site so this hook stays
  // focused on URL-state.
  const setParam = useCallback(
    (key: FilterParamKey, value: string | number | undefined, opts?: { resetPage?: boolean }) => {
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        if (value === undefined || value === '' || value === null) {
          next.delete(key);
        } else {
          next.set(key, String(value));
        }
        // Default: any filter change resets the page.  Caller can
        // opt-out (e.g. setPage itself doesn't reset).
        if (opts?.resetPage !== false && key !== 'page') {
          next.set('page', '1');
        }
        return next;
      });
    },
    [setParams],
  );

  return {
    ...state,
    // Compared against the PERSONA defaults, not against 'all' — see the
    // `narrowed` doc on AlertsFiltersAPI.  ``days`` is excluded: a window
    // is always set, so it never means "the user narrowed this".
    narrowed:
      state.typeFilter !== defaults.typeFilter
      || state.severityFilter !== defaults.severityFilter
      || state.vehicleSearch.trim() !== defaults.vehicleSearch.trim(),
    setTypeFilter: (v) => setParam('typeFilter', v),
    setSeverityFilter: (v) => setParam('severityFilter', v),
    // NOTE: changing ack-state changes which rows are "ackable", so
    // the caller should ALSO clear the selection set via
    // useAlertsSelection().clearSelection().  Kept as the caller's
    // responsibility (rather than baked into this hook) because
    // selection lives in a separate Context and this hook stays
    // single-concern.
    setAckState: (v) => setParam('ackState', v),
    setVehicleSearch: (v) => setParam('vehicleSearch', v),
    setDays: (v) => setParam('days', v),
    setPage: (v) => setParam('page', v, { resetPage: false }),
    resetToDefaults: () => {
      // Clearing FILTERS, not the page: an open drawer's ``?alertId``
      // survives, because "clear all filters" never meant "close what
      // I'm reading".
      setParams((prev) => writeDefaults(defaults, prev), { replace: true });
    },
  };
}
