import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Dialog, DialogContent, DialogTitle, DialogDescription, DialogHeader, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Button } from '@/components/ui/button';
import { Radio } from '@/components/ui/radio';
import { Checkbox } from '@/components/ui/checkbox';
import { ScrollRegion } from '@/components/scrolling';
import { ErrorState } from '@/components/shell';
import { ROLE_LABEL } from '@/components/RoleBadge';
import { chatApi, errorKey } from './api';
import { PeoplePicker } from './PeoplePicker';
import type { Action, Admins, Conversation, Person } from './types';
const delegated: Action[] = ['manage_info', 'manage_settings', 'manage_audience', 'pin_messages', 'delete_messages', 'archive_group'];
export function GroupDialog({ conversation, ownRole, onClose, onSaved }: { conversation?: Conversation; ownRole: string; onClose: () => void; onSaved: (c: Conversation) => void }) {
  const { t } = useTranslation(), id = useId();
  const [base, setBase] = useState(conversation);
  const [title, setTitle] = useState(conversation?.title ?? ''), [description, setDescription] = useState(conversation?.description ?? '');
  const [kind, setKind] = useState<'account' | 'roles'>(conversation?.kind === 'roles' ? 'roles' : 'account');
  const [roles, setRoles] = useState(conversation?.role_keys ?? [ownRole]);
  const [posting, setPosting] = useState(conversation?.settings.posting_mode ?? 'everyone');
  const [history, setHistory] = useState(conversation?.settings.new_member_history ?? 'all');
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [admins, setAdmins] = useState<Admins | null>(null);
  const [person, setPerson] = useState<Person | null>(null), [actions, setActions] = useState<Action[]>([]), [picker, setPicker] = useState<'admin' | 'transfer' | null>(null);
  const [confirm, setConfirm] = useState<'archive' | 'transfer' | null>(null), [section, setSection] = useState<'info' | 'admins'>('info');
  const can = (a: Action) => !base || (conversation ?? base).caller.actions.includes(a);
  useEffect(() => {
    if (!base?.caller.actions.includes('manage_admins') || section !== 'admins') return;
    const controller = new AbortController();
    void chatApi.admins(base.id, controller.signal).then(setAdmins).catch(e => { if (!controller.signal.aborted) setError(errorKey(e)); });
    return () => controller.abort();
  }, [base?.id, base?.version, base?.caller.actions, section]);
  async function run(operation: () => Promise<Conversation>, close = false) {
    if (busy || error === 'conflict') return; setBusy(true); setError('');
    try { const result = await operation(); setBase(result); onSaved(result); setConfirm(null); setPerson(null); setPicker(null); if (close) onClose(); }
    catch (e) { setError(errorKey(e)); } finally { setBusy(false); }
  }
  async function save() {
    if (!base) return run(() => chatApi.create({ title, description, kind, role_keys: kind === 'roles' ? roles : [], posting_mode: posting, new_member_history: history }), true);
    const changes: Record<string, unknown> = {};
    if (can('manage_info')) { if (title !== base.title) changes.title = title; if (description !== base.description) changes.description = description; }
    if (can('manage_settings')) { if (posting !== base.settings.posting_mode) changes.posting_mode = posting; if (history !== base.settings.new_member_history) changes.new_member_history = history; }
    if (can('manage_audience') && kind === 'roles' && [...roles].sort().join() !== [...base.role_keys].sort().join()) changes.role_keys = roles;
    if (!Object.keys(changes).length) { onClose(); return; }
    await run(() => chatApi.update(base, changes), true);
  }
  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose(); }}><DialogContent size="lg" className="text-foreground">
    <DialogHeader><DialogTitle>{t(base ? 'chat.manageGroup' : 'chat.newGroup')}</DialogTitle><DialogDescription>{t('chat.groupHint')}</DialogDescription></DialogHeader>
    {base && can('manage_admins') && <div className="flex gap-2"><Button className="text-foreground" variant={section === 'info' ? 'secondary' : 'ghost'} aria-pressed={section === 'info'} onClick={() => setSection('info')}>{t('chat.settings')}</Button><Button className="text-foreground" variant={section === 'admins' ? 'secondary' : 'ghost'} aria-pressed={section === 'admins'} onClick={() => setSection('admins')}>{t('chat.admins')}</Button></div>}
    <ScrollRegion label={t('chat.manageGroup')} className="max-h-96 space-y-5 p-1">
      {section === 'info' && <>
        <label className="block space-y-2 text-sm text-foreground"><span>{t('chat.groupName')}</span><Input autoFocus value={title} maxLength={80} disabled={busy || !can('manage_info')} onChange={e => setTitle(e.target.value)} /></label>
        <label className="block space-y-2 text-sm text-foreground"><span>{t('chat.description')}</span><Textarea value={description} rows={2} maxLength={300} disabled={busy || !can('manage_info')} onChange={e => setDescription(e.target.value)} /></label>
        {!base && <fieldset className="space-y-2"><legend className="text-sm font-medium text-foreground">{t('chat.audience')}</legend>{(['account', 'roles'] as const).map(option => <label key={option} className="flex items-center gap-2 text-sm text-foreground"><Radio name={`${id}-audience`} checked={kind === option} disabled={busy} onChange={() => setKind(option)} />{t(`chat.${option}`)}</label>)}</fieldset>}
        {kind === 'roles' && <fieldset className="space-y-2" disabled={busy || !can('manage_audience')}><legend className="text-sm font-medium text-foreground">{t('chat.roles')}</legend><div className="grid grid-cols-2 gap-2">{Object.keys(ROLE_LABEL).map(role => <label key={role} className="flex items-center gap-2 text-sm text-foreground"><Checkbox checked={roles.includes(role)} onChange={e => setRoles(old => e.target.checked ? [...old, role] : old.filter(r => r !== role))} />{t(`roles.${role}`, { defaultValue: ROLE_LABEL[role] })}</label>)}</div><p className="text-xs text-muted-foreground">{t('chat.audienceHint')}</p></fieldset>}
        <fieldset className="space-y-2" disabled={busy || !can('manage_settings')}><legend className="text-sm font-medium text-foreground">{t('chat.posting')}</legend>{(['everyone', 'admins'] as const).map(option => <label key={option} className="flex items-center gap-2 text-sm text-foreground"><Radio name={`${id}-posting`} checked={posting === option} onChange={() => setPosting(option)} />{t(`chat.${option}`)}</label>)}</fieldset>
        <fieldset className="space-y-2" disabled={busy || !can('manage_settings')}><legend className="text-sm font-medium text-foreground">{t('chat.history')}</legend>{(['all', 'since_join'] as const).map(option => <label key={option} className="flex items-center gap-2 text-sm text-foreground"><Radio name={`${id}-history`} checked={history === option} onChange={() => setHistory(option)} />{t(`chat.${option}`)}</label>)}<p className="text-xs text-muted-foreground">{t('chat.historyHint')}</p></fieldset>
        {base && can('archive_group') && !base.system_key && <Button className="text-foreground" variant="outline" disabled={busy} onClick={() => setConfirm('archive')}>{t(base.archived ? 'chat.reopenGroup' : 'chat.archiveGroup')}</Button>}
      </>}
      {section === 'admins' && base && can('manage_admins') && <>
        <p className="text-sm text-muted-foreground">{t('chat.adminHint')}</p>
        {admins?.items.map(admin => <Button key={admin.user_id} variant="outline" className="text-foreground w-full justify-start" disabled={busy} onClick={() => { setPerson({ id: admin.user_id, display_name: admin.display_name, role: '' }); setActions(admin.actions); setPicker('admin'); }}>{admin.display_name}</Button>)}
        <div className="flex flex-wrap gap-2"><Button variant="secondary" disabled={busy} onClick={() => { setPerson(null); setActions([]); setPicker('admin'); }}>{t('chat.addAdmin')}</Button>{can('transfer_ownership') && <Button className="text-foreground" variant="outline" disabled={busy} onClick={() => { setPerson(null); setPicker('transfer'); }}>{t('chat.transfer')}</Button>}</div>
        {picker && !person && <PeoplePicker conversationId={base.id} exclude={[base.owner_user_id!]} onPick={p => { setPerson(p); if (picker === 'transfer') setConfirm('transfer'); }} />}
        {person && picker === 'admin' && <fieldset disabled={busy} className="space-y-3"><legend className="text-sm font-medium text-foreground">{person.display_name}</legend>{delegated.filter(a => a !== 'manage_audience' || base.kind === 'roles').map(action => <label key={action} className="flex items-center gap-2 text-sm text-foreground"><Checkbox checked={actions.includes(action)} onChange={e => setActions(old => e.target.checked ? [...old, action] : old.filter(a => a !== action))} />{t(`chat.action_${action}`)}</label>)}<div className="flex gap-2"><Button onClick={() => void run(() => chatApi.setAdmin(base.id, base.version, person.id, actions))}>{t('chat.save')}</Button>{admins?.items.some(a => a.user_id === person.id) && <Button className="text-foreground" variant="outline" onClick={() => void run(() => chatApi.removeAdmin(base.id, base.version, person.id))}>{t('chat.removeAdmin')}</Button>}<Button className="text-foreground" variant="ghost" onClick={() => setPerson(null)}>{t('chat.cancel')}</Button></div></fieldset>}
      </>}
      {confirm && base && <div className="space-y-3 rounded-lg border border-border p-3 text-foreground"><p className="text-sm">{t(confirm === 'transfer' ? 'chat.transferWarning' : 'chat.archiveWarning', { name: person?.display_name })}</p><div className="flex gap-2"><Button disabled={busy} onClick={() => void run(() => confirm === 'transfer' ? chatApi.transfer(base, person!.id) : chatApi.update(base, { archived: !base.archived }), true)}>{t('chat.confirm')}</Button><Button className="text-foreground" variant="outline" disabled={busy} onClick={() => { setConfirm(null); setPerson(null); }}>{t('chat.cancel')}</Button></div></div>}
      {error && <ErrorState message={t(`chat.${error}`)} />}
      {error === 'conflict' && <Button className="text-foreground" variant="outline" onClick={onClose}>{t('chat.closeReload')}</Button>}
    </ScrollRegion>
    <DialogFooter><Button className="text-foreground" variant="outline" disabled={busy} onClick={onClose}>{t('chat.cancel')}</Button>{section === 'info' && <Button disabled={busy || !title.trim() || (kind === 'roles' && !roles.length) || error === 'conflict'} onClick={() => void save()}>{t(busy ? 'chat.saving' : 'chat.save')}</Button>}</DialogFooter>
  </DialogContent></Dialog>;
}
