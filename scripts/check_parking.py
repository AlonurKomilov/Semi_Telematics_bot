"""Quick diagnostic: show active parking events."""
import sqlite3, os

for f in sorted(os.listdir("data")):
    if f.startswith("tenant_") and f.endswith(".db"):
        path = f"data/{f}"
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT COUNT(*) as c, resolved FROM parking_events GROUP BY resolved"
        ).fetchall()
        for r in rows:
            print(f"{f}: resolved={r['resolved']}, count={r['c']}")
        active = conn.execute(
            "SELECT vehicle_name, location_class, alert_level, duration_hours, "
            "address, ai_analysis FROM parking_events WHERE resolved = 0 "
            "ORDER BY vehicle_name"
        ).fetchall()
        print(f"{f}: {len(active)} active parking events:")
        for a in active:
            ai = "yes" if a["ai_analysis"] else "no"
            print(
                f"  {a['vehicle_name']:10} loc={a['location_class']:8} "
                f"alert={a['alert_level']:10} dur={a['duration_hours']:6.1f}h "
                f"ai={ai}  addr={a['address'][:60]}"
            )
        conn.close()
