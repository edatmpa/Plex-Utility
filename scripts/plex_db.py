#!/usr/bin/env python3
"""
Plex Database Management Tool  (WRITE OPERATIONS)

Performs destructive changes to the Plex SQLite database.
PLEX MEDIA SERVER MUST BE STOPPED (on this PC) BEFORE RUNNING THIS SCRIPT.

Commands:
  plex.bat delete-part <Part ID>   Delete a stale/orphaned media part
  plex.bat cleanup-backups         Remove old .backup.* files (keeps 3 most recent)

A timestamped backup is created automatically before any delete operation.
Use plex_search.py (via plex.bat orphaned) to find Part IDs to delete.
"""

import sqlite3
import argparse
import os
import shutil
import sys
from datetime import datetime
from typing import Optional

import plex_config as cfg
from menu_common import explain

# Ensure UTF-8 I/O on Windows
if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ─── Database Connection (writable) ──────────────────────────────────────────

def connect_to_db(db_path: str) -> Optional[sqlite3.Connection]:
    """Open a standard writable connection. No immutable flag — writes are allowed."""
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        # Register dummy ICU collation to avoid schema parse errors
        for name in ('ICU', 'icu', 'UNICODE', 'unicode'):
            try:
                conn.create_collation(name, lambda a, b: (a > b) - (a < b))
            except Exception:
                pass
        return conn
    except sqlite3.Error as e:
        print(f"  [ERROR] Database connection failed: {e}")
        return None


# ─── Backup / Cleanup ─────────────────────────────────────────────────────────

def create_backup(db_path: str) -> Optional[str]:
    """Copy database to a timestamped .backup.YYYYMMDD_HHMMSS file before any write."""
    try:
        db_dir  = os.path.dirname(db_path)
        db_name = os.path.basename(db_path)
        ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup  = os.path.join(db_dir, f"{db_name}.backup.{ts}")
        print(f"  Creating backup: {os.path.basename(backup)}...")
        shutil.copy2(db_path, backup)
        print(f"  Backup saved:    {backup}")
        return backup
    except Exception as e:
        print(f"  [ERROR] Backup failed: {e}")
        return None


def cmd_cleanup_backups(db_dir: str, keep_count: int = 3) -> None:
    """Remove old .backup.* files from db_dir, keeping the most recent keep_count."""
    try:
        files = sorted(
            [f for f in os.listdir(db_dir) if ".backup." in f],
            key=lambda x: os.path.getmtime(os.path.join(db_dir, x)),
            reverse=True,
        )
        to_remove = files[keep_count:]
        for f in to_remove:
            os.remove(os.path.join(db_dir, f))
            print(f"  Removed: {f}")
        if to_remove:
            print(f"  Removed {len(to_remove)} old backup(s). Kept {keep_count}.")
        else:
            print(f"  {min(len(files), keep_count)} backup(s) present. Nothing to remove.")
    except Exception as e:
        print(f"  [ERROR] Cleanup failed: {e}")


# ─── Delete Command ───────────────────────────────────────────────────────────

def fmt_size(size_bytes: Optional[int]) -> str:
    if size_bytes is None:
        return "N/A"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:,.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:,.1f} PB"


def cmd_delete_part(db_path: str, part_id: int) -> None:
    """
    Delete a stale media part and all its dependents.

    Deletion order (child tables first):
      1. media_streams          (media_part_id = part_id)
      2. media_part_settings    (media_part_id = part_id)
      3. download_queue_items   (media_part_id = part_id)
      4. media_parts            (id = part_id)
      5. media_items            (id = media_item_id) — only if no other parts remain
    """
    print("\n" + "=" * 60)
    print("  DELETE STALE MEDIA PART")
    print("=" * 60)
    if cfg.plex_running():
        print("  [ABORT] Plex Media Server is running. Stop it first (tray icon -> Exit),")
        print("          then run this again. No changes made.")
        return
    print()

    # ── Backup first, before opening a writable connection ──────────────────
    backup_path = create_backup(db_path)
    if not backup_path:
        print("  [ABORT] Backup failed — no changes made.")
        return

    conn = connect_to_db(db_path)
    if not conn:
        return

    try:
        cur = conn.cursor()

        # Fetch part details
        part = cur.execute(
            "SELECT id, file, size, media_item_id FROM media_parts WHERE id = ?",
            (part_id,)
        ).fetchone()

        if not part:
            print(f"  Part ID {part_id} not found in database.")
            print(f"  Use: plex.bat orphaned  to list deletable part IDs.")
            return

        print(f"\n  Part ID         : {part_id}")
        print(f"  File            : {part['file']}")
        print(f"  Size            : {fmt_size(part['size'])}")

        if os.path.exists(part['file']):
            print(f"\n  WARNING: File still exists on disk!")
            if input("  Continue deletion anyway? (y/n): ").strip().lower() != 'y':
                print("  Cancelled.")
                return
        else:
            print(f"  File confirmed absent from disk.")

        media_item_id = part['media_item_id']
        part_count     = cur.execute(
            "SELECT COUNT(*) FROM media_parts WHERE media_item_id = ?", (media_item_id,)
        ).fetchone()[0]
        stream_count   = cur.execute(
            "SELECT COUNT(*) FROM media_streams WHERE media_part_id = ?", (part_id,)
        ).fetchone()[0]
        settings_count = cur.execute(
            "SELECT COUNT(*) FROM media_part_settings WHERE media_part_id = ?", (part_id,)
        ).fetchone()[0]
        dq_count       = cur.execute(
            "SELECT COUNT(*) FROM download_queue_items WHERE media_part_id = ?", (part_id,)
        ).fetchone()[0]

        print(f"\n  Rows to delete:")
        print(f"    media_streams             : {stream_count}")
        print(f"    media_part_settings       : {settings_count}")
        print(f"    download_queue_items      : {dq_count}")
        print(f"    media_parts               : 1")
        if part_count == 1:
            print(f"    media_items               : 1  (no other parts remain)")

        print(f"\n  Backup at: {backup_path}")
        print(f"\n  Type YES to confirm permanent deletion: ", end="", flush=True)

        if input().strip().upper() != "YES":
            print("  Cancelled. No changes made.")
            return

        # ── Execute deletes in child-first order ─────────────────────────────
        cur.execute("DELETE FROM media_streams WHERE media_part_id = ?",          (part_id,))
        cur.execute("DELETE FROM media_part_settings WHERE media_part_id = ?",    (part_id,))
        cur.execute("DELETE FROM download_queue_items WHERE media_part_id = ?",   (part_id,))
        cur.execute("DELETE FROM media_parts WHERE id = ?",                       (part_id,))
        if part_count == 1:
            cur.execute("DELETE FROM media_items WHERE id = ?",                   (media_item_id,))

        conn.commit()

        print(f"\n  Part ID {part_id} deleted successfully.")
        print(f"  Backup at: {backup_path}")
        print(f"\n  Next steps:")
        print(f"    1. Start Plex Media Server again")
        print(f"    2. Run a library scan in the Plex UI to verify")
        print(f"    3. Once verified: plex.bat cleanup-backups")

    except Exception as e:
        conn.rollback()
        print(f"\n  [ERROR] Deletion failed — rolled back: {e}")
        print(f"  Database is unchanged. Backup preserved at: {backup_path}")
    finally:
        conn.close()


# ─── Interactive Menu ─────────────────────────────────────────────────────────

def interactive_menu(db_path: str) -> None:
    """Sub-menu for database management operations."""
    while True:
        print("\n" + "=" * 60)
        print("  DATABASE MANAGEMENT  (write operations)")
        print("=" * 60)
        print("  [1] Delete stale media part    (by Part ID)")
        print("  [2] Clean up old backups       (keep 3 most recent)")
        print("  [q] Quit")
        print("=" * 60)

        choice = input("\n  Choice: ").strip().lower()

        if choice == 'q':
            print("  Goodbye.")
            break
        elif choice == '1':
            explain("Delete Stale Media Part", [
                "Creates a timestamped backup of the database first.",
                "Verifies the Part ID exists and shows its file path and size.",
                "Shows a dry-run of every row that would be affected.",
                "Deletes child-first (streams, part settings, queue items, the part,",
                "and the parent media item only if it has no other parts left).",
                "Requires typed YES to confirm before anything is deleted.",
            ], warning="Stop Plex Media Server first — this writes to the live database.")
            raw = input("  Part ID to delete: ").strip()
            if not raw.isdigit():
                print("  Invalid Part ID — must be a number.")
                continue
            cmd_delete_part(db_path, int(raw))
        elif choice == '2':
            explain("Clean Up Old Backups", [
                "Removes old .backup.* files from the database folder.",
                "Keeps the 3 most recent backups; does not touch the live database.",
            ])
            cmd_cleanup_backups(os.path.dirname(db_path))
        else:
            print("  Invalid choice.")
            continue

        input("\n  Press Enter to continue...")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plex Database Management Tool (write operations — stop Plex first!)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  plex.bat delete-part 310534
  plex.bat cleanup-backups
        """,
    )
    parser.add_argument("--db", default=str(cfg.LIBRARY_DB),
                        help="Plex library database (default: the live one on this PC)")
    parser.add_argument("--interactive", action="store_true",
                        help="Launch interactive management menu")

    sub = parser.add_subparsers(dest="command", help="Available commands")

    p = sub.add_parser("delete-part",
                       help="Delete a stale/orphaned media part by Part ID (creates backup first)")
    p.add_argument("id", type=int, help="Part ID from media_parts table")

    sub.add_parser("cleanup-backups",
                   help="Remove old .backup.* files from the DB directory (keeps 3 most recent)")

    return parser.parse_args()


def main():
    args = parse_args()

    if not os.path.exists(args.db):
        print(f"[ERROR] Database not found: {args.db}")
        sys.exit(1)

    if args.interactive or args.command is None:
        interactive_menu(args.db)
    elif args.command == "delete-part":
        cmd_delete_part(args.db, args.id)
    elif args.command == "cleanup-backups":
        cmd_cleanup_backups(os.path.dirname(args.db))
    else:
        print("No command specified. Use --help for usage.")
        sys.exit(1)


if __name__ == "__main__":
    main()
