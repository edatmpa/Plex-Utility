#!/usr/bin/env python3
r"""
One-shot Plex CSV report — movies & TV episodes.
Writes: Title, Year, Type, Resolution, Folder (2 levels up), File Path

Reads the live Plex database on this PC (read-only).
Output: reports\plex_report_<date>.csv in the project folder.

Usage:
  export_report.bat
  python export_report.py [path_to_db]      (default: the live database)
"""

import csv
import os
import sys
from datetime import date

import plex_config as cfg

# ─── Resolution classifier ────────────────────────────────────────────────────

def classify(height) -> str:
    if height is None:
        return "Unknown"
    if height >= 2160:
        return "4K"
    if height >= 1080:
        return "1080p"
    if height >= 720:
        return "720p"
    if height >= 480:
        return "480p"
    return "SD"

def fmt_gb(size_bytes) -> str:
    """Bytes → GB with 2 decimal places."""
    if size_bytes is None:
        return ""
    return f"{size_bytes / (1024**3):.2f}"

# ─── Folder helper ────────────────────────────────────────────────────────────
#   "2 levels up" — for movies: Action/MovieName/file.mkv → Action
#                     for TV:     ShowName/Season/file.mkv  → ShowName

def folder_2up(path: str) -> str:
    parts = path.replace("\\", "/").rstrip("/").split("/")
    return parts[-3] if len(parts) >= 3 else ""

# ─── Query ────────────────────────────────────────────────────────────────────

QUERY = """
    SELECT mi.title, mi.year, mi.metadata_type, mp.file, mi2.height, mp.size
    FROM metadata_items mi
    JOIN media_items mi2 ON mi.id = mi2.metadata_item_id
    JOIN media_parts mp ON mi2.id = mp.media_item_id
    WHERE mi.metadata_type IN (1, 4)   -- 1=movie, 4=episode
      AND mp.file != ''
    ORDER BY mp.file
"""

TYPE_NAMES = {1: "Movie", 4: "Episode"}

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else str(cfg.LIBRARY_DB)
    if not os.path.exists(db_path):
        print(f"ERROR: Database not found: {db_path}")
        sys.exit(1)

    conn = cfg.connect_ro(db_path)
    rows = conn.execute(QUERY).fetchall()
    conn.close()

    if not rows:
        print("No movies or episodes found.")
        return

    out_dir = cfg.PROJECT_ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"plex_report_{date.today().isoformat()}.csv"

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["Title", "Year", "Type", "Resolution", "Size (GB)", "Folder", "File Path"])
        for r in rows:
            writer.writerow([
                r["title"],
                r["year"] or "",
                TYPE_NAMES.get(r["metadata_type"], str(r["metadata_type"])),
                classify(r["height"]),
                fmt_gb(r["size"]),
                folder_2up(r["file"]),
                r["file"],
            ])

    print(f"Done — {len(rows)} rows → {out_path}")

if __name__ == "__main__":
    main()
