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

/**
 * The /mods PAGE — the same settings drawn as depth: a hub of
 * categories, a category as a grid of items, an item as its control.
 * It was deleted once on the reasoning that a page is a catalogue and
 * there was nothing to browse; that read the GX screenshots wrong. GX's
 * page is where you see what you HAVE, and the store is a button on it.
 */
export const MODS_PAGE_HREF = '/mods';
