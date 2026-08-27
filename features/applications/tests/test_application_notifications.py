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
  • the notice deep-links to the application (the legacy table could not),
  • DELIVERY is the capability's: one call names all three personal
    channels and no feature-side channel gate remains, so connection
    state and category mutes are what decide,
  • no failure escapes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import features.applications.service as svc
from features.applications.notifications import APPLICATION_RECEIVED
from capabilities.notifications.categories import TARGETED, get_category


class _FakeDB:
    """Enough platform DB for the fan-out: users and the account.

    Deliberately carries NO stand-in for the two retired stores (dropped
    in migration 190): if a feature-side notice write or channel gate ever
    creeps back, this fake raises AttributeError rather than quietly
    absorbing the call.
    """

    def __init__(self, users):
        self._users = users

    async def list_account_users(self, account_id):
        return self._users

    async def get_account(self, account_id):
        return SimpleNamespace(name="Blue Line Carriers")


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


def _user(uid, role, email=None, is_manager=False):
    return SimpleNamespace(id=uid, role=role, email=email, telegram_id=None,
                           is_manager=is_manager)


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
    """can_manage_applications through the USER resolver the service calls:
    recruiter always, HR only at the senior tier (the real shape — HR's
    base role lacks the flag and TIER_GRANTS adds it), driver never."""
    async def fake_get_user_permissions(role, account_id, is_manager=False, **kw):
        key = getattr(role, "value", role)
        return SimpleNamespace(can_manage_applications=(
            key == "recruiter" or (key == "hr" and is_manager)))

    import capabilities.permissions.roles as roles
    monkeypatch.setattr(roles, "get_user_permissions",
                        fake_get_user_permissions, raising=True)


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

    @pytest.mark.asyncio
    async def test_manager_tier_is_audience_but_the_base_role_is_not(self, notify):
        """HR holds can_manage_applications only through its senior tier,
        so the fan-out has to resolve the USER (role + tier).  Resolving
        the base role alone skipped every HR team lead — the people who
        actually run the hiring queue."""
        db = _FakeDB([_user(1, "hr"), _user(2, "hr", is_manager=True)])
        await svc.notify_new_application(db, 42, 77, "APP-77", "Dana")
        assert [c["user_id"] for c in notify.calls] == [2]

    @pytest.mark.asyncio
    async def test_notice_content_and_deep_link(self, notify):
        db = _FakeDB([_user(1, "recruiter")])
        await svc.notify_new_application(db, 42, 77, "APP-77", "Dana Driver")
        call = notify.calls[0]
        content = call["content"]
        assert call["account_id"] == 42
        assert content.category == APPLICATION_RECEIVED
        # The title carries the applicant — it is the email SUBJECT, and
        # a dozen identical subjects can't be triaged from an inbox list.
        assert content.title == "New driver application — Dana Driver"
        # The body carries the reference, so every channel shows it (the
        # in-app object chip was invisible in mail and chat).
        assert content.body == "Reference APP-77"
        # Deep-link to the row, not just the page — and the same target
        # declared as an ACTION, which the inbox row and the email both
        # render as a labelled button ("Review application").
        assert content.url.endswith("/workforce/applications?app=77")
        assert content.meta["action"] == {
            "label": "Review application",
            "url": "/workforce/applications?app=77",
        }
        assert content.meta["application_id"] == 77
        assert content.meta["reference"] == "APP-77"
        # Every personal channel is offered — the capability decides
        # which of them this person is actually reachable on.
        assert call["channels"] == ["in_app", "email", "telegram_dm"]
        # Correlation is per (application, recipient) — delivery
        # bookkeeping, NOT dedup: the in-app channel records no ledger
        # handle and the inbox has no uniqueness constraint, so calling
        # this twice for one application really would write two rows
        # (same as the page bell, email and telegram already do).
        assert call["correlation_key"] == "application:77:1"

    @pytest.mark.asyncio
    async def test_no_feature_side_channel_gate_remains(self, notify):
        """The feature's own 3-way channel pref is gone: muting belongs to
        the notification matrix, which the capability reads.  A second
        gate here would mean two stores disagreeing about one event.

        The fake exposes no ``get_application_notify_channels`` at all, so
        a surviving gate raises AttributeError instead of quietly reading
        a store that no longer exists."""
        db = _FakeDB([_user(1, "recruiter")])
        await svc.notify_new_application(db, 42, 77, "APP-77", "Dana")
        assert len(notify.calls) == 1

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
