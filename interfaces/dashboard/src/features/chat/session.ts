import { ApiError, chatApi, chatSocketURL, errorKey, type ChatApi } from './api';
import { mergeMessages, sameScope } from './model';
import type { Batch, Conversation, Draft, Message, PersonalState, Scope, Summary } from './types';
export interface Room {
  conversation: Conversation; messages: Message[]; before: number | null; browsingHistory: boolean;
  pins: Message[]; state: PersonalState; draft: Draft; scope: Scope; cursor: number; epoch: number;
}
export interface ChatState {
  summaries: Summary[]; next: string | null; inboxLoading: boolean; selected: string | null;
  room: Room | null; accessDenied: boolean; loading: boolean; error: string | null; connection: 'connecting' | 'live' | 'polling' | 'offline' | 'paused';
}
/** One short-lived store per real account/user. No message content in browser storage or shared query caches. */
export class ChatSession {
  private state: ChatState = { summaries: [], next: null, inboxLoading: true, selected: null, room: null, accessDenied: false, loading: false, error: null, connection: 'connecting' };
  private listeners = new Set<() => void>();
  private stopped = false;
  private lifetime = new AbortController();
  private roomAbort = new AbortController();
  private generation = 0;
  private epoch = 0;
  private inboxRequest = 0;
  private socket: WebSocket | null = null;
  private pollTimer?: ReturnType<typeof setTimeout>;
  private inboxTimer?: ReturnType<typeof setTimeout>;
  private reconnectAt = 0;
  private attempts = 0;
  private revision: number | null = null;
  private lastFrame = 0;
  private readyAt = 0;
  private work: Promise<void> = Promise.resolve();
  private polling = false;
  private queuedFrames = 0;
  private historyRequest = false;
  constructor(readonly api: ChatApi = chatApi, private socketFactory: ((url: string) => WebSocket) | null = url => new WebSocket(url)) {}
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => this.listeners.delete(fn); };
  getSnapshot = () => this.state;
  private set(patch: Partial<ChatState>) { if (this.stopped) return; this.state = { ...this.state, ...patch }; this.listeners.forEach(fn => fn()); }
  private active(g: number) { return !this.stopped && g === this.generation && !this.roomAbort.signal.aborted; }
  private enqueue(fn: () => Promise<void>) {
    this.work = this.work.then(async () => { if (!this.stopped) await fn(); }).catch(error => this.fail(error));
    return this.work;
  }
  private fail(error: unknown, roomOnly = false) {
    if (this.stopped || (error instanceof DOMException && error.name === 'AbortError')) return;
    if (error instanceof ApiError && [401, 402, 403].includes(error.status)) {
      this.generation++; this.inboxRequest++; this.roomAbort.abort(); this.lifetime.abort(); this.closeSocket();
      this.set({ summaries: [], next: null, room: null, accessDenied: true, loading: false, inboxLoading: false, error: 'unavailable', connection: 'paused' });
      return;
    }
    if (roomOnly && error instanceof ApiError && error.status === 404) { this.remove(); return; }
    this.set({ error: errorKey(error), loading: false, inboxLoading: false });
  }
  start() {
    void this.refreshInbox(); this.connect(); this.schedulePoll();
    document.addEventListener('visibilitychange', this.visibility);
    window.addEventListener('online', this.online);
    window.addEventListener('offline', this.offline);
  }
  stop() {
    this.stopped = true; this.generation++; this.lifetime.abort(); this.roomAbort.abort(); this.closeSocket();
    clearTimeout(this.pollTimer); clearTimeout(this.inboxTimer);
    document.removeEventListener('visibilitychange', this.visibility); window.removeEventListener('online', this.online); window.removeEventListener('offline', this.offline);
    this.listeners.clear();
  }
  private visibility = () => {
    if (document.hidden) { this.closeSocket(); this.set({ connection: 'paused' }); }
    else { this.set({ connection: 'connecting' }); void this.poll(true); this.connect(); }
  };
  private offline = () => { this.closeSocket(); this.set({ connection: 'offline' }); };
  private online = () => { this.reconnectAt = 0; void this.poll(true); this.connect(); };
  private closeSocket() { const socket = this.socket; this.socket = null; if (socket) { socket.onclose = null; socket.close(); } }
  private connect() {
    if (this.stopped || this.lifetime.signal.aborted || document.hidden || !navigator.onLine || !this.socketFactory || this.socket || Date.now() < this.reconnectAt) return;
    try {
      const socket = this.socketFactory(chatSocketURL()); this.socket = socket; this.lastFrame = Date.now();
      socket.onmessage = ({ data }) => {
        if (this.socket !== socket || this.stopped) return;
        this.lastFrame = Date.now();
        let frame: Batch & { type: string; revision: number; protocol: number };
        try { frame = JSON.parse(String(data)); } catch { socket.close(1008); return; }
        if (frame.type === 'ready') {
          if (frame.protocol !== 1) { socket.close(1008); return; }
          this.readyAt = Date.now(); this.set({ connection: 'live' }); this.sendSubscription();
        } else if (frame.type === 'sync') {
          if (frame.revision !== this.revision) { this.revision = frame.revision; this.scheduleInbox(); }
        } else if (frame.type === 'removed' && frame.conversation_id === this.state.selected) this.remove();
        else if (frame.type === 'events' && frame.conversation_id === this.state.selected) {
          const g = this.generation;
          // Bound retained content. HTTP resumes from the last fully applied cursor if a slow client falls behind.
          if (this.queuedFrames >= 8) { socket.close(1013); return; }
          this.queuedFrames++;
          void this.enqueue(async () => {
            try { if (this.active(g)) await this.apply(frame, g); }
            catch (error) { if (this.active(g)) { this.fail(error, true); this.closeSocket(); this.set({ connection: 'polling' }); } }
            finally { this.queuedFrames--; }
          });
        }
      };
      socket.onclose = () => {
        if (this.socket !== socket || this.stopped) return;
        this.socket = null;
        if (Date.now() - this.readyAt > 30000) this.attempts = 0;
        this.reconnectAt = Date.now() + Math.min(60000, 2000 * 2 ** Math.min(this.attempts++, 5)) * (1 + Math.random() * .3);
        this.set({ connection: navigator.onLine ? 'polling' : 'offline' }); void this.poll(true);
      };
      socket.onerror = () => socket.close();
    } catch { this.socket = null; this.reconnectAt = Date.now() + 10000; this.set({ connection: 'polling' }); }
  }
  private sendSubscription() {
    if (this.socket?.readyState !== 1) return;
    this.socket.send(JSON.stringify({ type: 'subscribe', conversations: this.state.selected ? [{ id: this.state.selected, ...(this.state.room ? { after: this.state.room.cursor } : {}) }] : [] }));
  }
  private scheduleInbox() {
    if (this.inboxTimer || this.stopped) return;
    this.inboxTimer = setTimeout(() => { this.inboxTimer = undefined; if (!document.hidden) void this.refreshInbox(); }, 2500);
  }
  async refreshInbox(more = false) {
    if (this.lifetime.signal.aborted) return;
    const request = ++this.inboxRequest;
    const pages = more ? 1 : Math.max(1, Math.ceil(this.state.summaries.length / 50));
    let after = more ? this.state.next ?? undefined : undefined;
    let items = more ? this.state.summaries : [];
    this.set({ inboxLoading: true });
    try {
      let next: string | null = null;
      for (let i = 0; i < pages; i++) {
        const page = await this.api.bootstrap(after, this.lifetime.signal);
        if (request !== this.inboxRequest || this.stopped) return;
        items = [...new Map([...items, ...page.items].map(c => [c.id, c])).values()]; next = page.next_after;
        if (!next) break; after = next;
      }
      this.set({ summaries: items, next, inboxLoading: false, error: this.state.error === 'unavailable' || (this.state.selected && !this.state.room) ? this.state.error : null });
    } catch (error) { if (request === this.inboxRequest) this.fail(error); }
  }
  select(id: string | null) {
    if (id === this.state.selected && this.state.room) return;
    this.generation++; this.roomAbort.abort(); this.roomAbort = new AbortController();
    this.set({ selected: id, room: null, loading: !!id, error: null }); this.sendSubscription();
    if (!id || this.lifetime.signal.aborted) return;
    const g = this.generation;
    // Subscribe first. A checkpoint BEFORE snapshots closes the HTTP/live race.
    void this.enqueue(async () => { if (this.active(g)) await this.snapshot(id, g); });
  }
  private async snapshot(id: string, g: number, checkpoint?: Batch) {
    const signal = this.roomAbort.signal;
    try {
      const cp = checkpoint ?? await this.api.events(id, undefined, signal);
      if (!this.active(g)) return;
      // Scope changes also invalidate snippets and pending inbox reads, not just the thread.
      const previous = this.state.room;
      if (previous && !sameScope(previous.scope, cp.scope)) {
        this.inboxRequest++;
        this.set({ summaries: this.state.summaries.filter(c => c.id !== id) });
        this.scheduleInbox();
      }
      // Unmount drafts, quotes, search and management dialogs from the former interval.
      this.set({ room: null, loading: true });
      const [conversation, page, pins, state, draft] = await Promise.all([
        this.api.conversation(id, signal), this.api.messages(id, undefined, signal), this.api.pins(id, undefined, signal), this.api.state(id, signal), this.api.draft(id, signal),
      ]);
      if (!this.active(g)) return;
      this.set({ room: { conversation, messages: mergeMessages([], page.items, cp.scope.visible_after_message_seq), before: page.next_before, browsingHistory: false,
        pins: pins.items, state, draft, scope: cp.scope, cursor: cp.cursor, epoch: ++this.epoch }, loading: false, error: null });
      await this.catchup(g);
    } catch (error) { if (this.active(g)) this.fail(error, true); }
  }
  private async apply(batch: Batch, g: number): Promise<void> {
    const current = this.state.room;
    if (!current || !this.active(g)) return;
    // Old subscription responses cannot roll a new membership interval backwards.
    if (batch.cursor < current.cursor) return;
    if (!sameScope(current.scope, batch.scope) || (batch.resync_required && batch.cursor !== current.cursor)) {
      await this.snapshot(current.conversation.id, g, batch); return;
    }
    const messages = batch.items.flatMap(event => event.message ? [event.message] : []);
    const boundary = batch.scope.visible_after_message_seq;
    const kinds = batch.items.map(e => e.kind);
    let room: Room = { ...current, conversation: { ...current.conversation, caller: batch.caller },
      messages: current.browsingHistory ? mergeMessages(current.messages, messages.filter(m => current.messages.some(old => old.id === m.id)), boundary) : mergeMessages(current.messages, messages, boundary),
      pins: mergeMessages(current.pins, messages.filter(m => current.pins.some(p => p.id === m.id)), boundary).filter(m => !m.deleted_at),
    };
    if (room.draft.reply?.status === 'available') {
      const quote = room.draft.reply;
      const updated = messages.find(message => message.id === quote.id);
      if (updated) room.draft = { ...room.draft, reply: updated.deleted_at ? { status: 'unavailable' } : { ...quote, body: updated.body! } };
    }
    if (room.messages[0]?.message_seq > (current.messages[0]?.message_seq ?? Infinity)) room.before = room.messages[0].message_seq;
    // Publish message snapshots now; commit the journal cursor only after dependent resources succeed.
    this.set({ room });
    const id = room.conversation.id, signal = this.roomAbort.signal;
    const tasks: Promise<void>[] = [];
    const put = (patch: Partial<Room>) => { if (this.active(g) && this.state.room) { room = { ...this.state.room, ...patch }; this.set({ room }); } };
    if (kinds.some(k => !k.startsWith('message_') && !['state_changed', 'draft_changed'].includes(k))) tasks.push(this.api.conversation(id, signal).then(conversation => put({ conversation })));
    if (kinds.some(k => ['message_pinned', 'message_unpinned', 'message_deleted'].includes(k))) tasks.push(this.api.pins(id, undefined, signal).then(page => put({ pins: page.items })));
    if (kinds.includes('state_changed')) tasks.push(this.api.state(id, signal).then(state => put({ state })));
    if (kinds.includes('draft_changed')) tasks.push(this.api.draft(id, signal).then(draft => put({ draft })));
    await Promise.all(tasks);
    if (this.active(g) && this.state.room) this.set({ room: { ...this.state.room, cursor: batch.cursor }, error: null });
  }
  private async catchup(g: number) {
    // Bound a drain so backlog cannot starve navigation or consume the endpoint budget.
    for (let i = 0; i < 4 && this.active(g) && this.state.room; i++) {
      const room = this.state.room;
      const batch = await this.api.events(room.conversation.id, room.cursor, this.roomAbort.signal);
      if (!this.active(g)) return;
      await this.apply(batch, g);
      if (!batch.has_more || batch.resync_required) break;
    }
  }
  private schedulePoll() {
    if (this.stopped) return;
    this.pollTimer = setTimeout(async () => { await this.poll(); this.schedulePoll(); }, 2200 + Math.random() * 500);
  }
  private async poll(force = false) {
    if (this.stopped || this.polling || document.hidden || !navigator.onLine || this.lifetime.signal.aborted) return;
    if (this.socket && Date.now() - this.lastFrame > 30000) { this.closeSocket(); this.set({ connection: 'polling' }); }
    this.connect();
    if (this.state.connection === 'live' && !force) return;
    this.polling = true;
    try {
      const sync = await this.api.sync(this.lifetime.signal);
      if (sync.revision !== this.revision) { this.revision = sync.revision; this.scheduleInbox(); }
      const g = this.generation;
      await this.enqueue(async () => {
        if (!this.active(g)) return;
        try { if (this.state.room) await this.catchup(g); else if (this.state.selected && !this.state.error) await this.snapshot(this.state.selected, g); }
        catch (error) { if (this.active(g)) this.fail(error, true); }
      });
      if (this.state.connection !== 'live' && !this.lifetime.signal.aborted) this.set({ connection: 'polling' });
    } catch (error) { this.fail(error); }
    finally { this.polling = false; }
  }
  private remove() {
    const id = this.state.selected; this.generation++; this.inboxRequest++; this.roomAbort.abort();
    this.set({ room: null, summaries: this.state.summaries.filter(c => c.id !== id), loading: false, error: 'unavailable' });
  }
  isCurrent(id: string, epoch: number) { return !this.stopped && !this.roomAbort.signal.aborted && this.state.room?.conversation.id === id && this.state.room.epoch === epoch; }
  async refreshRoom() {
    const g = this.generation, id = this.state.selected;
    if (!id || !this.state.room) return;
    try {
      const conversation = await this.api.conversation(id, this.roomAbort.signal);
      if (this.active(g) && this.state.room) this.set({ room: { ...this.state.room, conversation } });
      this.scheduleInbox();
    } catch (error) { if (this.active(g)) this.fail(error, true); }
  }
  async history(latest = false) {
    const current = this.state.room, g = this.generation;
    if (!current || this.historyRequest || (!latest && current.before === null)) return;
    this.historyRequest = true;
    try {
      const page = await this.api.messages(current.conversation.id, latest ? undefined : current.before!, this.roomAbort.signal);
      if (this.active(g) && this.state.room?.epoch === current.epoch) this.set({ room: { ...this.state.room, messages: latest ? mergeMessages([], page.items, current.scope.visible_after_message_seq) : mergeMessages(this.state.room.messages, page.items, current.scope.visible_after_message_seq, 200).slice(0, 100), before: page.next_before, browsingHistory: !latest } });
    } catch (error) { if (this.active(g)) this.fail(error, true); }
    finally { this.historyRequest = false; }
  }
  /** Fence action results against room switches/rejoins and pass cancellation to HTTP. */
  async action<T>(operation: (signal: AbortSignal) => Promise<T>, apply?: (value: T, room: Room) => Partial<Room>): Promise<T> {
    const g = this.generation, epoch = this.state.room?.epoch;
    if (this.stopped || !this.state.room || this.roomAbort.signal.aborted) throw new DOMException('Stale room', 'AbortError');
    try {
      const value = await operation(this.roomAbort.signal);
      if (!this.active(g) || epoch !== this.state.room?.epoch || !this.state.room) throw new DOMException('Stale room', 'AbortError');
      if (apply) this.set({ room: { ...this.state.room, ...apply(value, this.state.room) } });
      this.scheduleInbox(); return value;
    } catch (error) {
      if (this.active(g) && error instanceof ApiError && [401, 402, 403, 404].includes(error.status)) this.fail(error, true);
      throw error;
    }
  }
  addMessage(message: Message) {
    const room = this.state.room; if (!room) return;
    if (room.browsingHistory) { void this.history(true); this.scheduleInbox(); return; }
    const messages = mergeMessages(room.messages, [message], room.scope.visible_after_message_seq);
    const before = messages[0]?.message_seq > (room.messages[0]?.message_seq ?? Infinity) ? messages[0].message_seq : room.before;
    this.set({ room: { ...room, messages, before } }); this.scheduleInbox();
  }
}
