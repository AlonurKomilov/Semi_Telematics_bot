"""AI diagnosis helpers and auto-maintenance creation from faults."""

from __future__ import annotations

import logging
from capabilities.formatting.helpers import escape_html
from core.services import get_tenant_db, get_platform_db
from capabilities.alerting.pipeline import SYSTEM_USER_ID

logger = logging.getLogger("bot")


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

        response = await ai.generate(
            prompt,
            system=ai.FAULT_DIAGNOSIS_SYSTEM,
        )

        # Track proactive AI usage
        usage = ai.get_last_usage()
        if usage:
            try:
                # system-triggered usage
                await get_platform_db().log_ai_usage(
                    account_id=SYSTEM_USER_ID,
                    user_id=SYSTEM_USER_ID,
                    model=ai.get_current_model_name(),
                    request_type="proactive_diagnosis",
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    reply_tokens=usage.get("reply_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                )
            except Exception as e:
                logger.debug("AI usage logging failed (proactive_diagnosis): %s", e)

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

        response = await ai.generate(
            prompt,
            system=ai.FAULT_DIAGNOSIS_SYSTEM,
        )

        usage = ai.get_last_usage()
        if usage:
            try:
                await get_platform_db().log_ai_usage(
                    account_id=SYSTEM_USER_ID,
                    user_id=SYSTEM_USER_ID,
                    model=ai.get_current_model_name(),
                    request_type="proactive_health_diagnosis",
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    reply_tokens=usage.get("reply_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                )
            except Exception as e:
                logger.debug("Failed to log AI usage for health diagnosis: %s", e)
            text = _truncate_at_sentence(response, 800)
            return f"\n\n🤖 <b>AI Assessment:</b>\n{escape_html(text)}"
    except Exception as e:
        logger.debug(f"AI health note failed: {e}")
    return ""


# ── Auto-Maintenance from Critical Faults ────────────────────────

# SPN → maintenance task type mapping
_SPN_MAINTENANCE_MAP = {
    110: "custom",   # Coolant temp → custom inspection
    111: "custom",   # Coolant level
    100: "oil",      # Oil pressure
    101: "oil",      # Oil level
    91: "brakes",    # Brake pressure
    97: "custom",    # Water in fuel
    190: "custom",   # Engine speed (overspeed)
    4331: "custom",  # DEF quality
    3031: "custom",  # DEF level
    5246: "custom",  # DEF tank
}

_SPN_DESCRIPTIONS = {
    110: "Coolant temperature issue",
    111: "Coolant level issue",
    100: "Engine oil pressure issue",
    101: "Engine oil level issue",
    91: "Brake system pressure issue",
    97: "Water-in-fuel detected",
    190: "Engine overspeed event",
    4331: "DEF quality issue",
    3031: "DEF level low",
    5246: "DEF tank issue",
}


async def auto_create_maintenance_from_faults(
    account_id: int, vehicle_name: str, dtcs: list[dict],
):
    """Auto-create maintenance tasks from critical fault codes.

    Only creates a task if one doesn't already exist (pending/overdue)
    for the same vehicle and task type.
    """
    try:
        tenant = await get_tenant_db(account_id)
        existing = await tenant.get_maintenance_tasks(account_id, vehicle_name=vehicle_name)
        existing_types = {
            (t["vehicle_name"], t["task_type"])
            for t in existing
            if t["status"] in ("pending", "overdue")
        }

        for dtc in dtcs:
            spn = dtc.get("spnId")
            if spn not in _SPN_MAINTENANCE_MAP:
                continue

            task_type = _SPN_MAINTENANCE_MAP[spn]
            if (vehicle_name, task_type) in existing_types:
                continue  # already has a pending task

            desc = _SPN_DESCRIPTIONS.get(spn, f"Auto-created from SPN {spn}")
            fmi_desc = dtc.get("fmiDescription", "")
            if fmi_desc:
                desc += f" ({fmi_desc})"

            await tenant.add_maintenance_task(
                account_id=account_id,
                company_code="",
                vehicle_name=vehicle_name,
                task_type=task_type,
                description=f"🤖 Auto-created: {desc}",
                created_by=0,  # system-generated
            )
            existing_types.add((vehicle_name, task_type))
            logger.info(
                f"Auto-maintenance: {vehicle_name} → {task_type} (SPN {spn})"
            )
    except Exception as e:
        logger.error(f"Auto-maintenance creation failed: {e}")


async def check_api_health(account_id: int) -> dict[str, str]:
    """Test each company's Samsara API and return status dict.

    Returns: {company_code: "ok" | "error: <message>"}
    """
    companies = await (await get_tenant_db(account_id)).get_account_companies(account_id)
    results: dict[str, str] = {}

    for co in companies:
        from adapters.samsara.client import SamsaraClient
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
