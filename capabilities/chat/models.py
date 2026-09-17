"""Framework-neutral Chat values and domain errors."""
from dataclasses import dataclass

ADMIN_ACTIONS = frozenset({
    'manage_info', 'manage_settings', 'manage_audience',
    'pin_messages', 'delete_messages', 'archive_group',
})
OWNER_ACTIONS = ADMIN_ACTIONS | {'manage_admins', 'transfer_ownership'}
MAX_ACTIVE_GROUPS = 100


class ChatError(Exception):
    """The HTTP adapter maps code to its normal error response."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Actor:
    """Only validated transport identity supplies these IDs; never request JSON."""
    account_id: int
    user_id: int


@dataclass(frozen=True)
class Access:
    group_role: str
    actions: frozenset[str]
    visible_after_message_seq: int

    def sees_message(self, message_seq: int) -> bool:
        return message_seq > self.visible_after_message_seq
