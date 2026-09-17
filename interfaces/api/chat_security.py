"""Browser socket authentication and account holds at the API boundary.

HTTP middleware does not run for WebSockets. Re-evaluate the cookie and
live account/session state on every replay pass; no request identity cache.
"""
import os
import time

from fastapi import HTTPException
from jose import JWTError

from capabilities.chat.models import Actor
from interfaces.api.auth import decode_jwt, is_jti_revoked, validate_session_payload


async def check_chat_holds(db, actor):
    from system.security import quarantine
    if quarantine.enabled():
        # The normal HTTP helper deliberately fails open/cache-TTL. A persistent
        # stream requires a fresh authoritative decision and fails closed.
        user = await db.get_user_by_id(actor.user_id)
        account = await db.get_account(actor.account_id)
        if (not user or not account or getattr(user, 'security', None) == quarantine.HELD
                or getattr(account, 'security', None) == quarantine.HELD):
            raise HTTPException(403, 'Account access unavailable')
    if os.getenv('BILLING_ENFORCEMENT_ENABLED', '0') == '1':
        from adapters.storage.billing import BillingMixin
        sub = await db.get_subscription(actor.account_id)
        if BillingMixin.is_account_blocked(sub, int(os.getenv('BILLING_GRACE_PERIOD_DAYS', '7')))[0]:
            raise HTTPException(402, 'Payment required')


async def authenticate_chat_socket(socket, db):
    origins = getattr(socket.app.state, 'chat_origins', ())
    if (socket.headers.get('origin') not in origins or socket.query_params
            or socket.headers.get('authorization') or socket.headers.get('sec-websocket-protocol')):
        raise HTTPException(403, 'Unsupported browser connection')
    token = socket.cookies.get('auth_token')
    if not token:
        raise HTTPException(401, 'Not authenticated')
    try:
        payload = decode_jwt(token)
        # Dashboard cookies are unscoped; extension/setup/unknown audiences
        # and scoped credentials cannot be upgraded into a full Chat session.
        if payload.get('aud') is not None or payload.get('scope') is not None:
            raise HTTPException(403, 'Dashboard session required')
        if not isinstance(payload.get('exp'), (int, float)) or payload['exp'] <= time.time():
            raise JWTError('Expired session')
        if await is_jti_revoked(str(payload.get('jti') or '')):
            raise JWTError('Revoked session')
        payload = await validate_session_payload(payload, db)
        actor = Actor(int(payload['account_id']), int(payload.get('uid') or 0))
        if actor.account_id <= 0 or actor.user_id <= 0:
            raise JWTError('Customer session required')
    except (JWTError, ValueError, KeyError):
        raise HTTPException(401, 'Not authenticated') from None
    await check_chat_holds(db, actor)
    return actor
