# Backups — nightly encrypted off-site (Cloudflare R2)

`scripts/backup_db.sh`, system cron 03:30 UTC (after the 02:00
retention pass). NOT a bot job on purpose: a backup that dies with
the app is not a backup. Failure pings the first SYSTEM_OWNER_IDS
via the system bot; total-box-death detection is the uptime
monitor's job (docs/runbooks/uptime-monitoring.md).

## What & where

| what | bucket path | kept |
|---|---|---|
| `pg_dump -Fc`, GPG-AES256 | `Database/daily/4truck-<date>.pgdump.gpg` | 14 d |
| Sunday copy | `Database/weekly/` | 60 d |
| 1st-of-month copy | `Database/monthly/` | 190 d |
| `.env` (encrypted) | `Database/daily/env-<date>.gpg` | 14 d |
| media mirror (`data/userdata`) | `Media/` | mirror |

Local copies: `/home/abcdev/backups/db/`, 7 days. Secrets
(`R2_*`, `BACKUP_PASSPHRASE`) live in the project `.env`.
**The passphrase must ALSO exist off-server** (password manager) —
without it every backup is unopenable.

## Restore (drill this monthly)

```bash
cd ~/projects/Semi_Telematics_bot && set -a && . ./.env && set +a
export RCLONE_CONFIG_R2_TYPE=s3 RCLONE_CONFIG_R2_PROVIDER=Cloudflare \
       RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT" \
       RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
       RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"

rclone ls R2:4truck-backups/Database/daily          # pick a date
rclone copyto R2:4truck-backups/Database/daily/4truck-<date>.pgdump.gpg /tmp/r.gpg
gpg --batch --passphrase "$BACKUP_PASSPHRASE" -o /tmp/r.pgdump -d /tmp/r.gpg

# Monthly DRILL — restore into a scratch DB and count something:
createdb restore_test
pg_restore -d restore_test /tmp/r.pgdump
psql restore_test -c "SELECT COUNT(*) FROM warehouse.vehicle_telemetry;"
dropdb restore_test

# REAL disaster restore: new box → install postgres+redis → restore
# the dump into the app DB → decrypt env-<date>.gpg to .env → deploy
# the repo → rclone sync R2:4truck-backups/Media data/userdata → start.
```

An untested backup is a hope. The drill takes five minutes.
