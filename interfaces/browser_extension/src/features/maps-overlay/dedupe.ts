/**
 * One answer for every tab that asks the same question.
 *
 * The overlay runs once per google.com/maps TAB, and each copy polls on
 * its own clock — positions every five seconds, the list every thirty.
 * A person with three route tabs open was therefore three times the
 * traffic for one fleet's worth of trucks, all of it answered with
 * identical numbers.
 *
 * Two rules, and they cover different halves of the waste:
 *
 *   SINGLE FLIGHT — while a request is in the air, a second asker joins
 *   it rather than starting another.  Exact, no timing assumptions: two
 *   tabs whose polls land in the same instant cost one request.
 *
 *   A SHORT MEMORY — an answer is reused for a moment after it arrives.
 *   The window is shorter than the poll it serves, so a single tab's own
 *   cadence is untouched and never sees a staler number than it did
 *   before; it exists so tabs on offset clocks share.
 *
 * A failure is never remembered.  Every waiter gets the same rejection
 * and the next poll asks again — a cached error would turn one bad
 * moment into a minute of blank map.
 *
 * AND AN ANSWER BELONGS TO WHOEVER IT WAS FETCHED FOR.  Every ask names
 * the token it is asking with, and an answer is only ever handed back
 * to that same token; a different one empties the memory and asks the
 * API itself.  This is checked HERE, on the way in, rather than by
 * telling the cache when a token changed — the two moments that change
 * it, disconnecting and a routine refresh, both happen in the PANEL,
 * which is a different context from this one and cannot reach these
 * maps at all.  A cache that had to be told would never have been told.
 */

export interface Shared<T> {
  (key: string, token: string, fetcher: () => Promise<T>): Promise<T>;
}

export function makeShared<T>(ttlMs: number, now: () => number = () => Date.now()): Shared<T> {
  const answered = new Map<string, { at: number; value: T }>();
  const inflight = new Map<string, Promise<T>>();
  /** Whose answers these are.  Compared whole: two tokens being the
   *  same person is a guess, and guessing is the thing this prevents. */
  let owner: string | null = null;

  return function shared(key: string, token: string, fetcher: () => Promise<T>): Promise<T> {
    if (owner !== token) {
      owner = token;
      answered.clear();
      inflight.clear();
    }
    const hit = answered.get(key);
    if (hit && now() - hit.at < ttlMs) return Promise.resolve(hit.value);
    const running = inflight.get(key);
    if (running) return running;
    const mine = token;
    // Initialised, so the cleanup below can compare identities without
    // the compiler having to prove the assignment happened first — it
    // has, by the time anything awaits.
    let flight: Promise<T> | null = null;
    flight = (async () => {
      try {
        const value = await fetcher();
        // The asker who started this is entitled to the answer either
        // way; it is only KEPT while it still belongs to somebody.
        if (owner === mine) answered.set(key, { at: now(), value });
        return value;
      } finally {
        // Only ever clear our own: after an owner change the map holds
        // the NEW owner's flight, and evicting that would send a third
        // tab off to make a duplicate request.
        if (inflight.get(key) === flight) inflight.delete(key);
      }
    })();
    inflight.set(key, flight);
    return flight;
  };
}
