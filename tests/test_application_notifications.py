"""Recruiting notices reach the SHARED inbox — the Applications bucket.

A new driver application writes ONE in-app row: a ``notification_inbox``
notice.  Both bells — the top-bar panel's **Applications** tab and the
Applications page's own bell — read it, so "read" means read everywhere
(the feature's ``application_notifications`` store is retired; see
features/applications/notifications.py).  Pins:

  • the category is registered TARGETED under the ``applications`` source
    (the source string is what the panel's tab filters on — a rename
    silently empties the tab),
  • recipients are resolved by the PERMISSION, never a role list,
  • the retired store is not written any more,
  • the notice deep-links to the application (the legacy table could not),
  • one channel failing never costs the other, and no failure escapes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import features.applications.service as svc
from features.applications.notifications import APPLICATION_RECEIVED
from capabilities.notifications.categories import TARGETED, get_category


class _FakeDB:
    """Enough platform DB for the fan-out: users, channel prefs, account."""

    def __init__(self, users, channels=None):
        self._users = users
        self._channels = channels or {}
        self.page_notices: list[dict] = []

    async def list_account_users(self, account_id):
        return self._users

    async def get_application_notify_channels(self, user_id):
        return self._channels.get(user_id, ["dashboard"])

    async def get_account(self, account_id):
        return SimpleNamespace(name="Blue Line Carriers")

    async def create_application_notification(self, account_id, user_id, **kw):
        # Retired: recorded only so a test can prove it is NOT called.
        self.page_notices.append({"user_id": user_id, **kw})


class _RecordingNotify:
    def __init__(self, raises=None):
        self.calls: list[dict] = []
        self._raises = raises

    async def __call__(self, db, account_id, user_id, content, *,
                       channels=None, correlation_key=""):
        self.calls.append({
            "account_id": account_id, "user_id": user_id, "content": content,
            "channels": list(channels or []), "correlation_key": correlation_key,
        })
        if self._raises:
            raise self._raises
        return []


def _user(uid, role, email=None):
    return SimpleNamespace(id=uid, role=role, email=email, telegram_id=None)


@pytest.fixture
def notify(monkeypatch):
    """Patch notify_user at its source module — service.py imports it
    inside the function body, so the capability module is the seam."""
    rec = _RecordingNotify()
    import capabilities.notifications as caps
    monkeypatch.setattr(caps, "notify_user", rec, raising=True)
    return rec


@pytest.fixture(autouse=True)
def _perms(monkeypatch):
    """can_manage_applications: recruiter yes, driver no — resolved through
    the account-permission SSOT the service actually calls."""
    async def fake_get_account_permissions(role, account_id):
        key = getattr(role, "value", role)
        return SimpleNamespace(can_manage_applications=(key == "recruiter"))

    import capabilities.permissions.roles as roles
    monkeypatch.setattr(roles, "get_account_permissions",
                        fake_get_account_permissions, raising=True)


class TestCategory:
    def test_registered_targeted_in_the_applications_source(self):
        cat = get_category(APPLICATION_RECEIVED)
        assert cat is not None, "category must register on import"
        assert cat.kind == TARGETED
        # The panel's Applications tab filters notices on this exact
        # string (source = the category key's namespace).
        assert cat.source == "applications"
        assert APPLICATION_RECEIVED == "applications.received"

    def test_not_mandatory(self):
        """Someone who lives on the Applications page may mute it."""
        assert get_category(APPLICATION_RECEIVED).mandatory is False


class TestFanOut:
    @pytest.mark.asyncio
    async def test_permission_holders_only(self, notify):
        db = _FakeDB([_user(1, "recruiter"), _user(2, "driver"),
                      _user(3, "recruiter")])
        await svc.notify_new_application(db, 42, 77, "APP-77", "Dana Driver")
        assert [c["user_id"] for c in notify.calls] == [1, 3]
        assert db.page_notices == [], "the retired store must stay unwritten"

    @pytest.mark.asyncio
    async def test_notice_content_and_deep_link(self, notify):
        db = _FakeDB([_user(1, "recruiter")])
        await svc.notify_new_application(db, 42, 77, "APP-77", "Dana Driver")
        call = notify.calls[0]
        content = call["content"]
        assert call["account_id"] == 42
        assert content.category == APPLICATION_RECEIVED
        # The reference rides the object chip (meta.context), not the
        # body — one row must not state it twice.
        assert content.body == "Dana Driver applied"
        assert content.meta["context"] == "APP-77"
        # Deep-link to the row, not just the page.
        assert content.url.endswith("/workforce/applications?app=77")
        assert content.meta["application_id"] == 77
        assert content.meta["reference"] == "APP-77"
        # In-app only: email/telegram keep the feature's own senders.
        assert call["channels"] == ["in_app"]
        # Correlation is per (application, recipient) — delivery
        # bookkeeping, NOT dedup: the in-app channel records no ledger
        # handle and the inbox has no uniqueness constraint, so calling
        # this twice for one application really would write two rows
        # (same as the page bell, email and telegram already do).
        assert call["correlation_key"] == "application:77:1"

    @pytest.mark.asyncio
    async def test_dashboard_channel_off_skips_the_in_app_notice(self, notify):
        db = _FakeDB([_user(1, "recruiter")], channels={1: ["email"]})
        await svc.notify_new_application(db, 42, 77, "APP-77", "Dana")
        assert notify.calls == []

    @pytest.mark.asyncio
    async def test_inbox_failure_is_swallowed(self, monkeypatch):
        """A notice failure must never affect a submission that succeeded —
        the fan-out is best-effort by contract, and this is now the only
        in-app store, so nothing is left to fall back on."""
        import capabilities.notifications as caps
        monkeypatch.setattr(caps, "notify_user",
                            _RecordingNotify(raises=RuntimeError("inbox down")),
                            raising=True)
        db = _FakeDB([_user(1, "recruiter")])
        await svc.notify_new_application(db, 42, 77, "APP-77", "Dana")   # no raise
