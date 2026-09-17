"""Additive Chat schema and transactional Team Management history projection.

Postgres functions deliberately run natively: the portable script splitter
cannot parse PL/pgSQL. No application service calls this migration directly.
"""

SCHEMA_SQL = r"""
CREATE UNIQUE INDEX IF NOT EXISTS users_chat_account_id ON users(account_id, id);

CREATE TABLE IF NOT EXISTS chat_conversations (
    id TEXT PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id),
    kind TEXT NOT NULL CHECK (kind IN ('account', 'roles', 'direct')),
    title TEXT NOT NULL DEFAULT '' CHECK (char_length(title) <= 80),
    description TEXT NOT NULL DEFAULT '' CHECK (char_length(description) <= 300),
    owner_user_id BIGINT,
    system_key TEXT CHECK (system_key = 'general'),
    direct_low_user_id BIGINT,
    direct_high_user_id BIGINT,
    posting_mode TEXT NOT NULL DEFAULT 'everyone' CHECK (posting_mode IN ('everyone', 'admins')),
    new_member_history TEXT NOT NULL DEFAULT 'all' CHECK (new_member_history IN ('all', 'since_join')),
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
    message_seq BIGINT NOT NULL DEFAULT 0 CHECK (message_seq >= 0),
    event_seq BIGINT NOT NULL DEFAULT 0 CHECK (event_seq >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (account_id, id),
    UNIQUE (account_id, system_key),
    UNIQUE (account_id, direct_low_user_id, direct_high_user_id),
    FOREIGN KEY (account_id, owner_user_id) REFERENCES users(account_id, id),
    FOREIGN KEY (account_id, direct_low_user_id) REFERENCES users(account_id, id),
    FOREIGN KEY (account_id, direct_high_user_id) REFERENCES users(account_id, id),
    CHECK ((kind = 'direct' AND owner_user_id IS NULL AND system_key IS NULL
            AND direct_low_user_id IS NOT NULL AND direct_high_user_id IS NOT NULL
            AND direct_low_user_id < direct_high_user_id AND posting_mode = 'everyone'
            AND new_member_history = 'all' AND NOT archived)
        OR (kind <> 'direct' AND owner_user_id IS NOT NULL
            AND direct_low_user_id IS NULL AND direct_high_user_id IS NULL
            AND char_length(btrim(title)) > 0)),
    CHECK (system_key IS NULL OR (kind = 'account' AND NOT archived))
);
CREATE INDEX IF NOT EXISTS chat_conversations_account ON chat_conversations(account_id, created_at, id);

CREATE TABLE IF NOT EXISTS chat_conversation_roles (
    account_id BIGINT NOT NULL,
    conversation_id TEXT NOT NULL,
    role_key TEXT NOT NULL CHECK (role_key IN
        ('owner','admin','fleet','safety','dispatcher','hr','accounting','recruiter','driver')),
    PRIMARY KEY (account_id, conversation_id, role_key),
    FOREIGN KEY (account_id, conversation_id) REFERENCES chat_conversations(account_id, id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS chat_group_admins (
    account_id BIGINT NOT NULL,
    conversation_id TEXT NOT NULL,
    user_id BIGINT NOT NULL,
    actions TEXT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (account_id, conversation_id, user_id),
    FOREIGN KEY (account_id, conversation_id) REFERENCES chat_conversations(account_id, id) ON DELETE CASCADE,
    FOREIGN KEY (account_id, user_id) REFERENCES users(account_id, id),
    CHECK (actions <@ ARRAY['manage_info','manage_settings','manage_audience',
        'pin_messages','delete_messages','archive_group']::TEXT[] AND array_position(actions, NULL) IS NULL)
);
CREATE TABLE IF NOT EXISTS chat_participants (
    account_id BIGINT NOT NULL,
    conversation_id TEXT NOT NULL,
    user_id BIGINT NOT NULL,
    PRIMARY KEY (account_id, conversation_id, user_id),
    FOREIGN KEY (account_id, conversation_id) REFERENCES chat_conversations(account_id, id) ON DELETE CASCADE,
    FOREIGN KEY (account_id, user_id) REFERENCES users(account_id, id)
);
CREATE TABLE IF NOT EXISTS chat_membership_intervals (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL,
    conversation_id TEXT NOT NULL,
    user_id BIGINT NOT NULL,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    left_at TIMESTAMPTZ,
    visible_after_message_seq BIGINT NOT NULL CHECK (visible_after_message_seq >= 0),
    FOREIGN KEY (account_id, conversation_id) REFERENCES chat_conversations(account_id, id) ON DELETE CASCADE,
    FOREIGN KEY (account_id, user_id) REFERENCES users(account_id, id),
    CHECK (left_at IS NULL OR left_at >= joined_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS chat_membership_open
    ON chat_membership_intervals(account_id, conversation_id, user_id) WHERE left_at IS NULL;
CREATE INDEX IF NOT EXISTS chat_membership_user
    ON chat_membership_intervals(account_id, user_id, conversation_id) WHERE left_at IS NULL;

CREATE TABLE IF NOT EXISTS chat_messages (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL,
    conversation_id TEXT NOT NULL,
    author_id BIGINT NOT NULL,
    client_message_id TEXT NOT NULL CHECK (char_length(client_message_id) BETWEEN 1 AND 128),
    message_seq BIGINT NOT NULL CHECK (message_seq > 0),
    body TEXT,
    reply_to_id BIGINT,
    version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    edited_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    UNIQUE (account_id, conversation_id, id),
    UNIQUE (account_id, conversation_id, message_seq),
    UNIQUE (account_id, conversation_id, author_id, client_message_id),
    FOREIGN KEY (account_id, conversation_id) REFERENCES chat_conversations(account_id, id) ON DELETE CASCADE,
    FOREIGN KEY (account_id, author_id) REFERENCES users(account_id, id),
    FOREIGN KEY (account_id, conversation_id, reply_to_id) REFERENCES chat_messages(account_id, conversation_id, id),
    CHECK ((deleted_at IS NULL AND body IS NOT NULL AND char_length(btrim(body)) > 0
            AND char_length(body) <= 4000) OR (deleted_at IS NOT NULL AND body IS NULL))
);
CREATE TABLE IF NOT EXISTS chat_member_state (
    account_id BIGINT NOT NULL,
    conversation_id TEXT NOT NULL,
    user_id BIGINT NOT NULL,
    last_read_message_seq BIGINT NOT NULL DEFAULT 0 CHECK (last_read_message_seq >= 0),
    muted BOOLEAN NOT NULL DEFAULT FALSE,
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (account_id, conversation_id, user_id),
    FOREIGN KEY (account_id, conversation_id) REFERENCES chat_conversations(account_id, id) ON DELETE CASCADE,
    FOREIGN KEY (account_id, user_id) REFERENCES users(account_id, id)
);
CREATE TABLE IF NOT EXISTS chat_mentions (
    account_id BIGINT NOT NULL,
    conversation_id TEXT NOT NULL,
    message_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    PRIMARY KEY (account_id, conversation_id, message_id, user_id),
    FOREIGN KEY (account_id, conversation_id, message_id) REFERENCES chat_messages(account_id, conversation_id, id) ON DELETE CASCADE,
    FOREIGN KEY (account_id, user_id) REFERENCES users(account_id, id)
);
CREATE TABLE IF NOT EXISTS chat_pins (
    account_id BIGINT NOT NULL,
    conversation_id TEXT NOT NULL,
    message_id BIGINT NOT NULL,
    pinned_by BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (account_id, conversation_id, message_id),
    FOREIGN KEY (account_id, conversation_id, message_id) REFERENCES chat_messages(account_id, conversation_id, id) ON DELETE CASCADE,
    FOREIGN KEY (account_id, pinned_by) REFERENCES users(account_id, id)
);
CREATE TABLE IF NOT EXISTS chat_drafts (
    account_id BIGINT NOT NULL,
    conversation_id TEXT NOT NULL,
    user_id BIGINT NOT NULL,
    body TEXT NOT NULL DEFAULT '' CHECK (char_length(body) <= 4000),
    reply_to_id BIGINT,
    version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (account_id, conversation_id, user_id),
    FOREIGN KEY (account_id, conversation_id) REFERENCES chat_conversations(account_id, id) ON DELETE CASCADE,
    FOREIGN KEY (account_id, user_id) REFERENCES users(account_id, id),
    FOREIGN KEY (account_id, conversation_id, reply_to_id) REFERENCES chat_messages(account_id, conversation_id, id)
);
-- Sync journal, NOT Activity Trail. Content is read afresh through access rules.
CREATE TABLE IF NOT EXISTS chat_events (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL,
    conversation_id TEXT NOT NULL,
    event_seq BIGINT NOT NULL CHECK (event_seq > 0),
    kind TEXT NOT NULL,
    message_id BIGINT,
    resource_version BIGINT NOT NULL CHECK (resource_version > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (account_id, conversation_id, id),
    UNIQUE (account_id, conversation_id, event_seq),
    FOREIGN KEY (account_id, conversation_id) REFERENCES chat_conversations(account_id, id) ON DELETE CASCADE,
    FOREIGN KEY (account_id, conversation_id, message_id) REFERENCES chat_messages(account_id, conversation_id, id)
);
CREATE TABLE IF NOT EXISTS chat_event_deliveries (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL,
    conversation_id TEXT NOT NULL,
    event_id BIGINT NOT NULL,
    channel TEXT NOT NULL,
    recipient_user_id BIGINT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    lease_until TIMESTAMPTZ,
    lease_token TEXT,
    delivered_at TIMESTAMPTZ,
    UNIQUE (account_id, conversation_id, event_id, channel, recipient_user_id),
    FOREIGN KEY (account_id, conversation_id, event_id) REFERENCES chat_events(account_id, conversation_id, id) ON DELETE CASCADE,
    FOREIGN KEY (account_id, recipient_user_id) REFERENCES users(account_id, id)
);
CREATE INDEX IF NOT EXISTS chat_delivery_pending ON chat_event_deliveries(available_at, id)
    WHERE delivered_at IS NULL;

-- All Chat writers acquire this account lock BEFORE a conversation row lock.
-- Team changes use the same lock in their transaction. This deliberately
-- serializes account writes for v1; benchmark contention before widening it.
CREATE OR REPLACE FUNCTION chat_lock_account(aid BIGINT) RETURNS VOID LANGUAGE sql AS $$
    SELECT pg_advisory_xact_lock(hashtextextended('chat/account/' || aid::TEXT, 0));
$$;
CREATE OR REPLACE FUNCTION chat_sync_membership(cid TEXT) RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE c chat_conversations%ROWTYPE; aid BIGINT;
BEGIN
    SELECT account_id INTO aid FROM chat_conversations WHERE id = cid;
    IF NOT FOUND THEN RETURN; END IF;
    PERFORM chat_lock_account(aid);
    SELECT * INTO c FROM chat_conversations WHERE id = cid AND account_id = aid FOR UPDATE;
    IF NOT FOUND THEN RETURN; END IF;
    UPDATE chat_membership_intervals m SET left_at = clock_timestamp()
    WHERE m.account_id = aid AND m.conversation_id = cid AND m.left_at IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM users u WHERE u.account_id = aid AND u.id = m.user_id AND u.is_active = 1
          AND (c.kind = 'account'
            OR (c.kind = 'roles' AND EXISTS (SELECT 1 FROM chat_conversation_roles r
                WHERE r.account_id = aid AND r.conversation_id = cid AND r.role_key = u.role))
            OR (c.kind = 'direct' AND u.id IN (c.direct_low_user_id, c.direct_high_user_id)))
      );
    INSERT INTO chat_membership_intervals(account_id, conversation_id, user_id, visible_after_message_seq)
    SELECT aid, cid, u.id, CASE WHEN c.new_member_history = 'all' THEN 0 ELSE c.message_seq END
    FROM users u WHERE u.account_id = aid AND u.is_active = 1
      AND (c.kind = 'account'
        OR (c.kind = 'roles' AND EXISTS (SELECT 1 FROM chat_conversation_roles r
            WHERE r.account_id = aid AND r.conversation_id = cid AND r.role_key = u.role))
        OR (c.kind = 'direct' AND u.id IN (c.direct_low_user_id, c.direct_high_user_id)))
      AND NOT EXISTS (SELECT 1 FROM chat_membership_intervals m WHERE m.account_id = aid
          AND m.conversation_id = cid AND m.user_id = u.id AND m.left_at IS NULL)
    ON CONFLICT DO NOTHING;
END;
$$;
CREATE OR REPLACE FUNCTION chat_user_membership_changed() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE c RECORD;
BEGIN
    -- account_id changes with existing chat references are rejected by the
    -- composite FKs: historical communication never moves between tenants.
    PERFORM chat_lock_account(NEW.account_id);
    FOR c IN SELECT id FROM chat_conversations WHERE account_id = NEW.account_id ORDER BY id LOOP
        PERFORM chat_sync_membership(c.id);
    END LOOP;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS chat_user_inserted ON users;
CREATE TRIGGER chat_user_inserted AFTER INSERT ON users
    FOR EACH ROW EXECUTE FUNCTION chat_user_membership_changed();
DROP TRIGGER IF EXISTS chat_user_changed ON users;
CREATE TRIGGER chat_user_changed AFTER UPDATE OF role, is_active, account_id ON users
    FOR EACH ROW WHEN (OLD.role IS DISTINCT FROM NEW.role OR OLD.is_active IS DISTINCT FROM NEW.is_active
        OR OLD.account_id IS DISTINCT FROM NEW.account_id)
    EXECUTE FUNCTION chat_user_membership_changed();
CREATE OR REPLACE FUNCTION chat_fixed_identity() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.account_id IS DISTINCT FROM OLD.account_id
       OR NEW.kind IS DISTINCT FROM OLD.kind OR NEW.system_key IS DISTINCT FROM OLD.system_key
       OR NEW.direct_low_user_id IS DISTINCT FROM OLD.direct_low_user_id
       OR NEW.direct_high_user_id IS DISTINCT FROM OLD.direct_high_user_id THEN
        RAISE EXCEPTION 'Chat conversation identity is immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS chat_conversation_identity ON chat_conversations;
CREATE TRIGGER chat_conversation_identity BEFORE UPDATE ON chat_conversations
    FOR EACH ROW EXECUTE FUNCTION chat_fixed_identity();
CREATE OR REPLACE FUNCTION chat_participant_pair() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM chat_conversations c WHERE c.account_id = NEW.account_id
        AND c.id = NEW.conversation_id AND c.kind = 'direct'
        AND NEW.user_id IN (c.direct_low_user_id, c.direct_high_user_id)) THEN
        RAISE EXCEPTION 'Participant must belong to the canonical direct pair' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS chat_participant_guard ON chat_participants;
CREATE TRIGGER chat_participant_guard BEFORE INSERT OR UPDATE ON chat_participants
    FOR EACH ROW EXECUTE FUNCTION chat_participant_pair();
CREATE OR REPLACE FUNCTION chat_audience_changed() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN PERFORM chat_sync_membership(OLD.conversation_id); RETURN OLD; END IF;
    IF TG_OP = 'UPDATE' AND OLD.conversation_id IS DISTINCT FROM NEW.conversation_id THEN
        PERFORM chat_sync_membership(OLD.conversation_id);
    END IF;
    PERFORM chat_sync_membership(NEW.conversation_id);
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS chat_roles_changed ON chat_conversation_roles;
CREATE TRIGGER chat_roles_changed AFTER INSERT OR UPDATE OR DELETE ON chat_conversation_roles
    FOR EACH ROW EXECUTE FUNCTION chat_audience_changed();
CREATE OR REPLACE FUNCTION chat_conversation_created() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.kind = 'direct' THEN
        INSERT INTO chat_participants(account_id, conversation_id, user_id)
        VALUES (NEW.account_id, NEW.id, NEW.direct_low_user_id), (NEW.account_id, NEW.id, NEW.direct_high_user_id);
    END IF;
    PERFORM chat_sync_membership(NEW.id);
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS chat_conversation_inserted ON chat_conversations;
CREATE TRIGGER chat_conversation_inserted AFTER INSERT ON chat_conversations
    FOR EACH ROW EXECUTE FUNCTION chat_conversation_created();
"""


async def migrate_chat(conn) -> None:
    """One atomic, repeatable installation. Permission grants are untouched."""
    async with conn._acquire() as pg:
        async with pg.transaction():
            await pg.execute("SELECT pg_advisory_xact_lock(hashtextextended('chat/schema', 0))")
            await pg.execute(SCHEMA_SQL)

MESSAGE_API_SQL = r"""
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS request_fingerprint TEXT;
ALTER TABLE chat_member_state ADD COLUMN IF NOT EXISTS last_seen_message_seq BIGINT NOT NULL DEFAULT 0
    CHECK (last_seen_message_seq >= 0);
CREATE INDEX IF NOT EXISTS chat_messages_visible ON chat_messages(account_id, conversation_id, message_seq DESC)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS chat_mentions_user ON chat_mentions(account_id, user_id, conversation_id, message_id);
"""


async def migrate_chat_message_api(conn) -> None:
    """Legacy fingerprints stay NULL: unknown original payloads never match a retry."""
    async with conn._acquire() as pg:
        async with pg.transaction():
            await pg.execute("SELECT pg_advisory_xact_lock(hashtextextended('chat/schema', 0))")
            await pg.execute(MESSAGE_API_SQL)


REALTIME_SQL = r"""
ALTER TABLE chat_events ADD COLUMN IF NOT EXISTS recipient_user_id BIGINT;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chat_events_recipient_fk') THEN
        ALTER TABLE chat_events ADD CONSTRAINT chat_events_recipient_fk
            FOREIGN KEY (account_id, recipient_user_id) REFERENCES users(account_id, id);
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS chat_sync_state (
    account_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1,
    PRIMARY KEY (account_id, user_id),
    FOREIGN KEY (account_id, user_id) REFERENCES users(account_id, id) ON DELETE CASCADE
);
CREATE OR REPLACE FUNCTION chat_membership_signal() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO chat_sync_state(account_id, user_id) VALUES (NEW.account_id, NEW.user_id)
        ON CONFLICT (account_id, user_id) DO UPDATE SET revision = chat_sync_state.revision + 1;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS chat_membership_signal ON chat_membership_intervals;
CREATE TRIGGER chat_membership_signal AFTER INSERT OR UPDATE ON chat_membership_intervals
    FOR EACH ROW EXECUTE FUNCTION chat_membership_signal();

-- All event writers share one candidate fanout, including metadata and private
-- device sync. These are hints; session/grant/resource authorization is at read.
CREATE OR REPLACE FUNCTION chat_event_fanout() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO chat_event_deliveries(account_id, conversation_id, event_id, channel, recipient_user_id)
    SELECT NEW.account_id, NEW.conversation_id, NEW.id, 'realtime', m.user_id
    FROM chat_membership_intervals m
    WHERE m.account_id = NEW.account_id AND m.conversation_id = NEW.conversation_id AND m.left_at IS NULL
      AND (NEW.recipient_user_id IS NULL OR NEW.recipient_user_id = m.user_id)
      AND (NEW.message_id IS NULL OR EXISTS (SELECT 1 FROM chat_messages msg
          WHERE msg.account_id = NEW.account_id AND msg.conversation_id = NEW.conversation_id
            AND msg.id = NEW.message_id AND msg.message_seq > m.visible_after_message_seq))
    ON CONFLICT DO NOTHING;
    -- The inbox must refresh even when this conversation is not subscribed.
    -- Private events only reach their own recipient; hidden-message candidates
    -- were filtered above. The revision carries no conversation or content.
    INSERT INTO chat_sync_state(account_id, user_id)
    SELECT account_id, recipient_user_id FROM chat_event_deliveries
    WHERE account_id = NEW.account_id AND conversation_id = NEW.conversation_id
      AND event_id = NEW.id AND channel = 'realtime'
    ON CONFLICT (account_id, user_id) DO UPDATE SET revision = chat_sync_state.revision + 1;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS chat_event_fanout ON chat_events;
CREATE TRIGGER chat_event_fanout AFTER INSERT ON chat_events
    FOR EACH ROW EXECUTE FUNCTION chat_event_fanout();

CREATE OR REPLACE FUNCTION chat_personal_signal() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE seq BIGINT;
BEGIN
    IF TG_TABLE_NAME = 'chat_member_state' THEN
        IF TG_OP = 'INSERT' AND NEW.last_read_message_seq = 0
            AND NOT NEW.muted AND NOT NEW.pinned AND NOT NEW.archived THEN RETURN NEW; END IF;
        IF TG_OP = 'UPDATE' AND NEW.last_read_message_seq = OLD.last_read_message_seq
            AND NEW.muted = OLD.muted AND NEW.pinned = OLD.pinned AND NEW.archived = OLD.archived THEN RETURN NEW; END IF;
    END IF;
    UPDATE chat_conversations SET event_seq = event_seq + 1
        WHERE account_id = NEW.account_id AND id = NEW.conversation_id RETURNING event_seq INTO seq;
    INSERT INTO chat_events(account_id, conversation_id, event_seq, kind, resource_version, recipient_user_id)
        VALUES (NEW.account_id, NEW.conversation_id, seq,
            CASE WHEN TG_TABLE_NAME = 'chat_drafts' THEN 'draft_changed' ELSE 'state_changed' END, seq, NEW.user_id);
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS chat_state_signal ON chat_member_state;
CREATE TRIGGER chat_state_signal AFTER INSERT OR UPDATE ON chat_member_state
    FOR EACH ROW EXECUTE FUNCTION chat_personal_signal();
DROP TRIGGER IF EXISTS chat_draft_signal ON chat_drafts;
CREATE TRIGGER chat_draft_signal AFTER INSERT OR UPDATE ON chat_drafts
    FOR EACH ROW EXECUTE FUNCTION chat_personal_signal();
"""


async def migrate_chat_realtime(conn) -> None:
    async with conn._acquire() as pg:
        async with pg.transaction():
            await pg.execute("SELECT pg_advisory_xact_lock(hashtextextended('chat/schema', 0))")
            await pg.execute(REALTIME_SQL)


CHAT_TENANT_TABLES = (
    "chat_conversations", "chat_conversation_roles", "chat_group_admins",
    "chat_participants", "chat_membership_intervals", "chat_messages",
    "chat_member_state", "chat_mentions", "chat_pins", "chat_drafts",
    "chat_events", "chat_sync_state",
)

#: The one Chat table deliberately OUTSIDE the policy.  The outbox drain
#: claims pending deliveries across every account in one statement
#: (``chat_claim_deliveries``), from a runtime task that belongs to no
#: tenant — under a fail-closed policy it would find nothing, ever, and
#: realtime would go quiet the day ENABLE_RLS is set.  The row holds ids
#: only (conversation, event, recipient), never content; the same reason
#: ``notification_digest_queue`` and ``object_storage_sync_queue`` stay
#: out of migration 057's list.
CHAT_CROSS_TENANT_TABLES = ("chat_event_deliveries",)


async def migrate_chat_rls(conn) -> None:
    """The tenant_isolation policy on every Chat table a tenant path reads.

    Private conversations are the most sensitive rows this schema holds,
    and every other tenant table in the platform carries the policy
    (migrations 057 and 103 are the shape).  The delivery queue is the
    one exception — see ``CHAT_CROSS_TENANT_TABLES``.  The composite foreign keys
    already stop a row naming a user from another account; this is the
    second wall, the one that holds when a query forgets its WHERE.

    Same gating and shape as its siblings: skipped unless ``ENABLE_RLS``
    is set, fail-closed on the ``app.account_id`` GUC, FORCE so the
    table owner is subject to it too.  Idempotent.
    """
    import logging
    import os
    log = logging.getLogger(__name__)
    if os.getenv("ENABLE_RLS", "0").strip() not in ("1", "true", "TRUE", "yes"):
        log.info("Migration 215: ENABLE_RLS not set; Chat RLS policies skipped")
        return
    applied = 0
    for tbl in CHAT_TENANT_TABLES:
        try:
            await conn.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
            await conn.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
            await conn.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl}")
            await conn.execute(
                f"""
                CREATE POLICY tenant_isolation ON {tbl}
                USING       (account_id::text = current_setting('app.account_id', true))
                WITH CHECK  (account_id::text = current_setting('app.account_id', true))
                """
            )
            applied += 1
        except Exception as e:  # noqa: BLE001 — one table must not stop the rest
            log.error("Migration 215: RLS on %s failed — %s", tbl, e)
    log.info("Migration 215: tenant_isolation applied to %d/%d Chat tables", applied, len(CHAT_TENANT_TABLES))
