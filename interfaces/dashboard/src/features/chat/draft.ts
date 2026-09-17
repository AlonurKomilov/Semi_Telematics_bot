import type { Draft } from './types';
export interface DraftValue { body: string; reply_to_id: number | null }
export type DraftStatus = 'saved' | 'unsaved' | 'saving' | 'conflict' | 'error';
/** Serial compare-and-swap saves. A remote clear must never be resurrected by this device. */
export class DraftBuffer {
  value: DraftValue;
  version: number;
  status: DraftStatus = 'saved';
  private edit = 0;
  private savedEdit = 0;
  private running: Promise<void> | null = null;
  private remote: Draft | null = null;
  constructor(draft: Draft, private save: (value: DraftValue & { expected_version: number }) => Promise<Draft>, private changed: () => void) {
    this.value = { body: draft.body, reply_to_id: draft.reply?.status === 'available' ? draft.reply.id : null }; this.version = draft.version;
  }
  update(value: DraftValue) { this.value = value; this.edit++; if (this.status !== 'conflict') this.status = 'unsaved'; this.changed(); }
  receive(draft: Draft) {
    if (draft.version <= this.version) return;
    if (this.running) { if (!this.remote || this.remote.version < draft.version) this.remote = draft; return; }
    if (this.edit !== this.savedEdit) { this.remote = draft; this.status = 'conflict'; this.changed(); return; }
    this.replace(draft);
  }
  private replace(draft: Draft) { this.version = draft.version; this.value = { body: draft.body, reply_to_id: draft.reply?.status === 'available' ? draft.reply.id : null }; this.savedEdit = this.edit; this.status = 'saved'; this.remote = null; this.changed(); }
  useRemote(draft: Draft) { this.replace(draft); }
  async flush(): Promise<void> {
    if (this.running) { await this.running; return this.flush(); }
    if (this.status === 'conflict') throw new Error('draft_conflict');
    if (this.edit === this.savedEdit) return;
    const edit = this.edit, value = { ...this.value, expected_version: this.version };
    this.status = 'saving'; this.changed();
    this.running = this.save(value).then(draft => { this.version = draft.version; this.savedEdit = edit; this.status = this.edit === edit ? 'saved' : 'unsaved'; }).catch((error: unknown) => {
      this.status = error instanceof Error && 'status' in error && error.status === 409 ? 'conflict' : 'error'; throw error;
    }).finally(() => {
      this.running = null;
      if (this.remote) { const remote = this.remote; this.remote = null; this.receive(remote); }
      this.changed();
    });
    await this.running;
    if (this.edit !== this.savedEdit && (this.status as DraftStatus) !== 'conflict') await this.flush();
  }
}
