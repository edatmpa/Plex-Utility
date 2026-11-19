"""Quick schema dump for the live Plex DB (read-only)."""
import sys

import plex_config as cfg

conn = cfg.connect_ro(sys.argv[1] if len(sys.argv) > 1 else None)
print("=== TABLES ===")
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
for t in tables:
    print(f"  {t[0]}")

print("\n=== media_streams COLUMNS ===")
cols = conn.execute("PRAGMA table_info(media_streams)").fetchall()
for c in cols:
    print(f"  {c[1]:35s} {c[2]}")

print("\n=== media_parts COLUMNS ===")
cols = conn.execute("PRAGMA table_info(media_parts)").fetchall()
for c in cols:
    print(f"  {c[1]:35s} {c[2]}")

print("\n=== media_items COLUMNS ===")
cols = conn.execute("PRAGMA table_info(media_items)").fetchall()
for c in cols:
    print(f"  {c[1]:35s} {c[2]}")

print("\n=== metadata_items COLUMNS ===")
cols = conn.execute("PRAGMA table_info(metadata_items)").fetchall()
for c in cols:
    print(f"  {c[1]:35s} {c[2]}")

conn.close()
