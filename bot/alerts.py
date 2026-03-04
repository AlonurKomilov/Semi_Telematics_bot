"""Scheduled fault alert checks — multi-tenant."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from telegram.constants import ParseMode

from database import Role
from samsara_client import populate_org_display
from formatters import format_new_fault_alert

from bot.config import (
    db, logger, _known_faults, _active_messages, get_client,
)


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

                # Populate org display for this account
                acct_orgs = await db.get_account_orgs(account_id)
                populate_org_display(acct_orgs)
                org_codes = [o.code for o in acct_orgs]

                for v in faulted:
                    org = v.get("_org", "?")
                    vid = f"{account_id}:{org}:{v['id']}"
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
                                show_org=len(org_codes) > 1,
                            )
                            kb = InlineKeyboardMarkup([
                                [InlineKeyboardButton(
                                    f"📋 View Truck #{v['name']}",
                                    callback_data=f"orgtruck_{org}_{v['name']}"
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
                                        sub.telegram_id, []
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
                    org = v.get("_org", "?")
                    vid = f"{account_id}:{org}:{v['id']}"
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
