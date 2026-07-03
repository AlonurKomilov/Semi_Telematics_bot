"""Derived member sign-in lifecycle (Team Management).

A provisioned user (imported from an integration / added by a manager) is
"pending" until they can actually sign in; deactivation wins over all.
"""

from __future__ import annotations

from types import SimpleNamespace

from features.settings.team_management.service import member_lifecycle


def _u(**kw):
    return SimpleNamespace(
        is_active=kw.get("is_active", True),
        telegram_id=kw.get("telegram_id"),
        password_hash=kw.get("password_hash"),
    )


def test_provisioned_user_is_pending():
    # Imported Datatruck driver: no Telegram, no password → pending.
    assert member_lifecycle(_u()) == "pending"


def test_telegram_link_activates():
    assert member_lifecycle(_u(telegram_id=12345)) == "active"


def test_email_password_activates():
    assert member_lifecycle(_u(password_hash="bcrypt$…")) == "active"


def test_deactivation_wins_over_everything():
    assert member_lifecycle(_u(telegram_id=12345, is_active=False)) == "inactive"
    assert member_lifecycle(_u(is_active=False)) == "inactive"
