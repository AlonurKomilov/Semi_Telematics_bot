/**
 * The one disclosure sentence every vendor-save surface shows next to
 * its Address field.  The directory pipeline is automatic — a vendor
 * whose address completes its identity flows to the platform review
 * queue with no click — so the moment of typing the address is where
 * the user must learn that.  Single source: the Add-vendor dialog,
 * the profile Edit dialog and the work-order vendor block all import
 * this string so the wording can't drift between surfaces (it matches
 * the profile page's "Sent for directory review" state text).
 */
export const DIRECTORY_DISCLOSURE =
  'Adding an address sends this shop to the shared 4truck directory for review — ' +
  'once verified it appears publicly (and on the map). Only its name and contact ' +
  'info are shared, never your invoices.';
