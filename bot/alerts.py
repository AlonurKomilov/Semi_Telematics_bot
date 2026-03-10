"""Scheduled fault alert checks — multi-tenant."""

from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from telegram.constants import ParseMode

from database import Role
from samsara_client import populate_company_display
from formatters import format_new_fault_alert

from bot.config import (
    db, logger, _known_faults, _active_messages, get_client,
)

# Cooldown: don't re-alert the same company API failure within 6 hours
_API_ALERT_COOLDOWN_S = 6 * 3600
_api_alert_sent: dict[str, float] = {}   # "acctID:CODE" → timestamp


async def check_new_faults(app: Application):
    """Check all accounts with alert subscribers for new faults."""
    try:
        subscribers = await db.get_all_alert_subscribers()
        if not subscribers:
            return

        # Group subscribers by account
        acct_subs: dict[int, list] = {}
        for sub in subscribers:
            acct_subs.setdefault(sub.account_id, []).append(sub)

        for account_id, subs in acct_subs.items():
            try:
                samsara = await get_client(account_id)
                faulted, _, _ = await samsara.get_vehicles_with_faults()

                # ── Notify admins if any company APIs failed ─────
                if samsara.last_skipped:
                    await _notify_api_errors(
                        app, account_id, samsara.last_skipped
                    )

                # Populate company display for this account
                acct_companies = await db.get_account_companies(account_id)
                populate_company_display(acct_companies)
                company_codes = [o.code for o in acct_companies]

                for v in faulted:
                    co = v.get("_org", "?")
                    vid = f"{account_id}:{co}:{v['id']}"
                    current_codes = set()
                    for dtc in v.get("_dtcs", []):
                        key = f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}"
                        current_codes.add(key)

                    previously_known = _known_faults.get(vid, set())
                    new_codes = current_codes - previously_known

                    if new_codes and previously_known:
                        new_dtcs = [
                            dtc for dtc in v.get("_dtcs", [])
                            if f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}" in new_codes
                        ]
                        if new_dtcs:
                            alert_text = format_new_fault_alert(
                                v, new_dtcs,
                                show_company=len(company_codes) > 1,
                            )
                            kb = InlineKeyboardMarkup([
                                [InlineKeyboardButton(
                                    f"📋 View Truck #{v['name']}",
                                    callback_data=f"cotruck_{co}_{v['name']}"
                                )],
                                [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
                            ])
                            for sub in subs:
                                # Driver: only alert for their truck
                                if sub.role == Role.DRIVER and sub.truck_num:
                                    if v["name"].lower() != sub.truck_num.lower():
                                        continue
                                try:
                                    msg = await app.bot.send_message(
                                        chat_id=sub.telegram_id,
                                        text=alert_text,
                                        parse_mode=ParseMode.HTML,
                                        reply_markup=kb,
                                    )
                                    _active_messages.setdefault(
                                        (sub.telegram_id, sub.telegram_id), []
                                    ).append(msg.message_id)
                                except Exception as e:
                                    logger.error(f"Alert to {sub.telegram_id}: {e}")

                    _known_faults[vid] = current_codes

            except Exception as e:
                logger.error(f"Fault check for account {account_id}: {e}")

    except Exception as e:
        logger.error(f"Fault check error: {e}")


async def initialize_known_faults():
    """Pre-populate known faults for all accounts with alert subscribers."""
    try:
        subscribers = await db.get_all_alert_subscribers()
        account_ids = set(s.account_id for s in subscribers)

        for account_id in account_ids:
            try:
                samsara = await get_client(account_id)
                faulted, _, _ = await samsara.get_vehicles_with_faults()
                for v in faulted:
                    co = v.get("_org", "?")
                    vid = f"{account_id}:{co}:{v['id']}"
                    codes = set()
                    for dtc in v.get("_dtcs", []):
                        key = f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}"
                        codes.add(key)
                    _known_faults[vid] = codes
            except Exception as e:
                logger.error(f"Init faults for account {account_id}: {e}")

        logger.info(f"Known faults loaded for {len(_known_faults)} vehicles")
    except Exception as e:
        logger.error(f"Init faults failed: {e}")


# ── API Health Notifications ─────────────────────────────────────

async def _notify_api_errors(
    app: Application,
    account_id: int,
    skipped_codes: list[str],
):
    """Send a one-time Telegram alert to account owners/admins when
    a company's Samsara API call fails.  Throttled to one alert per
    company per _API_ALERT_COOLDOWN_S seconds.
    """
    now = datetime.now(timezone.utc).timestamp()
    codes_to_alert = []
    for code in skipped_codes:
        key = f"{account_id}:{code}"
        last = _api_alert_sent.get(key, 0)
        if now - last >= _API_ALERT_COOLDOWN_S:
            codes_to_alert.append(code)
            _api_alert_sent[key] = now

    if not codes_to_alert:
        return

    admins = await db.get_account_admins(account_id)
    if not admins:
        return

    codes_str = ", ".join(codes_to_alert)
    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  🚨  <b>API ERROR</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  Company: <b>{codes_str}</b>\n"
        "\n"
        "  The Samsara API returned an error\n"
        "  for this company. Reports will be\n"
        "  missing data until the issue is fixed.\n"
        "\n"
        "  <b>Possible causes:</b>\n"
        "  • API token expired or revoked\n"
        "  • Token missing required permissions\n"
        "  • Samsara service outage\n"
        "\n"
        "  Check your Samsara dashboard or\n"
        "  re-add the API key to resolve."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Check Status", callback_data="cmd_api_status")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])

    for admin in admins:
        try:
            await app.bot.send_message(
                chat_id=admin.telegram_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        except Exception as e:
            logger.error(f"API alert to admin {admin.telegram_id}: {e}")

    logger.warning(
        f"API error alert sent for account {account_id}, "
        f"companies: {codes_str}"
    )


async def check_api_health(account_id: int) -> dict[str, str]:
    """Test each company's Samsara API and return status dict.

    Returns: {company_code: "ok" | "error: <message>"}
    """
    companies = await db.get_account_companies(account_id)
    results: dict[str, str] = {}

    for co in companies:
        from samsara_client import SamsaraClient
        client = SamsaraClient(
            api_key=co.samsara_api_key,
            base_url="https://api.samsara.com",
        )
        try:
            vehicles = await client.get_vehicles()
            results[co.code] = f"ok ({len(vehicles)} vehicles)"
        except Exception as e:
            results[co.code] = f"error: {e}"
        finally:
            await client.close()

    return results
