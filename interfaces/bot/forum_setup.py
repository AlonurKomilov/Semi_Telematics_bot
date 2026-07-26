"""Forum-routing setup commands for the bot.

Currently implements ``/setupforum`` (Phase 3 of the routing rollout).
Subsequent phases will add ``/topicsstatus``, ``/repairforum``,
``/resetforum`` and ``/connectforum`` next to it.

These commands run **inside the target Telegram group**, never in DM.
The chat_id Telegram puts on the incoming update IS the group we
bind, and the bot's preconditions are checked against that same
chat_id so admins never have to copy-paste an identifier.
"""

from __future__ import annotations

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from adapters.storage import ALERT_TYPE_KEYS
from capabilities.notifications.forum_client import (
    check_setup_preconditions,
    create_topic,
    delete_topic,
    edit_topic,
    rename_general,
    TopicAlreadyNamed,
)
from capabilities.notifications.forum_topics import FORUM_TOPIC_SPEC, TOPIC_BY_KEY
from interfaces.bot.auth import _get_user
from interfaces.bot.config import logger
from interfaces.bot.state import get_platform_db


# Forum-setup commands intentionally **bypass** the standard
# group_chat_guard (which requires the chat to be in
# ``authorized_chats``).  These commands ARE the binding step —
# gating them on prior authorization would be a chicken-and-egg.
# We do our own lighter check here: the caller must be a registered
# user with account-admin role, and (for the in-group commands) the
# bot must already have topic-manager rights in this chat.  On
# successful bind we also auto-insert the chat into
# ``authorized_chats`` so every other bot command works there going
# forward — admins shouldn't have to also run /addgroup after.
async def _resolve_admin_user(update, context):
    """Return the registered admin behind this update or None.

    When None: a short error message has already been pushed back to
    the caller explaining why — the caller should just ``return``.
    Tightly scoped to the forum-setup commands so the existing
    ``_require_registered`` semantics elsewhere are unchanged.
    """
    user, _tid, sys_owner = await _get_user(update)
    chat = update.effective_chat
    fallback_chat = chat.id if chat else (update.effective_user.id if update.effective_user else 0)

    if sys_owner and not user:
        await context.bot.send_message(
            chat_id=fallback_chat,
            text=("ℹ️ Forum routing is per-account. As system owner you don't have "
                  "an account context to bind. Run this command as an account admin."),
        )
        return None
    if not user:
        await context.bot.send_message(
            chat_id=fallback_chat,
            text=("❌ Register with the bot first (send <code>/start</code> in DM), "
                  "then come back to this group as an account admin."),
            parse_mode="HTML",
        )
        return None
    if not user.is_admin_or_above:
        await context.bot.send_message(
            chat_id=fallback_chat,
            text="❌ Only account admins/owners can configure alert routing.",
        )
        return None
    context.user_data["_db_user"] = user
    return user


async def _auto_authorize_chat(account_id: int, chat_id: int, chat_title: str, user_id: int) -> None:
    """Add a forum-bound chat to ``authorized_chats`` so the standard
    group_chat_guard lets every other bot command through.  Idempotent
    via the underlying ON CONFLICT in ``add_authorized_chat``.
    """
    try:
        await get_platform_db().add_authorized_chat(
            account_id=account_id, chat_id=chat_id,
            chat_title=chat_title, added_by=user_id,
        )
    except Exception as e:
        logger.warning("Auto-authorize of forum chat %d failed: %s", chat_id, e)


async def handle_forum_service_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Index forum-topic events the bot observes.

    Telegram dispatches a service message (``forum_topic_created`` /
    ``forum_topic_edited`` / ``forum_topic_deleted``) to every member
    of a forum group whenever a topic changes.  We catch those here
    and maintain a (chat_id, thread_id, name) index so subsequent
    ``/setupforum`` and ``/repairforum`` runs can find existing
    topics by name and adopt them instead of creating duplicates.

    Telegram's Bot API has no method to enumerate existing topics,
    so this passive indexing is the only mechanism the bot has to
    learn what's already in a forum chat.
    """
    msg = update.message
    if msg is None or msg.chat is None:
        return
    thread_id = msg.message_thread_id
    if thread_id is None:
        return
    db = get_platform_db()
    try:
        if msg.forum_topic_created is not None:
            await db.record_forum_topic_seen(
                msg.chat.id, thread_id, msg.forum_topic_created.name,
            )
        elif msg.forum_topic_edited is not None:
            new_name = msg.forum_topic_edited.name
            if new_name:
                await db.record_forum_topic_seen(
                    msg.chat.id, thread_id, new_name,
                )
        elif getattr(msg, "forum_topic_closed", None) is not None:
            # Closed != deleted; the topic still exists.  No action.
            return
        else:
            # The dispatch filter shouldn't deliver other event
            # types but ignore safely if it does.
            return
        logger.debug(
            "Indexed forum topic event: chat=%d thread=%d",
            msg.chat.id, thread_id,
        )
    except Exception as e:
        logger.debug("Forum topic index update failed: %s", e)


# The default "General" topic gets renamed to this once setup
# succeeds, so members have a clean chat space separate from the
# bot's purpose-specific topics.  Plain-text to match the rest of
# the topic catalogue (Telegram already draws a "T" auto-icon).
_GENERAL_RENAME = "Team Chat"


async def _topic_exists(
    bot, chat_id: int, message_thread_id: int, current_name: str,
) -> bool:
    """Probe whether a forum topic still exists on Telegram's side.

    The Bot API has no ``getForumTopic`` method, so the cheapest
    probe is a no-op ``edit_forum_topic`` with the topic's current
    name.  Outcomes:

      • Returns 200 OK or ``Topic_not_modified``  → topic exists
      • Returns ``MESSAGE_THREAD_NOT_FOUND`` /
        ``TOPIC_DELETED`` / ``TOPIC_CLOSED`` / similar → topic gone
      • Any other 400                              → treat as gone
        (better to re-create a route than leave a stale one
        silently failing every alert delivery)

    The probe is silent: passing the same name doesn't emit a
    "topic edited" service message, so members see nothing.
    Returns False for the placeholder sentinel ``message_thread_id=0``
    (left by previous timed-out creates).
    """
    if message_thread_id <= 0:
        return False
    try:
        await edit_topic(bot, chat_id, message_thread_id,
                         name=current_name or "Topic")
        return True
    except TopicAlreadyNamed:
        # Telegram explicitly told us "name unchanged" — topic exists.
        return True
    except BadRequest as e:
        msg_raw = str(e)
        msg = msg_raw.lower().replace(" ", "_")

        # Belt-and-suspenders second pass on the not-modified case in
        # case the wrapper's pattern missed a Telegram variant.
        if "not_modified" in msg:
            return True

        # Known deletion / "topic-gone" signals.  We log at INFO so
        # the action is visible in the bot log without flipping log
        # level to DEBUG.
        deletion_signals = (
            "not_found", "topic_deleted", "thread_invalid",
            "topic_invalid", "message_thread_not_found",
            "topic_closed", "thread_not_found",
        )
        if any(sig in msg for sig in deletion_signals):
            logger.info(
                "Topic probe → DELETED  chat=%d thread=%d Telegram: %s",
                chat_id, message_thread_id, msg_raw,
            )
            return False

        # Unknown 400 — log loudly so we can refine the heuristic,
        # but err on the side of "gone" since the topic isn't usable
        # if Telegram refuses a no-op edit on it.  Worst case the
        # bot re-creates the topic; far better than alerts silently
        # failing every 5 minutes.
        logger.warning(
            "Topic probe → assuming DELETED on unknown 400  "
            "chat=%d thread=%d Telegram: %s",
            chat_id, message_thread_id, msg_raw,
        )
        return False
    except Exception as e:
        # Network / timeout — treat as "still there" so a transient
        # outage doesn't wipe healthy routes.
        logger.warning(
            "Topic probe network error  chat=%d thread=%d err=%r — "
            "leaving route intact this run.",
            chat_id, message_thread_id, e,
        )
        return True


async def cmd_setup_forum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bind the current Telegram supergroup to this account and create
    the 11 Option-C alert topics.

    Idempotent — re-running skips topics that already have a route.
    The follow-up ``/repairforum`` command handles topics deleted by
    a human admin after initial setup.
    """
    user = await _resolve_admin_user(update, context)
    if user is None:
        return
    chat = update.effective_chat

    # ── 1. Must run inside a group, not a DM ─────────────────────
    if chat is None or chat.type not in ("supergroup", "group"):
        await context.bot.send_message(
            chat_id=chat.id if chat else user.telegram_id,
            text=(
                "❌ Run /setupforum <b>inside the Telegram group</b> you want to "
                "bind, not in a direct message.\n\n"
                "If you haven't created the group yet:\n"
                "1. Create a new Telegram group.\n"
                "2. Open the group's settings → Topics → toggle <b>Enable Topics</b>.\n"
                "3. Add @"
                + (context.bot.username or "the bot")
                + " and promote it to admin with the 'Manage Topics' right.\n"
                "4. Send <code>/setupforum</code> in the group."
            ),
            parse_mode="HTML",
        )
        return

    # (admin check already enforced by _resolve_admin_user)
    chat_id = chat.id

    # ── 3. Verify the bot has the rights it needs ────────────────
    pre = await check_setup_preconditions(context.bot, chat_id)
    if not pre.ok:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ <b>Cannot run setup yet.</b>\n\n{pre.error_hint}",
            parse_mode="HTML",
        )
        return

    # ── 4. Reject if another account already owns this group ─────
    db = get_platform_db()
    existing = await db.get_forum_group_by_chat(chat_id)
    if existing and existing.account_id != user.account_id:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "❌ This group is already bound to a different account "
                f"(account #{existing.account_id}). Use <code>/resetforum</code> "
                "from that account first, or bind a different group."
            ),
            parse_mode="HTML",
        )
        return

    # ── 5. Record (or refresh) the binding before creating topics
    #      so a partial failure still leaves the row in place.
    await db.upsert_forum_group(
        account_id=user.account_id,
        chat_id=chat_id,
        chat_title=pre.chat_title,
        is_forum_enabled=True,
        created_by_user_id=user.id,
        setup_status="pending",
    )

    # Also register this chat in authorized_chats so the standard
    # group_chat_guard lets every other bot command (/users,
    # /faults, etc.) through here — admins shouldn't need a separate
    # /addgroup step after binding the forum.
    await _auto_authorize_chat(user.account_id, chat_id, pre.chat_title, user.id)

    # ── 6. Announce start ─────────────────────────────────────────
    await context.bot.send_message(
        chat_id=chat_id,
        text="🤖 <b>Setting up 4truck alert topics…</b>",
        parse_mode="HTML",
    )

    # ── 7. Create every topic that doesn't already have a route ──
    created: list[str] = []
    adopted: list[str] = []
    skipped: list[str] = []
    errors: list[tuple[str, str]] = []

    for spec in FORUM_TOPIC_SPEC:
        existing_route = await db.get_alert_route(user.account_id, spec.key)
        if existing_route:
            skipped.append(spec.name)
            continue

        # Adopt-before-create: if the bot has previously observed a
        # topic with this name in this chat (via the service-message
        # index), reuse it instead of creating a duplicate.  Common
        # case: an earlier setup/repair timed out client-side after
        # the topic was already created on Telegram's side.
        try:
            existing_thread = await db.find_forum_topic_by_name(
                chat_id, spec.name,
            )
        except Exception:
            existing_thread = None
        if existing_thread is not None:
            await db.upsert_alert_route(
                account_id=user.account_id,
                alert_type=spec.key,
                chat_id=chat_id,
                message_thread_id=existing_thread,
                topic_name_snapshot=spec.name,
                icon_emoji=spec.icon_emoji,
            )
            adopted.append(spec.name)
            continue

        try:
            thread_id = await create_topic(
                context.bot, chat_id,
                name=spec.name, icon_color=spec.icon_color,
            )
            # Belt-and-suspenders: index our own creation so later
            # runs don't accidentally re-create even if the service
            # message is missed.
            try:
                await db.record_forum_topic_seen(chat_id, thread_id, spec.name)
            except Exception:
                pass
            await db.upsert_alert_route(
                account_id=user.account_id,
                alert_type=spec.key,
                chat_id=chat_id,
                message_thread_id=thread_id,
                topic_name_snapshot=spec.name,
                icon_emoji=spec.icon_emoji,
            )
            created.append(spec.name)
        except Exception as e:
            logger.exception("Failed to create topic %s in chat %d", spec.name, chat_id)
            errors.append((spec.name, str(e)))

    # ── 8. Try to rename General → Team Chat (non-fatal on failure)
    general_renamed = False
    if not errors:
        try:
            await rename_general(context.bot, chat_id, _GENERAL_RENAME)
            general_renamed = True
        except Exception as e:
            logger.debug("rename_general failed on chat %d: %s", chat_id, e)

    # ── 9. Persist the final setup_status ────────────────────────
    final_status = "provisioned" if not errors else "partial"
    await db.mark_forum_group_status(
        user.account_id, final_status, bump_setup=True,
    )

    # ── 10. Reply with a tidy summary ────────────────────────────
    lines: list[str] = []
    if created:
        lines.append(f"✅ Created {len(created)} topic(s):")
        lines.extend(f"  • {n}" for n in created)
    if adopted:
        lines.append(f"\n🔗 Adopted {len(adopted)} existing topic(s):")
        lines.extend(f"  • {n}" for n in adopted)
    if skipped:
        lines.append(f"\n↩️ Skipped {len(skipped)} (already existed):")
        lines.extend(f"  • {n}" for n in skipped)
    if errors:
        lines.append(f"\n⚠️ {len(errors)} error(s):")
        lines.extend(f"  • {n}: {err[:80]}" for n, err in errors)
        lines.append("\nRun <code>/repairforum</code> to retry the missing ones.")
    if general_renamed:
        lines.append(f"\n📝 Renamed General → <b>{_GENERAL_RENAME}</b>")

    lines.append(
        "\n\n💡 <b>Tip:</b> have each member long-press the topics they care about "
        "most and pin them, so alerts surface at the top of the topic list."
    )

    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        parse_mode="HTML",
    )

    logger.info(
        "/setupforum acct=%d chat=%d status=%s created=%d skipped=%d errors=%d",
        user.account_id, chat_id, final_status,
        len(created), len(skipped), len(errors),
    )


# Sanity: every canonical alert key in the storage layer must have
# a matching topic spec, or /setupforum would silently skip it.
def _validate_catalog() -> None:
    missing = set(ALERT_TYPE_KEYS) - set(TOPIC_BY_KEY)
    extra = set(TOPIC_BY_KEY) - set(ALERT_TYPE_KEYS)
    if missing or extra:
        raise RuntimeError(
            f"forum topic catalog drift: missing={missing} extra={extra}"
        )


_validate_catalog()


# ── /topicsstatus ─────────────────────────────────────────────

async def cmd_topics_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the routing health: which topics are mapped, missing, or
    drifting (renamed by an admin).  Runs in DM or in the group —
    either way reports against the user's account.
    """
    user = await _resolve_admin_user(update, context)
    if user is None:
        return
    db = get_platform_db()
    group = await db.get_forum_group(user.account_id)

    if group is None:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                "ℹ️ Forum routing is not configured for this account.\n\n"
                "Run <code>/setupforum</code> inside a Telegram group with "
                "Topics enabled and the bot promoted as a topic-manager admin."
            ),
            parse_mode="HTML",
        )
        return

    routes = await db.list_alert_routes(user.account_id)
    by_key = {r.alert_type: r for r in routes}

    lines = [
        f"📋 <b>Forum routing — {group.chat_title}</b>",
        f"Status: <b>{group.setup_status}</b>",
        f"Group chat id: <code>{group.chat_id}</code>",
        "",
    ]

    mapped = 0
    inactive = 0
    missing: list[str] = []

    for spec in FORUM_TOPIC_SPEC:
        r = by_key.get(spec.key)
        if r is None:
            lines.append(f"  ❌  {spec.name} — <i>not provisioned</i>")
            missing.append(spec.name)
        elif not r.is_active:
            lines.append(f"  ⏸  {spec.name} — <i>disabled (falls back to DM)</i>")
            inactive += 1
        else:
            drift = ""
            if r.topic_name_snapshot and r.topic_name_snapshot != spec.name:
                drift = f" <i>(renamed: was '{r.topic_name_snapshot}')</i>"
            lines.append(f"  ✅  {spec.name} → thread <code>{r.message_thread_id}</code>{drift}")
            mapped += 1

    lines.append("")
    lines.append(f"Mapped: <b>{mapped}</b> · Disabled: <b>{inactive}</b> · Missing: <b>{len(missing)}</b>")

    if missing:
        lines.append("\nRun <code>/repairforum</code> to re-create missing topics.")
    lines.append(
        "\n<i>Note: this report reflects the bot's database. "
        "Run <code>/repairforum</code> to verify each topic still exists on "
        "Telegram (detects manual deletions and re-creates the topic).</i>"
    )

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\n".join(lines),
        parse_mode="HTML",
    )


# ── /repairforum ──────────────────────────────────────────────

async def cmd_repair_forum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-create any topics that disappeared (admin-deleted) or never
    got created (partial setup failure).  Idempotent — never touches
    topics that already have a working route.
    """
    user = await _resolve_admin_user(update, context)
    if user is None:
        return
    chat = update.effective_chat

    if chat is None or chat.type not in ("supergroup", "group"):
        await context.bot.send_message(
            chat_id=chat.id if chat else user.telegram_id,
            text=("❌ Run /repairforum <b>inside the bound group</b>, not in DM."),
            parse_mode="HTML",
        )
        return

    db = get_platform_db()
    group = await db.get_forum_group(user.account_id)
    if group is None or group.chat_id != chat.id:
        await context.bot.send_message(
            chat_id=chat.id,
            text=(
                "❌ This group is not the bound forum for your account. "
                "Run <code>/setupforum</code> here first if you want to bind it."
            ),
            parse_mode="HTML",
        )
        return

    pre = await check_setup_preconditions(context.bot, chat.id)
    if not pre.ok:
        await context.bot.send_message(
            chat_id=chat.id,
            text=f"❌ Cannot repair yet.\n\n{pre.error_hint}",
            parse_mode="HTML",
        )
        return

    await context.bot.send_message(
        chat_id=chat.id,
        text="🔧 <b>Repairing alert-routing topics…</b>",
        parse_mode="HTML",
    )

    repaired:      list[str] = []   # newly-created topics (never existed before)
    adopted:       list[str] = []   # existing topics found via the seen-index
    renamed:       list[str] = []   # existing topics whose name now matches the spec
    healed:        list[str] = []   # admin had deleted topic in TG; we re-created
    skipped:       list[str] = []   # already healthy AND already named correctly
    errors:        list[tuple[str, str]] = []

    # Spec keys whose existing route was probed-as-deleted in this
    # run.  Used so the adopt-or-create branch below puts the
    # eventual outcome into ``healed`` instead of ``repaired`` /
    # ``adopted``, avoiding a double-count in the user-facing
    # summary ("repaired 1" + "healed 1" for the same topic).
    recovering: set[str] = set()

    for spec in FORUM_TOPIC_SPEC:
        route = await db.get_alert_route(user.account_id, spec.key)
        if route is not None:
            # Probe Telegram to verify the topic still exists — the
            # admin may have deleted it in the client.  Without this
            # check, /repairforum would happily report "all healthy"
            # while the DB pointed at a ghost thread_id.
            current_name = route.topic_name_snapshot or spec.name
            still_exists = await _topic_exists(
                context.bot, chat.id, route.message_thread_id, current_name,
            )
            if not still_exists:
                logger.info(
                    "Topic %s (thread=%d) was deleted in Telegram; "
                    "purging stale route and recreating.",
                    spec.name, route.message_thread_id,
                )
                try:
                    await db.delete_alert_route(user.account_id, spec.key)
                    await db.forget_forum_topic(
                        chat.id, route.message_thread_id,
                    )
                except Exception:
                    logger.exception(
                        "Cleanup of stale route %s failed", spec.name,
                    )
                recovering.add(spec.key)
                route = None
                # Fall through to the adopt-or-create branch — when
                # it eventually appends a name, the recovering set
                # below redirects that name to ``healed`` instead.
            else:
                # Existing route + topic confirmed alive.  Handle any
                # rename drift between the DB snapshot and the spec.
                if route.topic_name_snapshot != spec.name:
                    try:
                        try:
                            await edit_topic(
                                context.bot, chat.id, route.message_thread_id,
                                name=spec.name,
                            )
                        except TopicAlreadyNamed:
                            logger.info(
                                "Topic %s already named correctly on "
                                "Telegram; just syncing snapshot.", spec.name,
                            )
                        await db.upsert_alert_route(
                            account_id=user.account_id,
                            alert_type=spec.key,
                            chat_id=chat.id,
                            message_thread_id=route.message_thread_id,
                            topic_name_snapshot=spec.name,
                            icon_emoji=spec.icon_emoji,
                        )
                        renamed.append(spec.name)
                    except Exception as e:
                        logger.exception("Rename failed for topic %s", spec.name)
                        errors.append((spec.name, f"rename: {e}"))
                else:
                    skipped.append(spec.name)
                continue
        # route is None here (either never existed OR was probed-as-deleted) →

        # Adopt-before-create: a previous timed-out attempt may have
        # already created this topic on Telegram's side.  The seen-
        # index is populated by service-message events (and by the
        # bot's own create calls), so check there before paying for
        # another createForumTopic that risks duplicating.
        try:
            existing_thread = await db.find_forum_topic_by_name(
                chat.id, spec.name,
            )
        except Exception:
            existing_thread = None
        if existing_thread is not None and existing_thread > 0:
            await db.upsert_alert_route(
                account_id=user.account_id,
                alert_type=spec.key,
                chat_id=chat.id,
                message_thread_id=existing_thread,
                topic_name_snapshot=spec.name,
                icon_emoji=spec.icon_emoji,
            )
            (healed if spec.key in recovering else adopted).append(spec.name)
            continue
        try:
            thread_id = await create_topic(
                context.bot, chat.id,
                name=spec.name, icon_color=spec.icon_color,
            )
            try:
                await db.record_forum_topic_seen(
                    chat.id, thread_id, spec.name,
                )
            except Exception:
                pass
            await db.upsert_alert_route(
                account_id=user.account_id,
                alert_type=spec.key,
                chat_id=chat.id,
                message_thread_id=thread_id,
                topic_name_snapshot=spec.name,
                icon_emoji=spec.icon_emoji,
            )
            (healed if spec.key in recovering else repaired).append(spec.name)
        except Exception as e:
            # Telegram's createForumTopic occasionally times out
            # client-side AFTER the topic was actually created server-
            # side.  Without a placeholder, the next /repairforum
            # would create another duplicate.  We insert a
            # placeholder route (message_thread_id=0, is_active=0)
            # that subsequent runs skip — the admin manually cleans
            # up the orphan topic + deletes the placeholder via
            # /resetforum or the dashboard before re-trying.
            error_msg = str(e)
            is_timeout = ("timed out" in error_msg.lower()
                          or "timedout" in type(e).__name__.lower())
            if is_timeout:
                try:
                    await db.upsert_alert_route(
                        account_id=user.account_id,
                        alert_type=spec.key,
                        chat_id=chat.id,
                        message_thread_id=0,           # sentinel for "unknown"
                        topic_name_snapshot=spec.name,
                        icon_emoji=spec.icon_emoji,
                    )
                    await db.set_alert_route_active(
                        user.account_id, spec.key, False,
                    )
                    logger.warning(
                        "create_topic for %s timed out — inserted "
                        "placeholder (inactive) to prevent duplicate "
                        "retries.  Admin must clean orphan topic + "
                        "re-run /repairforum after manual cleanup.",
                        spec.name,
                    )
                except Exception:
                    logger.exception("Failed to insert placeholder for %s", spec.name)
            logger.exception("Repair failed for topic %s", spec.name)
            errors.append((spec.name, error_msg[:120]))

    final_status = "provisioned" if not errors else "partial"
    await db.mark_forum_group_status(
        user.account_id, final_status, bump_repair=True,
    )

    parts: list[str] = []
    if healed:
        parts.append(f"🩹 Detected {len(healed)} deleted topic(s) — re-created:")
        parts.extend(f"  • {n}" for n in healed)
    if repaired:
        parts.append(f"\n✅ Re-created {len(repaired)} missing topic(s):")
        parts.extend(f"  • {n}" for n in repaired)
    if adopted:
        parts.append(f"\n🔗 Adopted {len(adopted)} existing topic(s) "
                     "(no duplicate created):")
        parts.extend(f"  • {n}" for n in adopted)
    if renamed:
        parts.append(f"\n📝 Renamed {len(renamed)} topic(s) to current spec:")
        parts.extend(f"  • {n}" for n in renamed)
    if skipped:
        parts.append(f"\n↩️ Skipped {len(skipped)} (already healthy)")
    if errors:
        parts.append(f"\n⚠️ {len(errors)} error(s):")
        parts.extend(f"  • {n}: {err[:80]}" for n, err in errors)
    if not (repaired or adopted or renamed or healed or errors):
        parts.append("✅ All topics already healthy — nothing to repair.")

    await context.bot.send_message(
        chat_id=chat.id,
        text="\n".join(parts),
        parse_mode="HTML",
    )

    logger.info(
        "/repairforum acct=%d chat=%d repaired=%d adopted=%d healed=%d "
        "renamed=%d skipped=%d errors=%d",
        user.account_id, chat.id, len(repaired), len(adopted), len(healed),
        len(renamed), len(skipped), len(errors),
    )


# ── /resetforum ───────────────────────────────────────────────

# In-memory pending-confirmation state.  Admins must run /resetforum
# twice within 60 seconds for the second invocation to actually act.
# Keyed by (account_id) to scope the confirmation per tenant — two
# different accounts confirming simultaneously don't collide.
_PENDING_RESET: dict[int, float] = {}
_RESET_CONFIRM_WINDOW_SEC = 60.0


async def cmd_reset_forum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete every bot-managed topic + wipe the routing table.

    Two-step confirm: first invocation arms the confirmation; second
    invocation within 60 s actually runs the deletion.  Topics on
    Telegram are deleted via the Bot API; if a delete fails we still
    drop the storage row so the system is consistent for the next
    /setupforum.
    """
    import time
    user = await _resolve_admin_user(update, context)
    if user is None:
        return
    chat = update.effective_chat

    if chat is None or chat.type not in ("supergroup", "group"):
        await context.bot.send_message(
            chat_id=chat.id if chat else user.telegram_id,
            text=("❌ Run /resetforum <b>inside the bound group</b>."),
            parse_mode="HTML",
        )
        return

    db = get_platform_db()
    group = await db.get_forum_group(user.account_id)
    if group is None or group.chat_id != chat.id:
        await context.bot.send_message(
            chat_id=chat.id,
            text="❌ This group is not the bound forum for your account.",
        )
        return

    # Two-step confirm
    now = time.time()
    armed_at = _PENDING_RESET.get(user.account_id)
    if armed_at is None or (now - armed_at) > _RESET_CONFIRM_WINDOW_SEC:
        _PENDING_RESET[user.account_id] = now
        await context.bot.send_message(
            chat_id=chat.id,
            text=(
                "⚠️ <b>This will delete every bot-managed topic</b> in this "
                "group and clear the alert-routing table.\n\n"
                "Run <code>/resetforum</code> <b>again within 60 seconds</b> "
                "to confirm.  Re-running <code>/setupforum</code> afterward "
                "re-creates the topics from scratch."
            ),
            parse_mode="HTML",
        )
        return

    # Second invocation → execute
    _PENDING_RESET.pop(user.account_id, None)

    routes = await db.list_alert_routes(user.account_id)
    deleted = 0
    errors: list[tuple[str, str]] = []

    for route in routes:
        try:
            await delete_topic(context.bot, route.chat_id, route.message_thread_id)
            deleted += 1
        except Exception as e:
            logger.debug("delete_topic failed for %s: %s", route.alert_type, e)
            errors.append((route.alert_type, str(e)))

    # Always wipe the storage rows even if some Telegram deletes
    # failed — leaving stale routes pointing at deleted topics is
    # worse than a couple of orphaned topics the admin can remove
    # manually.
    await db.delete_forum_group(user.account_id)

    lines = [f"♻️ Reset complete — deleted {deleted} topic(s)."]
    if errors:
        lines.append(f"⚠️ {len(errors)} couldn't be deleted from Telegram:")
        lines.extend(f"  • {k}: {e[:60]}" for k, e in errors)
        lines.append("\nThe routing table is cleared regardless. "
                     "Remove the leftover topics manually if needed.")
    lines.append("\nRun <code>/setupforum</code> to re-create the routing.")

    await context.bot.send_message(
        chat_id=chat.id, text="\n".join(lines), parse_mode="HTML",
    )

    logger.info(
        "/resetforum acct=%d chat=%d deleted=%d errors=%d",
        user.account_id, chat.id, deleted, len(errors),
    )


# ── /connectforum ─────────────────────────────────────────────

async def cmd_connect_forum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bind this group to the account WITHOUT auto-creating topics.

    Use when an admin has already curated the topic list manually and
    just wants to map existing topics one-by-one via the dashboard
    UI.  After /connectforum the account is bound and shows up as
    'pending' until the admin assigns topic threads or runs
    /setupforum to backfill.
    """
    user = await _resolve_admin_user(update, context)
    if user is None:
        return
    chat = update.effective_chat

    if chat is None or chat.type not in ("supergroup", "group"):
        await context.bot.send_message(
            chat_id=chat.id if chat else user.telegram_id,
            text="❌ Run /connectforum <b>inside the Telegram group</b>.",
            parse_mode="HTML",
        )
        return

    pre = await check_setup_preconditions(context.bot, chat.id)
    if not pre.ok:
        await context.bot.send_message(
            chat_id=chat.id,
            text=f"❌ Cannot connect.\n\n{pre.error_hint}",
            parse_mode="HTML",
        )
        return

    db = get_platform_db()
    existing = await db.get_forum_group_by_chat(chat.id)
    if existing and existing.account_id != user.account_id:
        await context.bot.send_message(
            chat_id=chat.id,
            text=("❌ Already bound to account "
                  f"#{existing.account_id}. Reset there first."),
        )
        return

    await db.upsert_forum_group(
        account_id=user.account_id,
        chat_id=chat.id,
        chat_title=pre.chat_title,
        is_forum_enabled=True,
        created_by_user_id=user.id,
        setup_status="pending",
    )
    await _auto_authorize_chat(user.account_id, chat.id, pre.chat_title, user.id)

    await context.bot.send_message(
        chat_id=chat.id,
        text=(
            f"🔗 <b>Connected</b> — {pre.chat_title} is now bound to your account.\n\n"
            "No topics created — assign each alert type to a topic from the "
            "admin dashboard, or run <code>/setupforum</code> to auto-create the "
            "11 default topics."
        ),
        parse_mode="HTML",
    )

    logger.info("/connectforum acct=%d chat=%d", user.account_id, chat.id)


# ── /testforum ────────────────────────────────────────────────

async def cmd_test_forum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a sample alert to each mapped topic — proves the routing
    pipeline end-to-end without waiting for real fault / fuel / event
    activity.  Useful right after ``/setupforum`` to confirm every
    topic actually receives messages from the bot.
    """
    import asyncio as _asyncio
    user = await _resolve_admin_user(update, context)
    if user is None:
        return
    chat = update.effective_chat
    if chat is None:
        return

    db = get_platform_db()
    group = await db.get_forum_group(user.account_id)
    if group is None:
        await context.bot.send_message(
            chat_id=chat.id,
            text=("❌ No forum group bound to your account. "
                  "Run <code>/setupforum</code> first."),
            parse_mode="HTML",
        )
        return

    routes = await db.list_alert_routes(user.account_id)
    active_routes = [r for r in routes if r.is_active and r.message_thread_id > 0]
    if not active_routes:
        await context.bot.send_message(
            chat_id=chat.id,
            text="❌ No active routes to test. Run <code>/repairforum</code> first.",
            parse_mode="HTML",
        )
        return

    # Up-front "🧪 Testing N topic(s)…" reply attached to the user's
    # /testforum message so they get immediate feedback even before
    # the loop finishes (avoids the "did anything happen?" silence
    # while we pace 11 posts to stay under Telegram's rate limit).
    starter_msg_id = None
    starter_text = (
        f"🧪 <b>Testing {len(active_routes)} topic(s)…</b>\n"
        "<i>Posting one test message per topic, pacing to stay under "
        "Telegram's flood limit. Summary follows.</i>"
    )
    try:
        starter = await context.bot.send_message(
            chat_id=chat.id, text=starter_text, parse_mode="HTML",
            reply_to_message_id=update.message.message_id if update.message else None,
        )
        starter_msg_id = starter.message_id
    except Exception as e:
        logger.debug("testforum: starter message failed: %s", e)

    from capabilities.notifications.forum_topics import TOPIC_BY_KEY
    sent: list[str] = []
    failed: list[tuple[str, str]] = []

    # Telegram allows ~30 messages/sec global per bot but rate-limits
    # individual chats at ~20/min.  11 sequential posts with a 700 ms
    # gap fits well inside both windows; a smaller gap risks the
    # 429 "Flood control exceeded" we saw on the first /testforum run.
    GAP_SECONDS = 0.7

    for idx, route in enumerate(active_routes):
        spec = TOPIC_BY_KEY.get(route.alert_type)
        name = spec.name if spec else route.alert_type
        emoji = spec.icon_emoji if spec else "🧪"
        try:
            await context.bot.send_message(
                chat_id=route.chat_id,
                message_thread_id=route.message_thread_id,
                text=(
                    f"{emoji} <b>Test alert — {name}</b>\n\n"
                    "This is a test post from <code>/testforum</code>.\n"
                    "If you can see this, the routing for this topic is working.\n\n"
                    "<i>Real alerts for this category will arrive here automatically.</i>"
                ),
                parse_mode="HTML",
            )
            sent.append(name)
        except Exception as e:
            logger.warning(
                "testforum: failed to post to %s (thread=%d): %s",
                route.alert_type, route.message_thread_id, e,
            )
            failed.append((name, str(e)[:80]))

        # Don't sleep after the last iteration.
        if idx < len(active_routes) - 1:
            await _asyncio.sleep(GAP_SECONDS)

    # ── Build the summary ────────────────────────────────────────
    lines: list[str] = [f"🧪 <b>Test posts complete — {len(sent)}/{len(active_routes)} delivered</b>"]
    if sent:
        lines.append(f"\n✅ Posted to {len(sent)} topic(s):")
        lines.extend(f"  • {n}" for n in sent)
    if failed:
        lines.append(f"\n❌ Failed for {len(failed)} topic(s):")
        lines.extend(f"  • {n}: {err}" for n, err in failed)
        lines.append(
            "\nMost common causes:\n"
            "  • Topic was deleted in Telegram → run <code>/repairforum</code>\n"
            "  • Flood control hit during the test → wait 60s and try again\n"
            "  • Bot lost 'Manage Topics' permission → re-grant in group admins"
        )
    summary_text = "\n".join(lines)

    # ── Deliver the summary, with graceful fallbacks ─────────────
    # 1. Best: edit the starter message in place (no extra noise).
    # 2. Else: send fresh into the group, replying to the user's /testforum.
    # 3. Last resort: DM the user directly so they aren't left guessing.
    delivered_via = None
    if starter_msg_id is not None:
        try:
            await context.bot.edit_message_text(
                chat_id=chat.id, message_id=starter_msg_id,
                text=summary_text, parse_mode="HTML",
            )
            delivered_via = "edit"
        except Exception as e:
            logger.debug("testforum: summary edit failed: %s", e)
    if delivered_via is None:
        try:
            await context.bot.send_message(
                chat_id=chat.id, text=summary_text, parse_mode="HTML",
                reply_to_message_id=update.message.message_id if update.message else None,
            )
            delivered_via = "group"
        except Exception as e:
            logger.warning("testforum: summary send to group failed: %s", e)
    if delivered_via is None:
        try:
            await context.bot.send_message(
                chat_id=user.telegram_id,
                text=("⚠️ Couldn't post the /testforum summary in the group "
                      "(rate-limited or permissions). Here it is:\n\n"
                      + summary_text),
                parse_mode="HTML",
            )
            delivered_via = "dm"
        except Exception as e:
            logger.error("testforum: summary delivery failed entirely: %s", e)
            delivered_via = "none"

    logger.info(
        "/testforum acct=%d chat=%d sent=%d failed=%d summary=%s",
        user.account_id, chat.id, len(sent), len(failed), delivered_via,
    )
