"""AI diagnosis helpers and auto-maintenance creation from faults."""

from __future__ import annotations

from bot.config import db, logger
from bot.helpers import escape_html
from bot.alerts.pipeline import SYSTEM_USER_ID


# ── Proactive AI on Critical Alerts ──────────────────────────────

async def _get_ai_diagnosis_note(vehicle: dict, dtcs: list[dict]) -> str:
    """Generate a short AI diagnosis note for critical fault alerts.

    Returns an HTML string to append to the alert, or empty string if
    AI is not configured or fails.
    """
    try:
        import ai
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
                from bot.config import db as _db
                # system-triggered usage
                await _db.log_ai_usage(
                    account_id=SYSTEM_USER_ID,
                    user_id=SYSTEM_USER_ID,
                    model=ai.get_current_model_name(),
                    request_type="proactive_diagnosis",
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    reply_tokens=usage.get("reply_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                )
            except Exception:
                pass

        if response and len(response) < 500:
            return f"\n\n🤖 <b>AI Diagnosis:</b>\n{escape_html(response)}"
        elif response:
            return f"\n\n🤖 <b>AI Diagnosis:</b>\n{escape_html(response[:500])}…"
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
        import ai
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
                from bot.config import db as _db
                await _db.log_ai_usage(
                    account_id=SYSTEM_USER_ID,
                    user_id=SYSTEM_USER_ID,
                    model=ai.get_current_model_name(),
                    request_type="proactive_health_diagnosis",
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    reply_tokens=usage.get("reply_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                )
            except Exception:
                pass

        if response and len(response) < 500:
            return f"\n\n🤖 <b>AI Assessment:</b>\n{escape_html(response)}"
        elif response:
            return f"\n\n🤖 <b>AI Assessment:</b>\n{escape_html(response[:500])}…"
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
        existing = await db.get_maintenance_tasks(account_id, vehicle_name=vehicle_name)
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

            await db.add_maintenance_task(
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
