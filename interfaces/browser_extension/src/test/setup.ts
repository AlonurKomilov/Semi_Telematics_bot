import { beforeEach } from 'vitest';
/** A chrome.* stand-in: storage as a Map, tabs recorded for assertion. */
const store = new Map<string, unknown>();
export const tabCalls: { update: unknown[]; create: unknown[] } = { update: [], create: [] };
export let activeTab: { id?: number; url?: string } | null = null;
export function setActiveTab(t: { id?: number; url?: string } | null) { activeTab = t; }

const sessionStore = new Map<string, unknown>();
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
  runtime: { onInstalled: { addListener: () => {} }, onMessageExternal: { addListener: () => {} } },
  sidePanel: { setPanelBehavior: async () => {} },
};
beforeEach(() => { store.clear(); sessionStore.clear(); tabCalls.update.length = 0; tabCalls.create.length = 0; activeTab = null; });
