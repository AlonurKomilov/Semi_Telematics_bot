import { beforeEach } from 'vitest';
/** A chrome.* stand-in: storage as a Map, tabs recorded for assertion. */
const store = new Map<string, unknown>();
export const tabCalls: { update: unknown[]; create: unknown[] } = { update: [], create: [] };
export let activeTab: { id?: number; url?: string } | null = null;
export function setActiveTab(t: { id?: number; url?: string } | null) { activeTab = t; }

(globalThis as unknown as { chrome: unknown }).chrome = {
  storage: { local: {
    get: async (k: string) => ({ [k]: store.get(k) }),
    set: async (o: Record<string, unknown>) => { for (const [k, v] of Object.entries(o)) store.set(k, v); },
    remove: async (k: string) => { store.delete(k); },
  } },
  tabs: {
    query: async () => (activeTab ? [activeTab] : []),
    update: async (id: number, p: unknown) => { tabCalls.update.push([id, p]); },
    create: async (p: unknown) => { tabCalls.create.push(p); },
  },
  runtime: { onInstalled: { addListener: () => {} } },
  sidePanel: { setPanelBehavior: async () => {} },
};
beforeEach(() => { store.clear(); tabCalls.update.length = 0; tabCalls.create.length = 0; activeTab = null; });
