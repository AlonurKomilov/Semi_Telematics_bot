/**
 * Where the Modifications surface lives — the one constant three doors
 * share: the top-bar popover's link, the avatar menu, and the /mods
 * redirect.
 *
 * Its own module, importing nothing, for two reasons. It breaks the
 * cycle ModPanel <-> Modifications would otherwise form (the panel needs
 * the href, the section needs the panel's controls). And when /mods
 * comes back as the CATALOGUE, a door still pointing at /mods would
 * silently retarget — one constant means that cannot happen quietly.
 */
export const MODS_HREF = '/profile#modifications';
