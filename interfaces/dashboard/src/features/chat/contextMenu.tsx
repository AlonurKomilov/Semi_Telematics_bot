import type { TFunction } from 'i18next';
import type { MenuAction } from '@/components/ui/context-menu';
import { Undo2, Pencil, Pin, Trash2 } from '@/lib/icons';
import type { Action, Conversation, Message, PersonalState } from './types';
export type MessageAction = 'reply' | 'edit' | 'pin' | 'delete';
export function messageActions(t: TFunction, grants: readonly Action[], message: Message, pinned: boolean, onAction: (action: MessageAction) => void): MenuAction[] {
  if (message.deleted_at) return [];
  const out: MenuAction[] = [];
  if (grants.includes('send')) out.push({ key: 'reply', label: t('chat.reply'), icon: <Undo2 className="size-4" />, onSelect: () => onAction('reply') });
  if (message.can_edit && grants.includes('send')) out.push({ key: 'edit', label: t('chat.edit'), icon: <Pencil className="size-4" />, onSelect: () => onAction('edit') });
  if (grants.includes('pin_messages')) out.push({ key: 'pin', label: t(pinned ? 'chat.unpin' : 'chat.pin'), icon: <Pin className="size-4" />, onSelect: () => onAction('pin') });
  if (message.can_delete && (grants.includes('send') || grants.includes('delete_messages'))) out.push({ key: 'delete', label: t('chat.delete'), icon: <Trash2 className="size-4" />, danger: true, separatorBefore: true, onSelect: () => onAction('delete') });
  return out;
}

export function conversationActions(t: TFunction, conversation: Conversation, state: PersonalState, personal: (key: 'muted' | 'pinned' | 'archived') => void, manage: () => void): MenuAction[] {
  const actions: MenuAction[] = [
    { key: 'mute', label: t(state.muted ? 'chat.unmute' : 'chat.mute'), onSelect: () => personal('muted') },
    { key: 'pin', label: t(state.pinned ? 'chat.unpinChat' : 'chat.pinChat'), onSelect: () => personal('pinned') },
    { key: 'archive', label: t(state.archived ? 'chat.unarchive' : 'chat.archive'), onSelect: () => personal('archived') },
  ];
  if (conversation.kind !== 'direct' && conversation.caller.actions.some(a => a.startsWith('manage_') || a === 'archive_group')) actions.unshift({ key: 'manage', label: t('chat.manageGroup'), onSelect: manage });
  return actions;
}
