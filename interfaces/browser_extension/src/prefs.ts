/**
 * The panel's own preferences — one storage mechanism for all of them.
 *
 * Each is a boolean a person set once and expects to find again: the
 * panel is a strip beside their work, and re-making the same choice
 * every morning is the fastest way to make it feel disposable.
 *
 * ``chrome.storage.local`` (not ``session``): the choice outlives the
 * browser, unlike the connect ``state``, which must not.
 */
export async function getFlag(key: string, fallback: boolean): Promise<boolean> {
  try {
    const got = await chrome.storage.local.get(key);
    const v = got[key];
    return typeof v === 'boolean' ? v : fallback;
  } catch {
    // A storage read must never be the reason a panel does not open.
    return fallback;
  }
}

export async function setFlag(key: string, value: boolean): Promise<void> {
  try {
    await chrome.storage.local.set({ [key]: value });
  } catch { /* the choice is lost, the session is not */ }
}
