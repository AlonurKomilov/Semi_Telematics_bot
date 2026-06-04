/**
 * Uniform props every vehicle-detail section accepts.  The page passes
 * a single ``{ vehicleName }`` object via PageLayoutHost; each section
 * destructures what it needs and fetches its own data with useQuery.
 *
 * Keeping the prop surface minimal (just the URL identifier) means
 * sections are self-contained — adding a new section never requires
 * threading a new prop through the page.
 */
export interface VehicleSectionProps {
  vehicleName: string;
}
