/**
 * Uniform props every vehicle-detail section accepts.  The page passes
 * a single object via PageLayoutHost; each section destructures what
 * it needs and fetches its own data with useQuery.
 *
 * ``company`` disambiguates cross-company vehicle-name collisions.
 * Two trucks with the same name (e.g. "103") can exist legitimately
 * under one account when the account has multiple companies; without
 * the company qualifier, the ``/vehicles/{name}`` endpoint returns
 * BOTH matches and the section silently picks the wrong one.  The
 * parent reads the qualifier from the URL search params (``?company=``)
 * and threads it here.
 *
 * Keeping the prop surface minimal means sections are self-contained
 * — adding a new section never requires threading a new prop through
 * the page.
 */
export interface VehicleSectionProps {
  vehicleName: string;
  company?: string;
}
