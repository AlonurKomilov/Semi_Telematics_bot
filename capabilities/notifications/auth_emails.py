"""Email templates + senders for account-security flows.

Three messages live here: password-reset link, email-verification link,
and the "your account was just locked after N failed login attempts"
heads-up.  Each one composes a plain-text body + an HTML body and
hands them to the shared ``send_email`` shim.

Why a dedicated module
----------------------
- Keeps copy + branding in one file so future redesigns touch one
  surface, not three call sites.
- Insulates the auth routes from string-formatting and HTML escaping.
- Makes the public URLs (where the link points) configurable via a
  single env var so dev/staging/prod can each have their own dashboard
  origin without code changes.
"""

from __future__ import annotations

import html
import logging
import os
from typing import Optional

from capabilities.notifications.email import is_email_configured, send_email

logger = logging.getLogger(__name__)


# Where the auth pages live — used to compose the link inside the
# email body.  Defaults to the apex (4truck.us) so the URL is the same
# for every persona; the dashboard SPA on the apex serves /login,
# /forgot-password, /reset-password, and /verify-email and bounces the
# user to their persona subdomain (fleet./dispatch./safety./dash.)
# AFTER they redeem the token.  Deployments override via env so a
# staging mail doesn't ship a prod link.
#
# ``DASHBOARD_BASE_URL`` is honored as a legacy fallback for hosts that
# still set the old name — they'll keep sending links to dash. until
# someone updates the env, which keeps working because the dashboard
# SPA on dash. handles the same routes.
def _auth_base() -> str:
    value = (
        os.getenv("AUTH_BASE_URL")
        or os.getenv("DASHBOARD_BASE_URL")
        or "https://4truck.us"
    )
    return value.rstrip("/")


def _company_name() -> str:
    return os.getenv("EMAIL_BRAND_NAME") or "4truck"


# ── Reset password ──────────────────────────────────────────────────


def send_password_reset_email(
    *, to: str, token: str, recipient_name: str = "",
) -> bool:
    """Compose + send the password-reset email.

    Returns ``True`` when handed off to the relay, ``False`` when
    SMTP isn't configured or the send raised.  The caller is
    responsible for the auth-route response either way — we never
    want a transient email failure to leak the existence (or not) of
    an email address to a probing attacker.
    """
    if not is_email_configured():
        logger.info(
            "Password reset email NOT sent (SMTP not configured) — "
            "token for %s: visit %s/reset-password?token=%s",
            to, _auth_base(), token,
        )
        return False

    brand = _company_name()
    base = _auth_base()
    reset_url = f"{base}/reset-password?token={token}"
    greeting = f"Hi {html.escape(recipient_name)}," if recipient_name else "Hi,"

    subject = f"{brand}: reset your password"
    text_body = (
        f"{greeting}\n\n"
        f"We received a request to reset the password for your {brand} account.\n"
        f"To choose a new password, open this link in your browser:\n\n"
        f"{reset_url}\n\n"
        "The link expires in 1 hour and can only be used once.\n\n"
        "If you didn't request this, you can safely ignore this email — "
        "your password won't change.\n\n"
        f"— The {brand} team"
    )
    html_body = f"""\
<!doctype html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:560px;margin:24px auto;color:#1f2937">
<p style="font-size:15px">{greeting}</p>
<p style="font-size:15px">We received a request to reset the password for your <strong>{html.escape(brand)}</strong> account.</p>
<p style="margin:28px 0">
  <a href="{html.escape(reset_url)}"
     style="background:#111827;color:#fff;text-decoration:none;padding:12px 22px;border-radius:8px;display:inline-block;font-weight:600">
    Choose a new password
  </a>
</p>
<p style="font-size:13px;color:#6b7280">Or paste this URL into your browser:<br>
<span style="word-break:break-all">{html.escape(reset_url)}</span></p>
<p style="font-size:13px;color:#6b7280">The link expires in 1 hour and can only be used once.</p>
<p style="font-size:13px;color:#6b7280">If you didn't request this, you can safely ignore this email — your password won't change.</p>
<p style="font-size:13px;color:#9ca3af;margin-top:32px">— The {html.escape(brand)} team</p>
</body></html>"""
    return send_email(to=to, subject=subject, body=text_body, html_body=html_body)


# ── Verify email ────────────────────────────────────────────────────


def send_verification_email(
    *, to: str, token: str, recipient_name: str = "",
) -> bool:
    """Compose + send the email-verification message.

    Sent on signup and on any later "resend verification" request.
    The link redirects the user to the dashboard once redeemed; the
    backend flips ``users.email_verified=1`` and clears the lock that
    prevented login.
    """
    if not is_email_configured():
        logger.info(
            "Verification email NOT sent (SMTP not configured) — "
            "token for %s: visit %s/verify-email?token=%s",
            to, _auth_base(), token,
        )
        return False

    brand = _company_name()
    base = _auth_base()
    verify_url = f"{base}/verify-email?token={token}"
    greeting = f"Welcome to {brand}, {html.escape(recipient_name)}!" if recipient_name else f"Welcome to {brand}!"

    subject = f"Verify your email for {brand}"
    text_body = (
        f"{greeting}\n\n"
        f"Confirm your email so you can sign in and start using {brand}:\n\n"
        f"{verify_url}\n\n"
        "The link expires in 24 hours.  If you didn't create this account, "
        "you can safely ignore this email.\n\n"
        f"— The {brand} team"
    )
    html_body = f"""\
<!doctype html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:560px;margin:24px auto;color:#1f2937">
<p style="font-size:17px;font-weight:600">{greeting}</p>
<p style="font-size:15px">Confirm your email address so you can sign in and start using <strong>{html.escape(brand)}</strong>.</p>
<p style="margin:28px 0">
  <a href="{html.escape(verify_url)}"
     style="background:#111827;color:#fff;text-decoration:none;padding:12px 22px;border-radius:8px;display:inline-block;font-weight:600">
    Verify my email
  </a>
</p>
<p style="font-size:13px;color:#6b7280">Or paste this URL into your browser:<br>
<span style="word-break:break-all">{html.escape(verify_url)}</span></p>
<p style="font-size:13px;color:#6b7280">The link expires in 24 hours.  If you didn't create this account, you can safely ignore this email.</p>
<p style="font-size:13px;color:#9ca3af;margin-top:32px">— The {html.escape(brand)} team</p>
</body></html>"""
    return send_email(to=to, subject=subject, body=text_body, html_body=html_body)


# ── Lockout notification ────────────────────────────────────────────


def send_lockout_notice(
    *, to: str, ip: Optional[str] = None,
    lock_minutes: int = 15, recipient_name: str = "",
) -> bool:
    """Heads-up email when an account hits the lockout threshold.

    Sent right after the 5th consecutive failed-login attempt.  The
    goal is to alert the legitimate account holder that someone may
    be trying to break in — they can then change their password
    proactively even before the lock window expires.
    """
    if not is_email_configured():
        logger.info(
            "Lockout notice NOT sent (SMTP not configured) — to %s, lock %d min",
            to, lock_minutes,
        )
        return False

    brand = _company_name()
    greeting = f"Hi {html.escape(recipient_name)}," if recipient_name else "Hi,"
    ip_note = f" The attempts came from IP <code>{html.escape(ip)}</code>." if ip else ""

    subject = f"{brand}: your account was temporarily locked"
    text_body = (
        f"{greeting}\n\n"
        f"We saw 5 failed login attempts on your {brand} account and "
        f"temporarily locked it for {lock_minutes} minutes.\n\n"
        "If that was you (typo, forgotten password), wait it out or use the "
        "\"Forgot password\" link on the login page.\n\n"
        "If it wasn't you, change your password as soon as the lock clears — "
        "someone may be trying to guess it.\n\n"
        f"— The {brand} team"
    )
    html_body = f"""\
<!doctype html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:560px;margin:24px auto;color:#1f2937">
<p style="font-size:15px">{greeting}</p>
<p style="font-size:15px">We saw <strong>5 failed login attempts</strong> on your <strong>{html.escape(brand)}</strong> account
and temporarily locked it for <strong>{lock_minutes} minutes</strong>.{ip_note}</p>
<p style="font-size:14px">If that was you (typo, forgotten password) — wait it out or use the
&ldquo;Forgot password&rdquo; link on the login page.</p>
<p style="font-size:14px">If it wasn't you, change your password as soon as the lock clears — someone may be trying to guess it.</p>
<p style="font-size:13px;color:#9ca3af;margin-top:32px">— The {html.escape(brand)} team</p>
</body></html>"""
    return send_email(to=to, subject=subject, body=text_body, html_body=html_body)


# ── Invite (new-teammate join link) ────────────────────────────────


def send_invite_email(
    *,
    to: str,
    code: str,
    account_name: str,
    role_label: str,
    inviter_display_name: str,
    expires_at: str,
    truck_num: Optional[str] = None,
    recipient_name: str = "",
) -> bool:
    """Send the operator-driven invite email.

    Uses the dedicated ``invites@`` sender (env ``SMTP_FROM_INVITES``)
    with a fallback to the shared ``SMTP_FROM`` so the feature ships
    even when the new env var hasn't been set yet — invite emails
    just route through ``noreply@`` until the operator updates the
    deploy.  See the design notes in interfaces/api/routes/admin.py.

    Adversarial-hardening choices (all surfaced by the design-vet
    workflow before any code was written):
      - PINNED Subject line.  No operator-supplied text reaches the
        Subject, Preheader, or From — prevents header injection and
        phishing-laundering of the 4truck brand.
      - NO operator free-text message field.  Body is fully templated;
        any future "personal note" addition must be plain-text-only,
        length-capped, html.escape()'d, and visually marked as a
        quoted note from the inviter (not as 4truck-authored copy).
      - LIST-UNSUBSCRIBE header per RFC 8058.  Required by
        Gmail/Yahoo for senders >5k/day; absence is a spam-score
        signal at any volume.  Points at a decline URL that revokes
        the invite — "unsubscribe" semantically maps to
        "don't onboard me" for a transactional invite.
      - AUTO-SUBMITTED: auto-generated tells autoresponders not to
        bounce-loop and keeps Exchange's filter quieter.
      - Path-segment signup URL (``/signup/<CODE>``) instead of
        ``?invite=`` query param — keeps the code out of Referer
        headers, browser history middle-segment logs, and CDN access
        logs.
      - PLAIN-TEXT fallback alongside HTML so Outlook desktop
        renders cleanly even when remote images are blocked.
    """
    brand = _company_name()
    base = _auth_base()
    # _sanitize_inline: strip control chars + cap length so an
    # operator-named account ("Acme; phishing victim", or an
    # account_name containing CRLF that would inject MIME headers
    # via base body wrap) can't laundering the 4truck brand for
    # social engineering.  Applies to anything operator-controlled
    # that reaches the Subject OR the plain-text body.
    def _sanitize_inline(s: str, *, max_len: int = 64) -> str:
        if not s:
            return ""
        # Strip CR/LF/TAB and any other control chars; collapse
        # internal whitespace; clip.  ASCII-only is overkill —
        # legitimate account names contain accents.
        cleaned = "".join(
            ch for ch in s if ch == " " or (ord(ch) >= 0x20 and ch not in "\r\n\t")
        ).strip()
        if len(cleaned) > max_len:
            cleaned = cleaned[: max_len - 1] + "…"
        return cleaned

    safe_account = _sanitize_inline(account_name) or "your new team"
    safe_inviter = _sanitize_inline(inviter_display_name) or "your inviter"
    safe_role = _sanitize_inline(role_label, max_len=32) or "member"
    safe_recipient = _sanitize_inline(recipient_name, max_len=64)
    safe_truck = _sanitize_inline(truck_num or "", max_len=24) or None
    # Path-segment URL preserves the invite code out of Referer +
    # CDN query logs.  The new /signup/<code> route at the SPA layer
    # is the matching read side.
    signup_url = f"{base}/signup/{code}"
    # Decline link MUST point at the actual API route (which lives
    # under /api/v1/auth/invite/decline — the SPA at the apex would
    # 404 if we sent recipients there).  Read from env so a deploy
    # split (api.4truck.us vs dash.4truck.us) can override the
    # default.  Honors APEX-routed deploys where dash.4truck.us
    # proxies /api/* to api.4truck.us via reverse proxy.
    api_base = (
        os.getenv("API_BASE_URL")
        or _auth_base()  # apex serves /api/v1 via reverse proxy on default deploys
    ).rstrip("/")
    decline_url = f"{api_base}/api/v1/auth/invite/decline?token={code}"

    greeting = (
        f"Hi {safe_recipient}," if safe_recipient else "Hi,"
    )
    truck_line = (
        f"\nTruck assignment: #{safe_truck}\n" if safe_truck else ""
    )
    # SUBJECT — the design pin was "no operator-supplied text reaches
    # Subject".  account_name IS operator-controlled (self-serve
    # signup names the account).  _sanitize_inline removes CRLF and
    # caps length so a hostile account name can't break MIME or
    # brand-launder via 64+ chars of phishing copy.  Brand stays in
    # the Subject so the recipient sees the vendor name they trust.
    subject = f"You're invited to {safe_account} on {brand}"
    text_body = (
        f"{greeting}\n\n"
        f"{safe_inviter} has invited you to join {safe_account} "
        f"on {brand} as a {safe_role}.{truck_line}\n"
        f"Open this link to set up your account:\n"
        f"  {signup_url}\n\n"
        f"The link is valid until {expires_at}.\n\n"
        f"Questions about this invite?  Contact your administrator at "
        f"{safe_account} directly — we can't forward replies to "
        f"individual senders.\n\n"
        f"If you weren't expecting this, ignore it or use the "
        f"unsubscribe link below to decline.\n\n"
        f"— The {brand} team"
    )
    truck_html = (
        f"<p style=\"font-size:14px;color:#374151\">"
        f"Truck assignment: <strong>#{html.escape(safe_truck)}</strong></p>"
        if safe_truck else ""
    )
    # All URL interpolations into HTML attributes go through
    # html.escape — defence-in-depth even though ``code`` is a
    # server-generated XXXX-XXXX hex string today.  If a future
    # refactor lets operator text reach the code format, the escape
    # is the only thing standing between a quote in the URL and a
    # href-attribute breakout.
    safe_signup = html.escape(signup_url, quote=True)
    safe_decline = html.escape(decline_url, quote=True)
    # Bulletproof CTA button — table+VML wrapper renders the
    # primary action as a real button in Outlook desktop O365
    # (Word renderer drops border-radius/padding on <a>).  The MSO
    # conditional comment only fires in Outlook; every other client
    # sees the inline-styled <a> fallback below.  Pattern: Campaign
    # Monitor's "bulletproof button".
    cta_button = f"""\
<!--[if mso]>
<v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="{safe_signup}" style="height:44px;v-text-anchor:middle;width:220px;" arcsize="14%" stroke="f" fillcolor="#0066ff">
  <w:anchorlock/>
  <center style="color:#ffffff;font-family:sans-serif;font-size:15px;font-weight:600;">Set up my account</center>
</v:roundrect>
<![endif]-->
<!--[if !mso]><!-- -->
<a href="{safe_signup}" style="display:inline-block;background:#0066ff;color:#ffffff;text-decoration:none;padding:12px 20px;border-radius:6px;font-weight:600">Set up my account</a>
<!--<![endif]-->"""
    # Preheader span — Gmail iOS uses the first ~90 chars of body
    # as the inbox preview line.  Hidden span overrides that with
    # intentional copy.  color:transparent + max-height:0 is the
    # cross-client recipe.
    preheader = (
        f"{html.escape(safe_inviter)} invited you to {html.escape(safe_account)} "
        f"on {html.escape(brand)} — set up your account in one click."
    )
    html_body = f"""\
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="color-scheme" content="light"><title>{html.escape(brand)} invite</title></head><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:560px;margin:24px auto;color:#1f2937">
<span style="display:none;max-height:0;overflow:hidden;color:transparent">{preheader}</span>
<p style="font-size:15px">{html.escape(greeting)}</p>
<p style="font-size:15px"><strong>{html.escape(safe_inviter)}</strong> has invited you to join
<strong>{html.escape(safe_account)}</strong> on {html.escape(brand)} as a <strong>{html.escape(safe_role)}</strong>.</p>
{truck_html}
<p style="font-size:15px;margin-top:24px">{cta_button}</p>
<p style="font-size:13px;color:#6b7280">Or paste this link into your browser: <a href="{safe_signup}" style="color:#0066ff">{safe_signup}</a></p>
<p style="font-size:13px;color:#6b7280">The link is valid until {html.escape(expires_at)}.</p>
<p style="font-size:14px;color:#374151;margin-top:24px">Questions about this invite?  Contact your administrator at {html.escape(safe_account)} directly — we can't forward replies to individual senders.</p>
<p style="font-size:12px;color:#6b7280;margin-top:32px">— The {html.escape(brand)} team</p>
<p style="font-size:11px;color:#6b7280">Wasn't expecting this?  <a href="{safe_decline}" style="color:#6b7280">Click here to decline this invite.</a></p>
</body></html>"""

    invites_from = os.getenv("SMTP_FROM_INVITES")
    # List-Unsubscribe per RFC 8058.  HTTPS-only — the mailto: leg
    # was removed because unsubscribe@<domain> is rarely a real
    # monitored mailbox and bounces back from it hurt sender
    # reputation more than the legacy-client coverage helps.
    # Gmail/Yahoo's One-Click POST + Outlook 2019+/Apple Mail all
    # honour the HTTPS form.  Add a real mailbox + autoresponder
    # in a follow-up if Outlook < 2019 turnout is measured to need it.
    headers = {
        "List-Unsubscribe": f"<{decline_url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        "Auto-Submitted": "auto-generated",
        "X-Entity-Ref-ID": f"invite-{code}",
    }
    return send_email(
        to=to,
        subject=subject,
        body=text_body,
        html_body=html_body,
        from_address=invites_from,                # falls back to SMTP_FROM in send_email
        from_name=f"{brand} Invites" if invites_from else None,
        extra_headers=headers,
    )
