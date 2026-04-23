"""One-time script: resolve alert_acknowledgment rows stuck 'active'
when their alert_history is already 'cleared'.

Run with: python3 scripts/fix_orphaned_acks.py
"""
import sqlite3
import sys

DB_PATH = "data/bot.db"
conn = sqlite3.connect(DB_PATH, timeout=30)

before = conn.execute(
    "SELECT COUNT(*) FROM alert_acknowledgments WHERE status='active'"
).fetchone()[0]

cur = conn.execute(
    """
    UPDATE alert_acknowledgments
    SET status        = 'acknowledged',
        acknowledged_by = 0,
        acknowledged_at = datetime('now')
    WHERE status = 'active'
      AND id IN (
          SELECT a.id
          FROM alert_acknowledgments a
          JOIN alert_history h
               ON  h.account_id = a.account_id
               AND h.alert_type = a.alert_type
               AND h.vehicle_id = a.vehicle_id
          WHERE a.status  = 'active'
            AND h.status  = 'cleared'
      )
    """
)
conn.commit()
fixed = cur.rowcount

after = conn.execute(
    "SELECT COUNT(*) FROM alert_acknowledgments WHERE status='active'"
).fetchone()[0]

print(f"Before : {before} active acks")
print(f"Fixed  : {fixed} orphaned rows → 'acknowledged'")
print(f"After  : {after} active acks remaining (truly pending)")

# Breakdown of remaining truly-pending acks
rows = conn.execute(
    """
    SELECT a.alert_type,
           COUNT(DISTINCT a.vehicle_id) AS vehicles,
           COUNT(*)                     AS acks
    FROM alert_acknowledgments a
    JOIN alert_history h
         ON  h.account_id = a.account_id
         AND h.alert_type = a.alert_type
         AND h.vehicle_id = a.vehicle_id
    WHERE a.status = 'active'
      AND h.status = 'active'
    GROUP BY a.alert_type
    ORDER BY vehicles DESC
    """
).fetchall()

if rows:
    print("\nTruly pending by type (vehicle count / ack rows):")
    for r in rows:
        print(f"  {r[0]}: {r[1]} vehicles, {r[2]} ack rows")
else:
    print("\nNo truly pending alerts remain.")

conn.close()
