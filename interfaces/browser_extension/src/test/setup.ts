import { beforeEach } from 'vitest';
/** A chrome.* stand-in: storage as a Map, tabs recorded for assertion. */
const store = new Map<string, unknown>();
export const tabCalls: { update: unknown[]; create: unknown[] } = { update: [], create: [] };
export let activeTab: { id?: number; url?: string } | null = null;
export function setActiveTab(t: { id?: number; url?: string } | null) { activeTab = t; }

const sessionStore = new Map<string, unknown>();

/** What `chrome.runtime.getManifest()` answers.  `key` is the channel
 *  marker: the store package has none, the sideload one carries it. */
const DEFAULT_MANIFEST: { version: string; key?: string } = { version: '0.5.19.0' };
let manifest: { version: string; key?: string } = { ...DEFAULT_MANIFEST };
export function setManifest(m: { version: string; key?: string }) { manifest = m; }

/** What `chrome.runtime.requestUpdateCheck()` answers — or throws, which
 *  is what an unpacked extension really does. */
let updateCheck: { status: string } | Error = { status: 'no_update' };
export function setUpdateCheck(r: { status: string } | Error) { updateCheck = r; }
(globalThis as unknown as { chrome: unknown }).chrome = {
  storage: {
    local: {
      get: async (k: string) => ({ [k]: store.get(k) }),
      set: async (o: Record<string, unknown>) => { for (const [k, v] of Object.entries(o)) store.set(k, v); },
      remove: async (k: string) => { store.delete(k); },
    },
    session: {
      get: async (k: string) => ({ [k]: sessionStore.get(k) }),
      set: async (o: Record<string, unknown>) => { for (const [k, v] of Object.entries(o)) sessionStore.set(k, v); },
      remove: async (k: string) => { sessionStore.delete(k); },
    },
    onChanged: { addListener: () => {}, removeListener: () => {} },
  },
  tabs: {
    query: async () => (activeTab ? [activeTab] : []),
    update: async (id: number, p: unknown) => { tabCalls.update.push([id, p]); },
    create: async (p: unknown) => { tabCalls.create.push(p); },
  },
  runtime: {
    onInstalled: { addListener: () => {} },
    onMessageExternal: { addListener: () => {} },
    id: 'test-extension-id',
    getManifest: () => manifest,
    requestUpdateCheck: async () => {
      if (updateCheck instanceof Error) throw updateCheck;
      return updateCheck;
    },
  },
  sidePanel: { setPanelBehavior: async () => {} },
};
beforeEach(() => {
  store.clear(); sessionStore.clear();
  tabCalls.update.length = 0; tabCalls.create.length = 0; activeTab = null;
  manifest = { ...DEFAULT_MANIFEST };
  updateCheck = { status: 'no_update' };
});
