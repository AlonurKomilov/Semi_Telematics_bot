"""AI diagnosis helpers and auto-maintenance creation from faults."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application

import infra.cache as rcache
from capabilities.formatting.helpers import escape_html
from infra.services import get_tenant_db, get_platform_db
from capabilities.alerting.pipeline import SYSTEM_USER_ID

logger = logging.getLogger("bot")


# ── API error notifications ─────────────────────────────────────
# Throttled alert fired when Samsara API errors cause skipped companies.
# Lives here (capability-shared) so faults / health / fuel can all reuse it.

_API_ALERT_COOLDOWN_S = 6 * 3600
_api_alert_sent: dict[str, float] = {}


async def _notify_api_errors(
    bot_app: Application,
    account_id: int,
    skipped_codes: list[str],
):
    """Send a one-time Telegram alert to account admins when one or more
    company API calls failed.  Throttled per-company via Redis (preferred)
    or in-memory fallback.
    """
    if not skipped_codes:
        return
    now = datetime.now(timezone.utc).timestamp()
    codes_to_alert: list[str] = []
    for code in skipped_codes:
        mem_key = f"{account_id}:{code}"
        redis_key = f"t:{account_id}:api_alert:{code}"
        if rcache.is_available():
            if await rcache.exists(redis_key):
                continue
            codes_to_alert.append(code)
            await rcache.setex_flag(redis_key, _API_ALERT_COOLDOWN_S)
        else:
            last = _api_alert_sent.get(mem_key, 0.0)
            if now - last >= _API_ALERT_COOLDOWN_S:
                codes_to_alert.append(code)
                _api_alert_sent[mem_key] = now

    if not codes_to_alert:
        return

    admins = await get_platform_db().get_account_admins(account_id)
    if not admins:
        return

    codes_str = ", ".join(codes_to_alert)
    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  🚨  <b>API ERROR</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  Company: <b>{escape_html(codes_str)}</b>\n"
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
            await bot_app.bot.send_message(
                chat_id=admin.telegram_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        except Exception as e:
            logger.error("API alert to admin %s failed: %s", admin.telegram_id, e)

    logger.warning(
        "API error alert sent for account %d, companies: %s",
        account_id, codes_str,
    )


def _truncate_at_sentence(text: str, max_len: int) -> str:
    """Truncate text at the last sentence boundary within max_len.

    If the text is shorter than max_len, returns it as-is.
    Otherwise finds the last '.', '!', or '?' within the limit.
    """
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    # Find last sentence-ending punctuation
    for i in range(len(truncated) - 1, -1, -1):
        if truncated[i] in ".!?":
            return truncated[: i + 1]
    # No sentence boundary found — fall back to last space
    last_space = truncated.rfind(" ")
    if last_space > max_len // 2:
        return truncated[:last_space] + "…"
    return truncated + "…"


_REFUSAL_PREFIXES = (
    "error", "i cannot", "i'm sorry", "i can't", "i am unable",
    "as an ai", "i'm not able",
)


def _is_valid_ai_response(text: str | None) -> bool:
    """Return True if the AI response contains useful content."""
    if not text or len(text.strip()) < 20:
        return False
    lower = text.strip().lower()
    return not lower.startswith(_REFUSAL_PREFIXES)


# ── Proactive AI on Critical Alerts ──────────────────────────────
#
# Per-call wall-clock budget for the AI note generators below.  Both
# ``check_health_alerts`` (120s) and ``check_new_faults`` (default 120s)
# loop over every alerted vehicle and ``await`` the AI note before
# calling ``send_alert``.  If ``ai.generate`` hangs (Vertex AI slowness
# / network), one stuck call eats half the job budget; ten stuck calls
# blow the budget and the job times out with no alerts shipped.
#
# 15s is the sweet spot: a healthy diagnosis takes ~3-5s and the
# fallback to a different model on rate-limit fits inside the budget,
# but a truly hung Vertex backend can't monopolise the loop.
_AI_NOTE_TIMEOUT_S = 15.0


async def _get_ai_diagnosis_note(vehicle: dict, dtcs: list[dict]) -> str:
    """Generate a short AI diagnosis note for critical fault alerts.

    Returns an HTML string to append to the alert, or empty string if
    AI is not configured or fails.
    """
    try:
        import capabilities.ai as ai
        if not ai.is_configured():
            return ""

        # Build a compact context for AI
        fault_descs = []
        for dtc in dtcs[:5]:
            spn = dtc.get("spnId", "?")
            fmi = dtc.get("fmiId", "?")
            desc = dtc.get("spnDescription", "Unknown")
            fault_descs.append(f"SPN {spn} / FMI {fmi}: {desc}")

        prompt = (
            f"In 2-3 sentences, diagnose these faults on Truck #{vehicle.get('name', '?')}: "
            + "; ".join(fault_descs)
            + ". What's the likely cause and should the driver stop?"
        )

        # ``action="proactive_diagnosis"`` enables router telemetry inside
        # ai.generate() — one ai_usage row is written for every model
        # attempt (success or failure), so no separate log call here.
        response, _usage = await ai.generate(
            prompt,
            system=ai.FAULT_DIAGNOSIS_SYSTEM,
            account_id=SYSTEM_USER_ID,
            user_id=SYSTEM_USER_ID,
            user_context={"role": "system"},
            action="proactive_diagnosis",
        )

        if _is_valid_ai_response(response):
            text = _truncate_at_sentence(response, 800)
            return f"\n\n🤖 <b>AI Diagnosis:</b>\n{escape_html(text)}"
    except Exception as e:
        logger.debug(f"AI diagnosis for alert failed: {e}")
    return ""


async def _get_ai_health_note(
    vehicle: dict, alert_names: list[str], health: dict,
) -> str:
    """Generate a short AI note for health alerts (low oil, high coolant, etc.).

    Returns an HTML string to append to the alert, or empty string if
    AI is not configured or fails.
    """
    try:
        import capabilities.ai as ai
        if not ai.is_configured():
            return ""

        condition_strs = []
        label_map = {
            "low_battery": "Low battery voltage",
            "low_oil_pressure": "Low oil pressure",
            "high_coolant_temp": "High coolant temperature",
            "low_def": "Low DEF level",
            "coolant_dtc": "Coolant-system DTC active",
        }
        for a in alert_names:
            desc = label_map.get(a, a)
            val = health.get(a)
            if val is not None:
                desc += f" ({val})"
            condition_strs.append(desc)

        prompt = (
            f"In 2-3 sentences, assess these health conditions on "
            f"Truck #{vehicle.get('name', '?')}: "
            + "; ".join(condition_strs)
            + ". What should the driver do immediately?"
        )

        # Router telemetry handled inside ai.generate() via action= param.
        response, _usage = await ai.generate(
            prompt,
            system=ai.FAULT_DIAGNOSIS_SYSTEM,
            account_id=SYSTEM_USER_ID,
            user_id=SYSTEM_USER_ID,
            user_context={"role": "system"},
            action="proactive_health_diagnosis",
        )

        if _is_valid_ai_response(response):
            text = _truncate_at_sentence(response, 800)
            return f"\n\n🤖 <b>AI Assessment:</b>\n{escape_html(text)}"
    except Exception as e:
        logger.debug(f"AI health note failed: {e}")
    return ""


# ── Auto-Maintenance from Critical Faults ────────────────────────
# Canonical implementation lives in capabilities/maintenance/service.py.
# Re-exported here for backward compatibility.
from features.maintenance.service import auto_create_maintenance_from_faults  # noqa: F401, E402


async def check_api_health(account_id: int) -> dict[str, str]:
    """Test each company's Samsara API and return status dict.

    Returns: {company_code: "ok" | "error: <message>"}

    For per-company credential probes use the Integration card's lean
    ``/me`` test instead — this helper does a heavier ``get_vehicles``
    call and is kept for backwards-compat with older callers.
    """
    from infra.services import get_client
    multi = await get_client(account_id)
    results: dict[str, str] = {}

    for code, client in multi.clients.items():
        try:
            vehicles = await client.get_vehicles()
            results[code] = f"ok ({len(vehicles)} vehicles)"
        except Exception as e:
            results[code] = f"error: {e}"

    return results
