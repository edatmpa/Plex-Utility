#!/usr/bin/env python3
"""
Plex Database Search & Reporting Tool  (READ-ONLY)

Plex Media Server runs on this PC; the live database is opened read-only in
place (location in plex_config.py). Media files are on the NAS.

Run interactively via plex.bat, or with a subcommand:
  plex.bat search "The Matrix"
  plex.bat find-file "V:\\Japanese\\Samurai\\47 Ronin"
  plex.bat orphaned
  plex.bat stats

To modify the database (delete parts, cleanup backups) use plex_db.py via:
  plex.bat delete-part 310534
  plex.bat cleanup-backups
"""

import sqlite3
import argparse
import os
import sys
import textwrap
from datetime import datetime, timezone
from typing import Optional

import plex_config as cfg
from menu_common import explain

# Ensure UTF-8 I/O on Windows so Cyrillic/CJK input matches DB encoding
if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def normalize_path_query(q: str) -> str:
    """Turn a typed path into a fragment that matches however Plex stored the file.
    Plex keeps the form each library was added with (V:\\..., \\\\192.168.1.11\\...,
    \\\\?\\V:\\...), so a full path is reduced to the part after the NAS share."""
    return cfg.share_relative(q)


# ─── Constants ────────────────────────────────────────────────────────────────

METADATA_TYPES = {
    1: "movie", 2: "show", 3: "season", 4: "episode",
    8: "artist", 9: "album", 10: "track",
    12: "photo", 13: "photo_album", 14: "collection",
}

STREAM_TYPES = {0: "video", 1: "audio", 2: "subtitle", 3: "embedded", 4: "lyrics"}

TAG_TYPES = {
    1: "genre", 2: "collection", 3: "director", 4: "writer", 5: "producer",
    6: "actor", 7: "role", 8: "similar", 9: "country", 10: "label",
    11: "content_rating", 12: "mood", 13: "style", 14: "theme",
    15: "franchise", 16: "decade", 17: "resolution", 18: "codec",
    19: "source", 20: "network", 21: "studio",
}


# ─── Database Connection (read-only) ─────────────────────────────────────────

def list_db_files(share_path: str) -> None:
    try:
        files = [f for f in os.listdir(share_path) if f.endswith(".db")]
        if files:
            print(f"  .db files found in {share_path}:")
            for f in sorted(files):
                full = os.path.join(share_path, f)
                print(f"    {f}  ({os.path.getsize(full):,} bytes)")
        else:
            print(f"  No .db files found in {share_path}")
    except Exception as e:
        print(f"  Could not list {share_path}: {e}")


def inspect_schema(conn) -> list:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def verify_schema(conn) -> bool:
    tables = set(inspect_schema(conn))
    required = {"metadata_items", "media_items", "media_parts", "library_sections"}
    missing = required - tables
    if not missing:
        return True
    print(f"\n  [ERROR] This does not look like the Plex library database.")
    print(f"  Missing tables: {', '.join(sorted(missing))}")
    if tables:
        print(f"\n  Tables actually in this file ({len(tables)}):")
        for t in sorted(tables):
            print(f"    {t}")
    else:
        print("\n  The file appears to be empty.")
    return False


def connect_to_db(db_path: str) -> sqlite3.Connection:
    """Read-only connection (mode=ro): cannot write, safe while Plex is running,
    and sees Plex's latest changes through its WAL file."""
    return cfg.connect_ro(db_path)


# ─── Formatters ───────────────────────────────────────────────────────────────

def fmt_duration(ms: Optional[int]) -> str:
    if ms is None:
        return "N/A"
    total_seconds = ms // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    return f"{minutes}m {seconds}s" if minutes else f"{seconds}s"


def fmt_size(size_bytes: Optional[int]) -> str:
    if size_bytes is None:
        return "N/A"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:,.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:,.1f} PB"


def fmt_timestamp(ts: Optional[int]) -> str:
    if ts is None:
        return "N/A"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def fmt_type_name(metadata_type: int) -> str:
    return METADATA_TYPES.get(metadata_type, f"unknown({metadata_type})")


def fmt_tag_type(tag_type: int) -> str:
    return TAG_TYPES.get(tag_type, f"unknown({tag_type})")


def fmt_stream_type(stream_type: int) -> str:
    return STREAM_TYPES.get(stream_type, f"unknown({stream_type})")


def truncate(text: Optional[str], max_len: int = 60) -> str:
    if text is None:
        return "N/A"
    return text[:max_len - 3] + "..." if len(text) > max_len else text


def resolve_media_type(name: str) -> int:
    rev = {v: k for k, v in METADATA_TYPES.items()}
    mt  = rev.get(name.lower())
    if mt is None:
        print(f"Unknown type '{name}'. Valid: {', '.join(METADATA_TYPES.values())}")
        sys.exit(1)
    return mt


def fetch_file_paths(conn, metadata_ids: list) -> dict:
    if not metadata_ids:
        return {}
    placeholders = ",".join("?" * len(metadata_ids))
    rows = conn.execute(f"""
        SELECT mitem.metadata_item_id, mp.file
        FROM media_parts mp
        JOIN media_items mitem ON mp.media_item_id = mitem.id
        WHERE mitem.metadata_item_id IN ({placeholders})
        AND mp.file != ''
        ORDER BY mitem.metadata_item_id, mp.id
    """, metadata_ids).fetchall()
    result: dict = {}
    for r in rows:
        result.setdefault(r["metadata_item_id"], []).append(r["file"])
    return result


def fmt_path(path: str, max_len: int = 100) -> str:
    if len(path) <= max_len:
        return path
    return "..." + path[-(max_len - 3):]


def print_media_table(rows, paths: dict = None) -> None:
    hdr = f"  {'ID':<8} {'Type':<10} {'Year':<6} {'Title':<70} {'Section':<25} {'Added'}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in rows:
        print(f"  {r['id']:<8} {fmt_type_name(r['metadata_type']):<10} "
              f"{str(r['year']) if r['year'] else 'N/A':<6} "
              f"{truncate(r['title'], 68):<70} "
              f"{truncate(r['section_name'] or 'N/A', 23):<25} "
              f"{fmt_timestamp(r['added_at'])}")
        if paths:
            for p in paths.get(r['id'], []):
                print(f"           {fmt_path(p)}")
    print(f"\n  Results: {len(rows)}")


def prompt(label: str, default: str = "") -> str:
    suffix = f" [Enter={default}]" if default else ""
    return input(f"  {label}{suffix}: ").strip() or default


def next_query(label: str) -> str:
    """Ask for the next search after a result list; blank returns to the menu."""
    return input(f"\n  {label} [Enter=back to menu]: ").strip().strip('"')


def prompt_int(label: str, default: int) -> int:
    raw = prompt(label, str(default))
    return int(raw) if raw.isdigit() else default


# ─── SQL Queries ──────────────────────────────────────────────────────────────

QUERIES = {
    "list_sections": """
        SELECT id, name, section_type, created_at, updated_at
        FROM library_sections ORDER BY name
    """,
    "search": """
        SELECT mi.id, mi.metadata_type, mi.title, mi.title_sort,
               mi.year, mi.duration, mi.rating, mi.added_at, mi.updated_at,
               mi.library_section_id, ls.name AS section_name
        FROM metadata_items mi
        LEFT JOIN library_sections ls ON mi.library_section_id = ls.id
        WHERE 1=1
    """,
    "info": """
        SELECT mi.id, mi.metadata_type, mi.title, mi.title_sort,
               mi.year, mi.duration, mi.rating, mi.summary,
               mi.added_at, mi.updated_at, mi.library_section_id,
               mi.guid, ls.name AS section_name
        FROM metadata_items mi
        LEFT JOIN library_sections ls ON mi.library_section_id = ls.id
        WHERE mi.id = ?
    """,
    "file_paths": """
        SELECT mp.id AS part_id, mp.file, mp.size,
               mp.duration AS part_duration, mi.id AS media_item_id
        FROM media_parts mp
        JOIN media_items mi ON mp.media_item_id = mi.id
        WHERE mi.metadata_item_id = ?
        ORDER BY mp.id
    """,
    "streams": """
        SELECT ms.id AS stream_id, ms.stream_type, ms.codec,
               ms.index, ms.language, ms.language_code,
               ms.title AS stream_title, ms.display_title,
               ms.selected, ms.width, ms.height, ms.audio_channels
        FROM media_streams ms
        WHERE ms.media_item_id IN (
            SELECT id FROM media_items WHERE metadata_item_id = ?
        )
        ORDER BY ms.media_item_id, ms.index
    """,
    "tags": """
        SELECT DISTINCT tg.id AS tag_id, tg.tag, tg.tag_type
        FROM tags tg
        JOIN taggings tgg ON tg.id = tgg.tag_id
        WHERE tgg.metadata_item_id = ?
        ORDER BY tg.tag_type, tg.tag
    """,
    "stats": """
        SELECT mi.metadata_type, COUNT(*) AS count,
               ls.name AS section_name,
               MIN(mi.year) AS min_year, MAX(mi.year) AS max_year,
               SUM(CASE WHEN mi.rating IS NOT NULL THEN 1 ELSE 0 END) AS rated_count
        FROM metadata_items mi
        LEFT JOIN library_sections ls ON mi.library_section_id = ls.id
        WHERE mi.metadata_type IN (1, 2, 4, 8, 9, 10)
        GROUP BY mi.metadata_type, mi.library_section_id
        ORDER BY mi.metadata_type, ls.name
    """,
    "find_file": """
        SELECT mp.id AS part_id, mp.file, mp.size,
               mi.metadata_item_id, mdi.title, mdi.metadata_type,
               mdi.year, ls.name AS section_name
        FROM media_parts mp
        JOIN media_items mi ON mp.media_item_id = mi.id
        JOIN metadata_items mdi ON mi.metadata_item_id = mdi.id
        LEFT JOIN library_sections ls ON mdi.library_section_id = ls.id
        WHERE mp.file LIKE ?
        ORDER BY mdi.title
    """,
    "duplicates": """
        SELECT mp.file, COUNT(*) AS part_count,
               COUNT(DISTINCT mi.metadata_item_id) AS item_count,
               GROUP_CONCAT(DISTINCT mdi.title, ' ||| ') AS titles,
               GROUP_CONCAT(DISTINCT CAST(mdi.id AS TEXT), ',') AS metadata_ids
        FROM media_parts mp
        JOIN media_items mi ON mp.media_item_id = mi.id
        JOIN metadata_items mdi ON mi.metadata_item_id = mdi.id
        GROUP BY mp.file
        HAVING COUNT(DISTINCT mi.metadata_item_id) > 1
        ORDER BY item_count DESC
    """,
    "orphaned_parts": """
        SELECT p.id, p.file, p.size
        FROM media_parts p
        LEFT JOIN media_items mi ON p.media_item_id = mi.id
        WHERE mi.id IS NULL
        ORDER BY p.id
    """,
}

_RECENT_BASE = """
    SELECT mi.id, mi.metadata_type, mi.title, mi.year,
           mi.duration, mi.rating, mi.added_at,
           mi.library_section_id, ls.name AS section_name
    FROM metadata_items mi
    LEFT JOIN library_sections ls ON mi.library_section_id = ls.id
    WHERE {type_clause}
    ORDER BY mi.added_at DESC LIMIT ?
"""

_TAG_SEARCH_BASE = """
    SELECT DISTINCT mi.id, mi.metadata_type, mi.title, mi.year,
           mi.added_at, mi.library_section_id, ls.name AS section_name
    FROM metadata_items mi
    LEFT JOIN library_sections ls ON mi.library_section_id = ls.id
    JOIN taggings tgg ON mi.id = tgg.metadata_item_id
    JOIN tags tg ON tgg.tag_id = tg.id
    WHERE tg.tag LIKE ? {type_clause}
    ORDER BY mi.title
    LIMIT ?
"""


# ─── CLI Parsing ──────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plex Database Search & Reporting Tool (read-only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Run without a subcommand to enter interactive mode.

            Examples:
              plex.bat                                        (interactive)
              plex.bat search "Inception"
              plex.bat search --type movie --year 2024 "Dune"
              plex.bat recent --limit 30
              plex.bat info 12345
              plex.bat find-file "Movies/Inception.mkv"
              plex.bat find-file "V:\\Japanese\\Samurai\\47 Ronin"
              plex.bat tag --type director "Nolan"
              plex.bat stats
              plex.bat duplicates
              plex.bat orphaned
              plex.bat check

            To delete parts or clean up backups:
              plex.bat delete-part 310534
              plex.bat cleanup-backups
        """),
    )
    parser.add_argument("--db", default=str(cfg.LIBRARY_DB),
                        help="Plex library database (default: the live one on this PC)")

    sub = parser.add_subparsers(dest="command", help="Available commands")

    sub.add_parser("list-sections", help="List all library sections")

    p = sub.add_parser("search", help="Search media by title")
    p.add_argument("query", type=str)
    p.add_argument("--type", dest="media_type", type=str, default=None,
                   help="Filter: movie, show, episode, artist, album, track")
    p.add_argument("--year", type=int, default=None)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--section", type=str, default=None)

    p = sub.add_parser("tag", help="Search by tag (genre, director, actor…)")
    p.add_argument("query", type=str)
    p.add_argument("--type", dest="tag_type", type=str, default=None,
                   help=f"Tag type: {', '.join(TAG_TYPES.values())}")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("recent", help="Most recently added media")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--type", dest="media_type", type=str, default=None)

    p = sub.add_parser("info", help="Full details for a metadata item (files + streams + tags)")
    p.add_argument("id", type=int)

    p = sub.add_parser("find-file", help="Find media by file path fragment")
    p.add_argument("query", type=str)
    p.add_argument("--limit", type=int, default=30)

    sub.add_parser("stats",      help="Library statistics")
    sub.add_parser("duplicates", help="Find duplicate file references")
    sub.add_parser("check",      help="Database integrity check (skips FTS/spellfix tables)")
    sub.add_parser("orphaned",   help="List media_parts with no parent media_item")

    return parser.parse_args()


# ─── Commands ─────────────────────────────────────────────────────────────────

def cmd_list_sections(conn):
    rows = conn.execute(QUERIES["list_sections"]).fetchall()
    if not rows:
        print("No library sections found.")
        return
    hdr = f"{'ID':<5} {'Name':<40} {'Type':<12} {'Created':<22} {'Updated':<22}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['id']:<5} {truncate(r['name'], 38):<40} "
              f"{fmt_type_name(r['section_type']):<12} "
              f"{fmt_timestamp(r['created_at']):<22} "
              f"{fmt_timestamp(r['updated_at']):<22}")
    print(f"\nTotal sections: {len(rows)}")


def cmd_search(conn, args):
    conditions = ["mi.title LIKE ?"]
    params = [f"%{args.query}%"]
    if args.media_type:
        conditions.append("mi.metadata_type = ?")
        params.append(resolve_media_type(args.media_type))
    if args.year is not None:
        conditions.append("mi.year = ?")
        params.append(args.year)
    if args.section:
        conditions.append("ls.name LIKE ?")
        params.append(f"%{args.section}%")
    query = QUERIES["search"] + " AND " + " AND ".join(conditions)
    query += " ORDER BY mi.added_at DESC LIMIT ?"
    params.append(args.limit)
    rows = conn.execute(query, params).fetchall()
    if not rows:
        print("  No results found.")
        return
    paths = fetch_file_paths(conn, [r["id"] for r in rows])
    print_media_table(rows, paths)


def cmd_search_tag(conn, tag_value: str, tag_type_name: Optional[str], limit: int):
    rev = {v: k for k, v in TAG_TYPES.items()}
    if tag_type_name:
        tt = rev.get(tag_type_name.lower())
        if tt is None:
            print(f"  Unknown tag type '{tag_type_name}'.")
            print(f"  Valid: {', '.join(TAG_TYPES.values())}")
            return
        type_clause = "AND tg.tag_type = ?"
        params = [f"%{tag_value}%", tt, limit]
    else:
        type_clause = ""
        params = [f"%{tag_value}%", limit]
    query = _TAG_SEARCH_BASE.format(type_clause=type_clause)
    rows = conn.execute(query, params).fetchall()
    if not rows:
        print(f"  No results for tag '{tag_value}'.")
        return
    paths = fetch_file_paths(conn, [r["id"] for r in rows])
    print_media_table(rows, paths)


def cmd_recent(conn, args):
    if args.media_type:
        type_clause = "mi.metadata_type = ?"
        params = [resolve_media_type(args.media_type), args.limit]
    else:
        type_clause = "mi.metadata_type IN (1, 2, 4)"
        params = [args.limit]
    query = _RECENT_BASE.format(type_clause=type_clause)
    rows = conn.execute(query, params).fetchall()
    if not rows:
        print("  No recently added items found.")
        return
    paths = fetch_file_paths(conn, [r["id"] for r in rows])
    print_media_table(rows, paths)


def cmd_info(conn, item_id):
    row = conn.execute(QUERIES["info"], (item_id,)).fetchone()
    if not row:
        print(f"  Item {item_id} not found.")
        return
    print(f"\n  {'=' * 60}")
    print(f"  {'Metadata Item':<20} {row['id']}")
    print(f"  {'=' * 60}")
    print(f"  {'Title':<20} {row['title']}")
    print(f"  {'Sort Title':<20} {row['title_sort'] or 'N/A'}")
    print(f"  {'Type':<20} {fmt_type_name(row['metadata_type'])} ({row['metadata_type']})")
    print(f"  {'Year':<20} {row['year'] or 'N/A'}")
    print(f"  {'Duration':<20} {fmt_duration(row['duration'])}")
    print(f"  {'Rating':<20} {row['rating'] or 'N/A'}")
    print(f"  {'Section':<20} {row['section_name'] or 'N/A'} (ID {row['library_section_id']})")
    print(f"  {'GUID':<20} {row['guid'] or 'N/A'}")
    print(f"  {'Added':<20} {fmt_timestamp(row['added_at'])}")
    print(f"  {'Updated':<20} {fmt_timestamp(row['updated_at'])}")
    if row['summary']:
        print(f"  {'Summary':<20} {textwrap.fill(row['summary'], 80, subsequent_indent=' '*22)}")

    tags = conn.execute(QUERIES["tags"], (item_id,)).fetchall()
    if tags:
        by_type = {}
        for t in tags:
            by_type.setdefault(fmt_tag_type(t['tag_type']), []).append(t['tag'])
        print(f"\n  {'─' * 60}")
        print("  Scraped metadata (tags):")
        for ttype, tlist in sorted(by_type.items()):
            print(f"    {ttype.title():<16}: {', '.join(sorted(tlist))}")

    parts = conn.execute(QUERIES["file_paths"], (item_id,)).fetchall()
    if parts:
        print(f"\n  {'─' * 60}")
        print(f"  File paths on NAS ({len(parts)} part(s)):")
        for p in parts:
            print(f"    [{p['part_id']}] {p['file']}")
            print(f"          Size: {fmt_size(p['size'])},  Duration: {fmt_duration(p['part_duration'])}")

    streams = conn.execute(QUERIES["streams"], (item_id,)).fetchall()
    if streams:
        print(f"\n  {'─' * 60}")
        print(f"  Streams ({len(streams)} total):")
        for s in streams:
            stype = fmt_stream_type(s['stream_type'])
            desc  = s['codec'] or 'N/A'
            if s['language']:
                desc += f" [{s['language']}]"
            if s['width'] and s['height']:
                desc += f" {s['width']}x{s['height']}"
            if s['audio_channels']:
                desc += f" {s['audio_channels']}ch"
            sel = " *" if s['selected'] else ""
            print(f"    [{s['stream_id']}] {stype:<10} {desc}{sel}")
    print()


def cmd_find_file(conn, query, limit):
    query = normalize_path_query(query)
    rows = conn.execute(QUERIES["find_file"], (f"%{query}%",)).fetchall()
    if not rows:
        print(f"  No files matching '{query}' found.")
        return
    hdr = f"  {'PartID':<8} {'Type':<10} {'Year':<6} {'Title':<55} {'Section':<20} {'Size':<12} File"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in rows[:limit]:
        print(f"  {r['part_id']:<8} {fmt_type_name(r['metadata_type']):<10} "
              f"{str(r['year']) if r['year'] else 'N/A':<6} "
              f"{truncate(r['title'], 53):<55} "
              f"{truncate(r['section_name'] or 'N/A', 18):<20} "
              f"{fmt_size(r['size']):<12} {r['file']}")
    print(f"\n  Results: {len(rows)}")


def cmd_stats(conn):
    rows = conn.execute(QUERIES["stats"]).fetchall()
    if not rows:
        print("  No statistics available.")
        return
    total = 0
    hdr = f"  {'Type':<12} {'Section':<30} {'Count':<8} {'Years':<14} {'Rated'}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in rows:
        yr = f"{r['min_year']}-{r['max_year']}" if r['min_year'] else "N/A"
        total += r['count']
        print(f"  {fmt_type_name(r['metadata_type']):<12} "
              f"{truncate(r['section_name'] or 'N/A', 28):<30} "
              f"{r['count']:<8} {yr:<14} {r['rated_count']}")
    print(f"\n  Total media items: {total}")


def cmd_check(conn):
    SKIP = ("fts", "_segdir", "_segments", "_docsize", "_stat", "spellfix")
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall() if not any(p in r[0].lower() for p in SKIP)]

    print(f"  Checking {len(tables)} regular tables...\n")
    print(f"  {'Table':<45} {'Rows':>10}")
    print("  " + "-" * 57)

    import time
    errors = []
    t0 = time.time()
    for tbl in tables:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM [{tbl}]").fetchone()[0]
            print(f"  {'OK':<4} {tbl:<43} {n:>10,}")
        except Exception as e:
            errors.append((tbl, str(e)))
            print(f"  {'ERR':<4} {tbl:<43} {e}")

    elapsed = time.time() - t0
    print(f"\n  --- Relationship checks ---")
    rel_checks = [
        ("Orphan media_parts (no media_item)",
         "SELECT COUNT(*) FROM media_parts mp LEFT JOIN media_items mi ON mp.media_item_id=mi.id WHERE mi.id IS NULL"),
        ("Orphan media_items (no metadata_item)",
         "SELECT COUNT(*) FROM media_items mi LEFT JOIN metadata_items mdi ON mi.metadata_item_id=mdi.id WHERE mdi.id IS NULL"),
        ("Orphan taggings (no tag)",
         "SELECT COUNT(*) FROM taggings tgg LEFT JOIN tags tg ON tgg.tag_id=tg.id WHERE tg.id IS NULL"),
        ("metadata_items with missing section",
         "SELECT COUNT(*) FROM metadata_items WHERE library_section_id IS NULL AND metadata_type IN (1,2,4)"),
        ("media_parts with empty file path",
         "SELECT COUNT(*) FROM media_parts WHERE file IS NULL OR file = ''"),
    ]
    for label, sql in rel_checks:
        try:
            n = conn.execute(sql).fetchone()[0]
            status = "OK  " if n == 0 else "WARN"
            print(f"  {status}  {label}: {n:,}" if n else f"  {status}  {label}")
        except Exception as e:
            print(f"  ERR   {label}: {e}")

    print(f"\n  Completed in {elapsed:.1f}s")
    if errors:
        print(f"  {len(errors)} table error(s) — likely missing SQLite extensions (FTS tokenizers).")
    else:
        print(f"  All regular tables readable.")


def cmd_duplicates(conn):
    rows = conn.execute(QUERIES["duplicates"]).fetchall()
    if not rows:
        print("  No duplicate file references found.")
        return
    print(f"\n  Found {len(rows)} file(s) referenced by multiple items:\n")
    for r in rows:
        titles = r['titles'].split(" ||| ")
        ids    = r['metadata_ids'].split(",")
        print(f"  File: {r['file']}")
        print(f"  Referenced by {r['item_count']} item(s):")
        for tid, title in zip(ids, titles):
            print(f"    [ID {tid}] {title}")
        print()


def cmd_find_orphaned(conn) -> None:
    """List media_parts with no parent media_item. Use plex.bat delete-part <ID> to remove."""
    rows = conn.execute(QUERIES["orphaned_parts"]).fetchall()
    if not rows:
        print("  No orphaned parts found.")
        return
    print(f"  Found {len(rows)} orphaned part(s):\n")
    for r in rows:
        print(f"  Part ID : {r['id']}")
        print(f"    File  : {r['file']}")
        print(f"    Size  : {fmt_size(r['size'])}\n")
    print(f"  To delete: plex.bat delete-part <Part ID>")


# ─── Interactive Mode ─────────────────────────────────────────────────────────

TAG_TYPE_NAMES = sorted(TAG_TYPES.values())

MENU = [
    ("title",    "Search by title"),
    ("file",     "Search by file path (on NAS)"),
    ("tag",      "Search by scraped metadata  (genre, director, actor…)"),
    ("recent",   "Recent additions"),
    ("info",     "Full item details            (files + streams + tags)"),
    ("sections", "List library sections"),
    ("stats",    "Library statistics"),
    ("dupes",    "Find duplicate file references"),
    ("orphaned", "Find orphaned media parts    (read-only — use plex_db.py to delete)"),
    ("check",    "Database integrity check"),
    ("inspect",  "Inspect DB tables & columns"),
]

MENU_HELP = {
    "title":    ("Search by Title", [
        "Searches title via LIKE across all metadata items (movies, shows, episodes...).",
        "Optional filters: media type, year, library section.",
    ]),
    "file":     ("Search by File Path", [
        "Matches a NAS file-path fragment against media_parts.file.",
        "Full paths (V:\\..., \\\\192.168.1.11\\...) match whichever form Plex stored.",
    ]),
    "tag":      ("Search by Scraped Metadata", [
        "Searches genre, director, actor, writer, studio and other scraped tags",
        "via the taggings join. Optionally restrict to one tag type.",
    ]),
    "recent":   ("Recent Additions", [
        "Lists most recently added items, ordered by added date.",
        "Optional media-type filter and result limit.",
    ]),
    "info":     ("Full Item Details", [
        "Full detail dump for one metadata item: title, year, summary,",
        "tags, file parts, and streams.",
    ]),
    "sections": ("List Library Sections", [
        "Lists all Plex library sections with IDs, names, types and timestamps.",
    ]),
    "stats":    ("Library Statistics", [
        "Item counts per section and type, plus year range and rated-item count.",
    ]),
    "dupes":    ("Find Duplicate File References", [
        "Finds file paths referenced by more than one metadata item —",
        "may indicate stale duplicate entries worth investigating.",
    ]),
    "orphaned": ("Find Orphaned Media Parts", [
        "Lists media_parts rows with no parent media_item.",
        "Read-only — use plex.bat's Database Management branch to delete.",
    ]),
    "check":    ("Database Integrity Check", [
        "Row counts across all regular tables plus 5 orphan-relationship checks.",
        "Skips FTS/spellfix virtual tables (not readable via standard SQLite).",
    ]),
    "inspect":  ("Inspect DB Tables & Columns", [
        "Full table and column listing for the connected database.",
    ]),
}


def interactive_mode(conn, db_path: str):
    print(f"\n  Plex DB: {db_path}")
    print(f"  SQLite:  {sqlite3.sqlite_version}")
    print(f"  Mode:    READ-ONLY")

    while True:
        print("\n" + "=" * 60)
        print("  PLEX LIBRARY SEARCH  (read-only)")
        print("=" * 60)
        for i, (_, label) in enumerate(MENU, 1):
            print(f"  [{i}] {label}")
        print("  [q] Quit")

        choice = input("\n  Choice: ").strip().lower()

        if choice == 'q':
            print("  Goodbye.")
            break

        if not choice.isdigit() or not (1 <= int(choice) <= len(MENU)):
            print("  Invalid choice.")
            continue

        field = MENU[int(choice) - 1][0]
        title, bullets = MENU_HELP[field]
        explain(title, bullets)

        try:
            if field == "title":
                q = prompt("Title contains")
                if not q:
                    continue
                mt     = prompt("Media type  [movie/show/episode/all]", "all")
                mt     = None if mt == "all" else mt
                yr_raw = prompt("Year filter", "skip")
                yr     = int(yr_raw) if yr_raw.isdigit() else None
                sec    = prompt("Section name", "skip")
                sec    = None if sec == "skip" else sec
                limit  = prompt_int("Max results", 50)
                while q:
                    print()
                    cmd_search(conn, argparse.Namespace(
                        query=q, media_type=mt, year=yr, section=sec, limit=limit))
                    q = next_query("Next title (same filters)")
                continue

            elif field == "file":
                q = prompt("File path contains")
                if not q:
                    continue
                limit = prompt_int("Max results", 30)
                while q:
                    print()
                    cmd_find_file(conn, q, limit)
                    q = next_query("Next file path")
                continue

            elif field == "tag":
                print(f"  Tag types: {', '.join(TAG_TYPE_NAMES)}")
                tt = prompt("Tag type   (Enter for any)", "")
                tt = tt or None
                q  = prompt("Tag value contains")
                if not q:
                    continue
                limit = prompt_int("Max results", 50)
                while q:
                    print()
                    cmd_search_tag(conn, q, tt, limit)
                    q = next_query("Next tag value (same tag type)")
                continue

            elif field == "recent":
                mt    = prompt("Media type  [movie/show/episode/all]", "all")
                mt    = None if mt == "all" else mt
                limit = prompt_int("How many", 20)
                print()
                cmd_recent(conn, argparse.Namespace(media_type=mt, limit=limit))

            elif field == "info":
                id_raw = prompt("Metadata item ID")
                if not id_raw.isdigit():
                    print("  Please enter a numeric ID.")
                    continue
                cmd_info(conn, int(id_raw))

            elif field == "sections":
                cmd_list_sections(conn)

            elif field == "stats":
                cmd_stats(conn)

            elif field == "dupes":
                cmd_duplicates(conn)

            elif field == "orphaned":
                cmd_find_orphaned(conn)

            elif field == "check":
                cmd_check(conn)

            elif field == "inspect":
                tables = inspect_schema(conn)
                print(f"  Tables in database ({len(tables)}):\n")
                for t in tables:
                    try:
                        cols = conn.execute(f"PRAGMA table_info({t})").fetchall()
                        col_names = ", ".join(c["name"] for c in cols)
                        print(f"  {t}")
                        print(f"    {col_names}")
                    except Exception:
                        print(f"  {t}  (could not read columns)")
                print()

        except KeyboardInterrupt:
            print()
            continue

        input("\n  Press Enter to continue...")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    print(f"Database: {args.db}")

    try:
        conn = connect_to_db(args.db)
    except sqlite3.Error as e:
        print(f"Connection error: {e}")
        print(f"\nExpected the Plex database at: {cfg.LIBRARY_DB}")
        print("Set PLEX_DATA in .env if Plex's data folder has moved.")
        sys.exit(1)

    print(f"  SQLite: {sqlite3.sqlite_version}\n")

    if not verify_schema(conn):
        list_db_files(os.path.dirname(args.db))
        conn.close()
        sys.exit(1)

    try:
        cmds = {
            "list-sections": lambda: cmd_list_sections(conn),
            "search":        lambda: cmd_search(conn, args),
            "tag":           lambda: cmd_search_tag(conn, args.query, args.tag_type, args.limit),
            "recent":        lambda: cmd_recent(conn, args),
            "info":          lambda: cmd_info(conn, args.id),
            "find-file":     lambda: cmd_find_file(conn, args.query, args.limit),
            "stats":         lambda: cmd_stats(conn),
            "duplicates":    lambda: cmd_duplicates(conn),
            "check":         lambda: cmd_check(conn),
            "orphaned":      lambda: cmd_find_orphaned(conn),
        }
        fn = cmds.get(args.command)
        if fn:
            fn()
        elif args.command is None:
            interactive_mode(conn, args.db)
        else:
            print(f"Unknown command '{args.command}'. Use --help for usage.")
    except sqlite3.DatabaseError as e:
        print(f"\nDatabase error: {e}")
        print("This may be due to Plex's custom ICU collation or FTS tokenizer.")
        print("Plex's dated backups are in the same folder, e.g.:")
        print(f"  plex.bat --db \"{cfg.LIBRARY_DB}-YYYY-MM-DD\" ...")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
