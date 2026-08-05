#!/usr/bin/env bash
# Nightly off-site backup — encrypted pg_dump + .env to Cloudflare R2,
# plus a media mirror.  Runs from SYSTEM CRON, deliberately not from
# the bot: a backup that dies with the app is not a backup (the
# 2026-08-04 outage took every in-app job down with it).
#
#   30 3 * * *  /home/abcdev/projects/Semi_Telematics_bot/scripts/backup_db.sh
#
# Layout in the bucket (prefix Database/ = the folder the owner made):
#   Database/daily/    4truck-YYYY-MM-DD.pgdump.gpg + env-*.gpg  (kept 14d)
#   Database/weekly/   Sunday copies                              (kept 60d)
#   Database/monthly/  1st-of-month copies                        (kept 190d)
#   Media/             mirror of data/userdata
#
# Restore: see docs/runbooks/backups.md.  Everything is GPG-encrypted
# with BACKUP_PASSPHRASE before leaving the box — keep a copy of that
# passphrase OFF this server; without it the backups are unopenable.
set -euo pipefail

REPO=/home/abcdev/projects/Semi_Telematics_bot
LOCAL=/home/abcdev/backups/db
cd "$REPO"
set -a; . ./.env; set +a
mkdir -p "$LOCAL"

notify_fail() {
    # Best-effort Telegram to the first system owner via the system
    # bot's API identity — works as long as THIS box is alive enough
    # to run cron at all; total-box-death is the uptime monitor's job.
    local owner="${SYSTEM_OWNER_IDS%%,*}"
    [ -n "${TELEGRAM_SYSTEM_BOT_TOKEN:-}" ] && [ -n "$owner" ] && \
    curl -sS -m 10 "https://api.telegram.org/bot${TELEGRAM_SYSTEM_BOT_TOKEN}/sendMessage" \
        -d chat_id="$owner" \
        -d text="🔴 Nightly backup FAILED on $(hostname) at $(date -u +%FT%TZ) — check /home/abcdev/backups/backup.log" \
        >/dev/null || true
}
trap notify_fail ERR

# rclone remote from env vars — no config file, secrets stay in .env.
export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT"
export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
export RCLONE_CONFIG_R2_REGION=auto
# R2 object-scoped tokens may not HeadBucket/CreateBucket; rclone's
# default pre-flight bucket check then 403s every upload.
export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
DEST="R2:4truck-backups"

STAMP=$(date -u +%F)
DOW=$(date -u +%u)    # 1=Mon .. 7=Sun
DOM=$(date -u +%d)

DUMP="$LOCAL/4truck-$STAMP.pgdump"
pg_dump "$DATABASE_URL" -Fc -f "$DUMP"
gpg --batch --yes --symmetric --cipher-algo AES256 \
    --passphrase "$BACKUP_PASSPHRASE" -o "$DUMP.gpg" "$DUMP"
rm -f "$DUMP"
gpg --batch --yes --symmetric --cipher-algo AES256 \
    --passphrase "$BACKUP_PASSPHRASE" -o "$LOCAL/env-$STAMP.gpg" .env

rclone copyto "$DUMP.gpg"           "$DEST/Database/daily/4truck-$STAMP.pgdump.gpg"
rclone copyto "$LOCAL/env-$STAMP.gpg" "$DEST/Database/daily/env-$STAMP.gpg"
[ "$DOW" = "7" ]  && rclone copyto "$DUMP.gpg" "$DEST/Database/weekly/4truck-$STAMP.pgdump.gpg"
[ "$DOM" = "01" ] && rclone copyto "$DUMP.gpg" "$DEST/Database/monthly/4truck-$STAMP.pgdump.gpg"

rclone sync "$REPO/data/userdata" "$DEST/Media" --transfers 4 --quiet

# Rotation — local 7 days; remote per-tier windows.
find "$LOCAL" -name "*.gpg" -mtime +7 -delete
rclone delete "$DEST/Database/daily"   --min-age 14d
rclone delete "$DEST/Database/weekly"  --min-age 60d
rclone delete "$DEST/Database/monthly" --min-age 190d

echo "backup ok $STAMP ($(du -h "$DUMP.gpg" | cut -f1) dump)"
