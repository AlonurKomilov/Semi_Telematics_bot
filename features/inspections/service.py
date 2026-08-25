"""PTI workflow service — business logic between routes and storage.

Owns the rules the storage layer deliberately doesn't enforce:

  * **Spawn cadence** — when to create a new weekly inspection per
    driver (skip if one is open, skip if the last submission is
    < cadence_days old).
  * **Submit validation** — every required item must have a
    non-pending status, every ``requires_media`` item must have at
    least one photo or video attached.
  * **Status transitions** — only ``submitted`` rows can be reviewed;
    only ``in_progress`` / ``revision_required`` rows can be
    submitted.
  * **Defect computation** — fold per-item status into
    ``defects_count`` + ``has_oos_defect`` at submit time.

Routes call into this module rather than the storage mixin directly
so a "submit" button on the mini-app can't bypass the
all-required-items check by hitting the storage UPDATE directly.

Future hooks live as TODO comments — auto-spawn maintenance task on
``needs_service`` review (Phase 4) lands here, not in routes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from features.inspections.templates import (
    DEFECT_ITEM_STATUSES,
    OOS_ITEM_STATUSES,
    STANDARD_DOT_TRAILER_ITEMS,
    STANDARD_DOT_TRUCK_ITEMS,
    VALID_ITEM_STATUSES,
    VALID_REVIEW_STATUSES,
)

logger = logging.getLogger(__name__)


# ── Defaults — module-level so tests + jobs see them ─────────────────


# Cadence: how many days between spawn-new + previous submission.
# Stored here (not as an account setting yet) so the spawn cron has a
# stable default.  When Phase 2 lands the per-account override, this
# becomes the fallback when the setting is unset.
DEFAULT_CADENCE_DAYS: int = 7

# How far ahead "due_soon" reminders fire.
DEFAULT_REMIND_AHEAD_HOURS: int = 24


# ── Errors ──────────────────────────────────────────────────────────


class PTIError(Exception):
    """Base for PTI service-layer rule violations.

    Routes catch this + map to 400 / 409 — never let it bubble as a
    500.  Subclasses carry an ``error_code`` the dashboard can use to
    render a specific UI hint without parsing the message.
    """

    error_code = "pti_error"


class IncompleteInspection(PTIError):
    error_code = "incomplete_inspection"


class MissingMedia(PTIError):
    error_code = "missing_media"


class MissingSignature(PTIError):
    """Driver tried to submit without first capturing an e-signature.

    Surfaces as HTTP 400 from the route layer; the Mini App reads
    ``error_code`` to bounce the user back to the signature step
    rather than showing a generic toast.
    """

    error_code = "missing_signature"


class WrongStatus(PTIError):
    error_code = "wrong_status"


class NotFound(PTIError):
    error_code = "not_found"


# ── Helpers ─────────────────────────────────────────────────────────


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _summarize_items(items: list[dict]) -> tuple[int, bool, list[str]]:
    """Walk inspection items and return:
        (defects_count, has_oos_defect, list of item_keys that still
        need a non-pending status or a required photo/video).

    The "needs_attention" list is what the caller surfaces as a
    user-facing error when ``submit_inspection`` rejects a request.
    """
    defects = 0
    has_oos = False
    needs_attention: list[str] = []
    for it in items:
        # photo / document items carry no OK/Minor/OOS status — their
        # gate is "media present", enforced in _items_missing_media.
        # Only 'check' items participate in the status walk.
        if (it.get("item_type") or "check") != "check":
            continue
        status = (it.get("status") or "pending").lower()
        if not it.get("required"):
            # Optional items can stay pending — they're informational.
            if status in DEFECT_ITEM_STATUSES:
                defects += 1
            if status in OOS_ITEM_STATUSES:
                has_oos = True
            continue
        if status == "pending":
            needs_attention.append(it["item_key"])
            continue
        if status in DEFECT_ITEM_STATUSES:
            defects += 1
        if status in OOS_ITEM_STATUSES:
            has_oos = True
    return defects, has_oos, needs_attention


def _items_missing_media(items: list[dict], media: list[dict]) -> list[str]:
    """Return item_keys that have ``requires_media=True`` but no row
    in ``media`` referencing their id (or no general inspection-level
    media for items that ask for it without a specific link).

    Media rows MAY be linked to an item via ``item_id`` or stored as
    general inspection media (``item_id=None``).  We're strict: a
    ``requires_media`` item needs at least one media row tied to its
    own ``item_id``.  General media doesn't satisfy an item-specific
    requirement — otherwise a driver could upload one photo and tick
    every box.
    """
    # Count media per item, split by kind so a 'document' item is only
    # satisfied by a document upload (not a stray photo) and vice-versa.
    photo_by_item: dict[int, int] = {}
    doc_by_item: dict[int, int] = {}
    for m in media:
        iid = m.get("item_id")
        if iid is None:
            continue
        if (m.get("media_type") or "").lower() == "document":
            doc_by_item[int(iid)] = doc_by_item.get(int(iid), 0) + 1
        else:
            photo_by_item[int(iid)] = photo_by_item.get(int(iid), 0) + 1

    out: list[str] = []
    for it in items:
        if not it.get("required"):
            continue
        item_type = (it.get("item_type") or "check")
        iid = int(it["id"])
        if item_type == "document":
            # A document item must have at least one document upload.
            if doc_by_item.get(iid, 0) == 0:
                out.append(it["item_key"])
            continue
        if item_type == "photo":
            # A guided photo point always needs a photo/video.
            if photo_by_item.get(iid, 0) == 0:
                out.append(it["item_key"])
            continue
        # 'check' item: only enforce media when the template flagged it.
        if not it.get("requires_media"):
            continue
        # "na" exempts the photo — the item literally doesn't apply.
        if (it.get("status") or "").lower() == "na":
            continue
        if photo_by_item.get(iid, 0) == 0:
            out.append(it["item_key"])
    return out


# ── Spawning ────────────────────────────────────────────────────────


async def spawn_weekly_inspections(
    account_id: int,
    tenant_db,
    *,
    cadence_days: int = DEFAULT_CADENCE_DAYS,
    now: Optional[datetime] = None,
) -> list[int]:
    """Iterate every driver with an assigned truck.  For each that is
    *due* — no open inspection AND last submission older than
    ``cadence_days`` (or no prior submission at all) — spawn a fresh
    weekly inspection with items copied from the active truck
    template.

    Returns the list of newly-created inspection ids.

    Drivers without a ``truck_num`` are skipped (no truck to inspect).
    Skipping is silent here; the admin digest surfaces the
    "unassigned drivers" warning separately so this remains a clean
    cron entry point.
    """
    now_dt = now or datetime.now(timezone.utc)
    cutoff = now_dt - timedelta(days=cadence_days)

    drivers = await tenant_db.list_drivers(account_id)
    if not drivers:
        return []

    # Active truck template — needed for every spawn.  One fetch per
    # account-tick, items reused across drivers (template doesn't
    # change mid-loop).
    tpl = await tenant_db.get_active_template_with_items(
        account_id, "truck", "weekly",
    )
    if not tpl:
        logger.warning(
            "spawn_weekly_inspections acct=%d: no active truck template",
            account_id,
        )
        return []

    spawned: list[int] = []
    for d in drivers:
        # Skip drivers without an assigned vehicle.
        vehicle_name = getattr(d, "vehicle_name", None)
        if not vehicle_name:
            continue
        # Open inspection? — skip.
        open_ins = await tenant_db.get_open_inspection_for_user(d.user_id)
        if open_ins:
            continue
        # Last submission inside cadence window? — skip.
        last = await tenant_db.get_last_submitted_inspection_for_user(
            d.user_id, "weekly",
        )
        if last and last.get("submitted_at"):
            try:
                last_at = datetime.fromisoformat(last["submitted_at"])
            except (TypeError, ValueError):
                last_at = None
            if last_at and last_at > cutoff:
                continue

        # Spawn.
        due_by = (now_dt + timedelta(days=cadence_days)).isoformat(timespec="seconds")
        ins_id = await tenant_db.create_inspection(
            account_id=account_id,
            user_id=d.user_id,
            vehicle_name=vehicle_name,
            inspection_type="weekly",
            template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
            scheduled_for=now_dt.isoformat(timespec="seconds"),
            due_by=due_by,
        )
        await tenant_db.add_inspection_items(ins_id, tpl["items"])
        spawned.append(ins_id)

    logger.info(
        "spawn_weekly_inspections acct=%d spawned=%d / drivers=%d",
        account_id, len(spawned), len(drivers),
    )
    return spawned


# ── Ad-hoc creation (fleet manual) ──────────────────────────────────


async def create_ad_hoc_inspection(
    account_id: int,
    *,
    driver_id: int,
    vehicle_name: str,
    inspection_type: str = "weekly",
    vehicle_type: str = "truck",
    due_by: Optional[str] = None,
    tenant_db,
    platform_db,
) -> dict:
    """Fleet manually assigns a PTI to a driver outside the cron loop.

    Validates:
      * Driver belongs to ``account_id``.
      * Vehicle name resolves to a real assignment OR is explicitly
        overridden by the fleet (the picker default is the driver's
        assigned vehicle, but the fleet can change it when assigning
        a one-off inspection of a different truck).
      * ``vehicle_type`` template exists for the account.
      * Driver doesn't already have an open inspection (avoids
        accidental double-spawn that would confuse the driver).

    Returns the created inspection dict (with items + due_by).
    """
    # 1. Driver belongs to the account?
    driver = await platform_db.get_user_by_id(int(driver_id))
    if driver is None or driver.account_id != account_id:
        raise NotFound(f"driver {driver_id} not found")

    # 2. Open inspection check — fail-fast so the fleet doesn't queue
    #    two PTIs the driver can't tell apart in the mini-app.
    open_ins = await tenant_db.get_open_inspection_for_user(int(driver_id))
    if open_ins:
        raise WrongStatus(
            f"driver already has open inspection #{open_ins['id']} — "
            "ask them to complete or resend a reminder instead."
        )

    # 3. Template lookup.
    tpl = await tenant_db.get_active_template_with_items(
        account_id, vehicle_type, inspection_type,
    )
    if not tpl:
        raise NotFound(
            f"no active {vehicle_type} template for {inspection_type}"
        )

    # 4. Due-by default — caller may pass an explicit one (form picker).
    if not due_by:
        due_by = (
            datetime.now(timezone.utc) + timedelta(days=DEFAULT_CADENCE_DAYS)
        ).isoformat(timespec="seconds")

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ins_id = await tenant_db.create_inspection(
        account_id=account_id,
        user_id=int(driver_id),
        vehicle_name=vehicle_name,
        inspection_type=inspection_type,
        template_id=int(tpl["id"]),
        template_version=int(tpl["version"]),
        scheduled_for=now_iso,
        due_by=due_by,
    )
    await tenant_db.add_inspection_items(ins_id, tpl["items"])

    # Fire the bot reminder so the driver knows it's been assigned
    # right now (vs the next cron-tick reminder).  Lazy import keeps
    # the service decoupled from the bot module.
    try:
        from interfaces.bot.pti import notify_due_soon  # type: ignore[import-not-found]
        refreshed = await tenant_db.get_inspection(ins_id) or {}
        if refreshed:
            await notify_due_soon(account_id, [refreshed])
    except ImportError:
        pass
    except Exception:
        logger.exception("pti: ad-hoc create ping failed for ins_id=%d", ins_id)

    return await tenant_db.get_inspection_detail(ins_id, account_id=account_id) or {}


# ── Resend reminder (fleet manual nudge) ────────────────────────────


# In-memory throttle so fleet can't button-mash and spam the driver.
# Cleared at process restart; tighter throttles can land via Redis
# later if abuse becomes a real concern.
_LAST_MANUAL_REMIND: dict[int, datetime] = {}
_MANUAL_REMIND_COOLDOWN_SECONDS = 60 * 60   # 1 hour


async def resend_reminder(
    inspection_id: int,
    *,
    account_id: int,
    tenant_db,
) -> dict:
    """Re-ping the driver assigned to an open inspection.

    Throttled to once per hour per inspection so a fleet member tapping
    the button repeatedly doesn't generate a ping-storm on the driver's
    Telegram.

    Returns ``{sent: bool, throttled_until: iso8601 | None}``.
    """
    ins = await tenant_db.get_inspection(inspection_id, account_id=account_id)
    if not ins:
        raise NotFound(f"inspection {inspection_id} not found")
    status = (ins.get("status") or "").lower()
    if status not in ("scheduled", "in_progress", "revision_required"):
        raise WrongStatus(
            f"cannot resend reminder for inspection in '{status}' status"
        )

    # Cooldown check.
    last = _LAST_MANUAL_REMIND.get(int(inspection_id))
    now = datetime.now(timezone.utc)
    if last and (now - last).total_seconds() < _MANUAL_REMIND_COOLDOWN_SECONDS:
        next_ok = last + timedelta(seconds=_MANUAL_REMIND_COOLDOWN_SECONDS)
        return {
            "sent": False,
            "throttled_until": next_ok.isoformat(timespec="seconds"),
        }

    # Clear the cron-side throttle column so the next cron tick would
    # also pick this row up if the manual ping fails (defence in depth).
    try:
        await tenant_db._db.execute(
            "UPDATE driver_inspections SET alerted_at = NULL WHERE id = ?",
            (inspection_id,),
        )
        await tenant_db._db.commit()
    except Exception:
        logger.debug("pti: alerted_at clear failed for ins_id=%d", inspection_id)

    try:
        from interfaces.bot.pti import notify_due_soon  # type: ignore[import-not-found]
        await notify_due_soon(account_id, [ins])
    except ImportError:
        # Bot module unavailable — caller treats this as a server-side
        # ping failure, but the row is now eligible for the next cron
        # tick (alerted_at was cleared above).
        return {"sent": False, "throttled_until": None}
    except Exception:
        logger.exception(
            "pti: resend ping failed for ins_id=%d", inspection_id,
        )
        return {"sent": False, "throttled_until": None}

    _LAST_MANUAL_REMIND[int(inspection_id)] = now
    return {
        "sent": True,
        "throttled_until": (
            now + timedelta(seconds=_MANUAL_REMIND_COOLDOWN_SECONDS)
        ).isoformat(timespec="seconds"),
    }


# ── Submit ──────────────────────────────────────────────────────────


def _coords_valid(lat, lon) -> bool:
    """Sanity-bound a coordinate pair before persisting it."""
    try:
        latf, lonf = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    if latf == 0 and lonf == 0:
        return False   # null-island — almost always a missing fix
    return -90 <= latf <= 90 and -180 <= lonf <= 180


async def _resolve_and_save_location(
    ins: dict,
    inspection_id: int,
    device_location: Optional[tuple[float, float]],
    tenant_db,
) -> None:
    """Pick the best available position for the inspection + persist it.

    Order: vehicle telematics (preferred) → device GPS (fallback) →
    nothing.  Never raises — a location failure must not block a
    legitimate submit.
    """
    try:
        vehicle_name = (ins.get("vehicle_name") or "").strip()
        account_id = int(ins.get("account_id") or 0)
        if vehicle_name and account_id:
            try:
                rows = await tenant_db.get_vehicle_state(
                    account_id, vehicle_nums=[vehicle_name],
                )
            except Exception:
                rows = []
            for r in rows or []:
                if _coords_valid(r.get("lat"), r.get("lon")):
                    await tenant_db.save_inspection_location(
                        inspection_id,
                        lat=float(r["lat"]), lon=float(r["lon"]),
                        source="vehicle",
                    )
                    return
        # Fallback: browser GPS the Mini App passed up.
        if device_location and _coords_valid(*device_location):
            await tenant_db.save_inspection_location(
                inspection_id,
                lat=float(device_location[0]),
                lon=float(device_location[1]),
                source="device",
            )
    except Exception:
        logger.exception(
            "pti: location resolve failed for inspection_id=%d", inspection_id,
        )


async def submit_inspection(
    inspection_id: int,
    user_id: int,
    tenant_db,
    *,
    device_location: Optional[tuple[float, float]] = None,
) -> dict:
    """Driver finalizes the inspection.

    Validates:
      * Inspection exists + belongs to ``user_id``.
      * Status is ``in_progress`` or ``revision_required``.
      * Every ``required`` ``check`` item has a non-pending status.
      * Every ``required`` ``photo``/media item has a photo, and every
        ``document`` item has a document upload.

    ``device_location`` is an optional ``(lat, lon)`` from the browser
    GPS, used ONLY as a fallback when the vehicle has no recent
    telematics position.

    Returns ``{inspection_id, status, defects_count, has_oos_defect}``.
    """
    ins = await tenant_db.get_inspection(inspection_id)
    if not ins:
        raise NotFound(f"inspection {inspection_id} not found")
    if int(ins["user_id"]) != int(user_id):
        # Don't leak existence to other users.
        raise NotFound(f"inspection {inspection_id} not found")
    status = (ins.get("status") or "").lower()
    if status not in ("in_progress", "revision_required"):
        # ``scheduled`` rows must be marked in-progress first (route
        # does this implicitly on the first item update).  Re-submit
        # of an already-submitted inspection is a no-op error.
        raise WrongStatus(
            f"inspection in status '{status}' cannot be submitted"
        )

    items = await tenant_db.get_inspection_items(inspection_id)
    media = await tenant_db.list_inspection_media(inspection_id)

    defects, has_oos, pending = _summarize_items(items)
    if pending:
        raise IncompleteInspection(
            f"items still pending: {', '.join(pending)}"
        )
    missing = _items_missing_media(items, media)
    if missing:
        raise MissingMedia(
            f"items missing media: {', '.join(missing)}"
        )

    # Driver e-signature gate (DOT compliance).  The Mini App captures
    # the signature in a step BEFORE the final submit, so by the time
    # we get here the column should already be populated.  If it isn't
    # — the user tampered with the request, or the Mini App is on an
    # old build — refuse the submit and let the wizard bounce them
    # back to the signature pad.
    if not (ins.get("driver_signature") or "").strip():
        raise MissingSignature(
            "driver e-signature required before submit",
        )

    # Resolve WHERE the inspection happened before flipping status.
    # Vehicle telematics wins (more accurate + no device prompt); the
    # browser GPS is only a fallback for trailer-only / no-gateway rigs.
    await _resolve_and_save_location(
        ins, inspection_id, device_location, tenant_db,
    )

    ok = await tenant_db.submit_inspection(
        inspection_id, defects_count=defects, has_oos_defect=has_oos,
    )
    if not ok:
        # Concurrent submit by another tab — surface as wrong-status
        # so the client refreshes its view.
        raise WrongStatus("inspection already submitted")

    # Refresh row + fan out instant ping to fleet admins/reviewers.
    # Lazy import keeps the service layer independent of the bot
    # module (the bot wiring is optional during phased rollouts).
    refreshed = await tenant_db.get_inspection(inspection_id) or {}
    try:
        from interfaces.bot.pti import notify_submission_to_fleet  # type: ignore[import-not-found]
        await notify_submission_to_fleet(
            int(refreshed.get("account_id") or 0),
            refreshed,
        )
    except ImportError:
        pass
    except Exception:
        # Bot send failure must NOT bubble up — driver's submit
        # already succeeded.  Log + swallow.
        logger.exception(
            "pti: submission ping failed for inspection_id=%d",
            inspection_id,
        )

    return {
        "inspection_id": inspection_id,
        "status": "submitted",
        "defects_count": defects,
        "has_oos_defect": has_oos,
    }


# ── Signatures ──────────────────────────────────────────────────────


# Cheap guards on signature payloads.  We don't enforce a maximum
# canvas size — that's a UI concern — but we do refuse obvious junk
# (empty, ridiculously huge, or non-PNG payloads) before bloating the
# row.  100 KB of base64 ≈ 75 KB of binary, plenty of headroom for a
# 400×150 PNG.
_MIN_SIGNATURE_LEN = 200      # an empty canvas serializes to ~200 chars
_MAX_SIGNATURE_LEN = 200_000  # ~150 KB binary cap


def _validate_signature_payload(data: str) -> None:
    if not data or not isinstance(data, str):
        raise PTIError("signature data is required")
    if len(data) < _MIN_SIGNATURE_LEN:
        raise PTIError("signature appears blank")
    if len(data) > _MAX_SIGNATURE_LEN:
        raise PTIError("signature payload too large")
    if not data.startswith("data:image/png;base64,"):
        # Strict format keeps the dashboard's <img src={...}/> rendering
        # path trivial.  Mini App canvas.toDataURL('image/png') always
        # produces this exact prefix.
        raise PTIError("signature must be a PNG data URL")


# ── Annotated image payload validation ─────────────────────────────


# Driver-baked defect annotations live in ``pti_inspection_media`` —
# the route re-uses the same ObjectStorage path and just rewrites the
# blob.  Annotated PNGs can be substantially larger than signatures
# (the underlying photo is multi-megapixel) so the cap is wider, but
# we still refuse anything that exceeds the per-upload media limit
# enforced on the original capture path.
_MAX_ANNOTATED_BYTES = 25 * 1024 * 1024   # mirror _MAX_MEDIA_BYTES on the route side


def decode_annotated_png(data_url: str) -> bytes:
    """Validate + decode a PNG data URL produced by the Mini App's
    photo annotator.

    Returns the raw PNG bytes.  Raises ``PTIError`` if the payload is
    malformed or exceeds the per-image cap — same envelope as the
    signature validator so the route's error mapping covers both.
    """
    import base64
    import binascii
    if not data_url or not isinstance(data_url, str):
        raise PTIError("annotated image data is required")
    if not data_url.startswith("data:image/png;base64,"):
        raise PTIError("annotated image must be a PNG data URL")
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
    except (ValueError, binascii.Error) as e:
        raise PTIError(f"invalid base64 payload: {e}") from e
    if not raw:
        raise PTIError("annotated image is empty")
    if len(raw) > _MAX_ANNOTATED_BYTES:
        raise PTIError("annotated image payload too large")
    # Cheap PNG magic-number check — keeps a malicious caller from
    # sneaking JPEG/WebP bytes through under a PNG MIME prefix.
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PTIError("annotated image is not a valid PNG")
    return raw


async def set_signature(
    inspection_id: int,
    *,
    role: str,
    signature_data: str,
    user_id: int,
    account_id: int,
    tenant_db,
) -> dict:
    """Attach a captured e-signature to an inspection row.

    Authorization:
      * ``driver``   — must be the inspection's assigned driver and
                       the inspection must still be open (not yet
                       submitted/reviewed).  Re-signing is allowed
                       while in_progress/revision_required so a driver
                       who scribbled by accident can redo it.
      * ``reviewer`` — must be acting in the inspection's account and
                       the inspection must be in ``submitted`` status
                       (you can't co-sign before there's something to
                       co-sign on).

    Route is responsible for checking the caller's permissions; this
    layer enforces the inspection-state preconditions.
    """
    if role not in ("driver", "reviewer"):
        raise PTIError(f"unknown signature role: {role!r}")
    _validate_signature_payload(signature_data)

    ins = await tenant_db.get_inspection(inspection_id, account_id=account_id)
    if not ins:
        raise NotFound(f"inspection {inspection_id} not found")

    status = (ins.get("status") or "").lower()
    if role == "driver":
        if int(ins["user_id"]) != int(user_id):
            raise NotFound(f"inspection {inspection_id} not found")
        if status not in ("scheduled", "in_progress", "revision_required"):
            raise WrongStatus(
                f"driver cannot sign in status '{status}'",
            )
    else:  # reviewer
        if status != "submitted":
            raise WrongStatus(
                f"reviewer cannot sign in status '{status}'",
            )

    await tenant_db.set_inspection_signature(
        inspection_id, role=role, signature_data=signature_data,
    )
    return await tenant_db.get_inspection(inspection_id, account_id=account_id)


# ── Review ──────────────────────────────────────────────────────────


async def review_inspection(
    inspection_id: int,
    *,
    account_id: int,
    reviewer_id: int,
    review_status: str,
    review_notes: str = "",
    tenant_db,
) -> dict:
    """Fleet records the review outcome.

    Validates:
      * Inspection exists in the reviewer's account.
      * Status is ``submitted``.
      * ``review_status`` is one of the four valid outcomes.

    Returns the refreshed inspection row.

    Phase 4 hook: when ``review_status == 'needs_service'`` the
    capability layer can fan out to ``features.maintenance`` and
    spawn a maintenance task carrying the OOS items.  Not implemented
    in MVP; the fleet creates the task manually for now.
    """
    if review_status not in VALID_REVIEW_STATUSES:
        raise PTIError(f"invalid review_status: {review_status}")

    ins = await tenant_db.get_inspection(inspection_id, account_id=account_id)
    if not ins:
        raise NotFound(f"inspection {inspection_id} not found")
    if (ins.get("status") or "").lower() != "submitted":
        raise WrongStatus(
            f"inspection in status '{ins.get('status')}' cannot be reviewed"
        )

    ok = await tenant_db.review_inspection(
        inspection_id,
        reviewer_id=reviewer_id,
        review_status=review_status,
        review_notes=review_notes,
    )
    if not ok:
        raise WrongStatus("inspection already reviewed")

    return await tenant_db.get_inspection(inspection_id, account_id=account_id)


# ── Item update wrapper ─────────────────────────────────────────────


async def update_item_status(
    inspection_id: int,
    item_key: str,
    *,
    user_id: int,
    status: Optional[str] = None,
    notes: Optional[str] = None,
    tenant_db,
) -> dict:
    """Driver updates one item's status / notes.

    First-touch behaviour: if the inspection is ``scheduled``, flip it
    to ``in_progress`` so the route handler doesn't have to remember.
    Returns the refreshed item row.
    """
    if status is not None and status not in VALID_ITEM_STATUSES:
        raise PTIError(f"invalid item status: {status}")

    ins = await tenant_db.get_inspection(inspection_id)
    if not ins:
        raise NotFound(f"inspection {inspection_id} not found")
    if int(ins["user_id"]) != int(user_id):
        raise NotFound(f"inspection {inspection_id} not found")

    cur_status = (ins.get("status") or "").lower()
    if cur_status not in ("scheduled", "in_progress", "revision_required"):
        raise WrongStatus(
            f"inspection in status '{cur_status}' is read-only"
        )
    if cur_status == "scheduled":
        await tenant_db.mark_inspection_in_progress(inspection_id)

    item = await tenant_db.get_inspection_item_by_key(inspection_id, item_key)
    if not item:
        raise NotFound(f"item {item_key} not found on inspection {inspection_id}")

    await tenant_db.update_inspection_item(
        int(item["id"]), status=status, notes=notes,
    )
    refreshed = await tenant_db.get_inspection_item(int(item["id"]))
    return refreshed or {}


# ── Cron-driven helpers ─────────────────────────────────────────────


async def mark_overdue_inspections(account_id: int, tenant_db) -> list[dict]:
    """List the overdue inspections — caller uses the result to fan out
    a notification.  No status flip here: the driver's view is still
    actionable (they can still complete it), the bot just nags."""
    return await tenant_db.list_overdue(account_id)


async def list_due_soon(
    account_id: int, tenant_db,
    *, hours_ahead: int = DEFAULT_REMIND_AHEAD_HOURS,
) -> list[dict]:
    return await tenant_db.list_due_soon(account_id, hours_ahead=hours_ahead)


# ── Template reset helper (re-export with bound defaults) ───────────


async def reset_template_to_default(
    account_id: int,
    vehicle_type: str,
    tenant_db,
    *,
    inspection_type: str = "weekly",
) -> int:
    """Wraps ``tenant_db.reset_template_to_default`` with the right
    default item list pulled from ``features.inspections.templates``.

    Routes call this rather than the storage method directly so the
    item list source of truth stays in one place.
    """
    items = (
        STANDARD_DOT_TRUCK_ITEMS
        if vehicle_type == "truck"
        else STANDARD_DOT_TRAILER_ITEMS
    )
    return await tenant_db.reset_template_to_default(
        account_id, vehicle_type, inspection_type, items,
    )
