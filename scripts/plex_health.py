#!/usr/bin/env python3
"""
Plex database health tools for the Plex server on this PC.

  check     Integrity check of the live databases with Plex's own SQLite (read-only,
            safe while Plex runs). Replaces DBRepair-Diagnostic.ps1.
  missing   Dead links: video files and external subtitles Plex knows about that no longer exist on the NAS
            (read-only report). Tells apart items with NO FILE LEFT (unavailable in Plex) from old copies
            of items that still play. Replaces DBRepair-Orphan-Finder.ps1 scan.
  trash     Show what Plex has marked as deleted and, after confirmation, let Plex
            empty the trash per library (Plex web API - the database is never edited
            directly). Replaces the Orphan-Finder "cleanup" that deleted rows itself.

Reports go to <project>\\reports\\.

  python plex_health.py check
  python plex_health.py missing [--library Chinese | --library ?] [--only "Japanese\\Samurai"]
  python plex_health.py trash [--library Chinese --yes]
  python plex_health.py scan [--library Chinese] [--no-wait]

  scan      Ask Plex to scan a library (or all) for new, changed and removed files and wait until it is
            done; refuses a library whose folder can't be reached (a scan then drops every film).
"""

import argparse
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import plex_config as cfg
from menu_common import confirm

if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPORTS = cfg.PROJECT_ROOT / "reports"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def report_file(name: str):
    REPORTS.mkdir(exist_ok=True)
    return REPORTS / f"{name}_{STAMP}.txt"


# ─── check ────────────────────────────────────────────────────────────────────

def cmd_check(_args) -> int:
    lines = []

    def out(s=""):
        print(s)
        lines.append(s)

    out("Plex database check (read-only, Plex SQLite)")
    out(f"  SQLite : {cfg.PLEX_SQLITE}")
    out(f"  Plex   : {'running' if cfg.plex_running() else 'stopped'}")
    if not cfg.PLEX_SQLITE.exists():
        out("  [ERROR] Plex SQLite.exe not found - set PLEX_SQLITE in .env.")
        return 1
    healthy = True
    for db in (cfg.LIBRARY_DB, cfg.BLOBS_DB):
        out()
        out(f"--- {db.name} ---")
        if not db.exists():
            out(f"  [ERROR] not found: {db}")
            healthy = False
            continue
        out(f"  Size      : {db.stat().st_size / 1024**2:,.1f} MB")
        info = cfg.plex_sqlite(db, "SELECT page_count, page_size, freelist_count FROM "
                                   "pragma_page_count, pragma_page_size, pragma_freelist_count;")
        out(f"  Pages     : {info}  (count|size|free)")
        for pragma in ("quick_check(1)", "integrity_check(1)"):
            print(f"  {pragma:<20} running...", end="\r", flush=True)
            res = cfg.plex_sqlite(db, f"PRAGMA {pragma};")
            ok = res.strip().lower() == "ok"
            healthy &= ok
            out(f"  {pragma:<20}: {'PASS' if ok else 'FAIL - ' + res[:300]}")
    out()
    if healthy:
        out("RESULT: databases are healthy.")
    else:
        out("RESULT: problems found. Repair: Stop Plex, then Plex Utility.bat -> [1] -> Repair.")
    rf = report_file("db_check")
    rf.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport: {rf}")
    return 0 if healthy else 2


# ─── missing ──────────────────────────────────────────────────────────────────

MISSING_SQL = """
    SELECT mp.id AS part_id, mp.file, md.id AS item_id, md.title, md.year,
           md.metadata_type, ls.name AS section, md.deleted_at
    FROM media_parts mp
    JOIN media_items mi    ON mp.media_item_id = mi.id
    JOIN metadata_items md ON mi.metadata_item_id = md.id
    LEFT JOIN library_sections ls ON md.library_section_id = ls.id
    WHERE mp.file IS NOT NULL AND mp.file != ''
"""
# external subtitles (.srt/.idx … next to the video) Plex links to - a dead one can stop the film playing
SUBS_SQL = """
    SELECT ms.url, md.id AS item_id, md.title, md.year, ls.name AS section
    FROM media_streams ms
    JOIN media_items mi    ON ms.media_item_id = mi.id
    JOIN metadata_items md ON mi.metadata_item_id = md.id
    LEFT JOIN library_sections ls ON md.library_section_id = ls.id
    WHERE ms.url LIKE 'file://%'
"""
TYPES = {1: "Movie", 4: "Episode", 10: "Track", 12: "Photo"}


def libraries() -> list:
    """[(id, name)] of the Plex libraries, by name."""
    conn = cfg.connect_ro()
    rows = conn.execute("SELECT id, name FROM library_sections ORDER BY name").fetchall()
    conn.close()
    return [(r["id"], r["name"]) for r in rows]


def sub_path(url: str) -> str:
    """Plex's file:// address of a subtitle -> a Windows path."""
    from urllib.parse import unquote
    p = unquote(url[len("file://"):])
    if p.startswith("/") and p[2:3] == ":":
        p = p[1:]                       # file:///V:/... -> V:/...
    elif p[1:2] != ":":
        p = "//" + p.lstrip("/")        # file://<NAS IP>/share/... -> //<NAS IP>/share/...
    return p.replace("/", "\\")


def pick_library(name: str) -> str:
    """'?' or '' -> ask which library (number); a name -> the library whose name matches it."""
    libs = libraries()
    if name and name != "?":
        hits = [n for _, n in libs if n.lower() == name.lower()] or [n for _, n in libs if name.lower() in n.lower()]
        if len(hits) == 1:
            return hits[0]
        print(f"[!] '{name}' matches {len(hits)} libraries: {', '.join(hits) or '-'}")
    for i, (_, n) in enumerate(libs, 1):
        print(f"  [{i:>2}] {n}")
    try:
        sel = input("\nLibrary number (Enter = all libraries): ").strip()
    except EOFError:
        sel = ""
    return libs[int(sel) - 1][1] if sel.isdigit() and 0 < int(sel) <= len(libs) else ""


def cmd_missing(args) -> int:
    if args.library:
        args.library = pick_library(args.library)
    conn = cfg.connect_ro()
    rows = conn.execute(MISSING_SQL).fetchall()
    subs = conn.execute(SUBS_SQL).fetchall()
    conn.close()
    if args.library:
        rows = [r for r in rows if r["section"] == args.library]
        subs = [s for s in subs if s["section"] == args.library]
    only = cfg.share_relative(args.only).lower() if args.only else ""
    if only:
        rows = [r for r in rows if only in cfg.share_relative(r["file"]).lower()]
        subs = [s for s in subs if only in cfg.share_relative(sub_path(s["url"])).lower()]
    where = (f" in library {args.library!r}" if args.library else "") + (f" under {args.only!r}" if only else "")
    print(f"Checking {len(rows):,} video files and {len(subs):,} subtitle links Plex knows about{where} "
          "(NAS over the network - can take a few minutes)...")

    # Check each share root once so an offline NAS is not reported as thousands of missing files.
    shares, offline = {}, set()
    for r in rows:
        c = cfg.canonical(r["file"])
        root = "\\".join(c.split("\\")[:4]) if c.startswith("\\\\") else c[:3]
        shares.setdefault(root, None)
    for root in shares:
        if not os.path.exists(root):
            offline.add(root)
            print(f"  [!] not reachable: {root} - its files are skipped")

    def gone(r):
        p = cfg.openable(r["file"])
        if any(p.startswith(o) for o in offline):
            return False
        return not os.path.exists(p)

    def sub_gone(s):
        p = sub_path(s["url"])
        if any(cfg.canonical(p).startswith(o) for o in offline):
            return False
        return not os.path.exists(cfg.openable(p))

    with ThreadPoolExecutor(max_workers=16) as pool:
        flags = list(pool.map(gone, rows, chunksize=64))
        sflags = list(pool.map(sub_gone, subs, chunksize=64))
    missing = [r for r, g in zip(rows, flags) if g]
    dead_subs = [s for s, g in zip(subs, sflags) if g]
    # an item whose other file still exists only has a dead OLD copy (e.g. the SD file after an HD replace, or the
    # name before a rename) - it still plays; an item with no file left shows as unavailable in Plex
    has_file = defaultdict(bool)
    for r, g in zip(rows, flags):
        has_file[r["item_id"]] |= not g

    by_section = defaultdict(list)
    for r in missing:
        by_section[r["section"] or "(no library)"].append(r)
    subs_by = defaultdict(list)
    for s in dead_subs:
        subs_by[s["section"] or "(no library)"].append(s)
    lost = {r["item_id"] for r in missing if not has_file[r["item_id"]]}
    lines = [f"Plex dead links - {datetime.now():%Y-%m-%d %H:%M}" + (f"   Library: {args.library}" if args.library
                                                                       else ""),
             f"Video files checked: {len(rows):,}   missing: {len(missing):,} "
             f"({len(lost)} items with NO file left, {len(missing) - sum(1 for r in missing if r['item_id'] in lost)}"
             f" old copies of items that still play)",
             f"Subtitle links checked: {len(subs):,}   missing: {len(dead_subs):,}"
             + (f"   Filter: {args.only}" if only else ""),
             "NO FILE LEFT = Plex shows the item as unavailable. OLD COPY = the item still has another file.",
             "In trash = Plex already marked the item deleted; 'trash' empties it.", ""]
    for section in sorted(set(by_section) | set(subs_by)):
        items = sorted(by_section[section], key=lambda r: (r["item_id"] not in lost, (r["title"] or "").lower()))
        lines.append(f"== {section} ({len(items)} video, {len(subs_by[section])} subtitle) ==")
        for r in items:
            kind = "NO FILE LEFT" if r["item_id"] in lost else "old copy"
            mark = "  [in trash]" if r["deleted_at"] else ""
            lines.append(f"  {kind:<12} {TYPES.get(r['metadata_type'], r['metadata_type'])} {r['item_id']}: "
                         f"{r['title']} ({r['year'] or '?'}){mark}")
            lines.append(f"      {r['file']}")
        for s in sorted(subs_by[section], key=lambda s: (s["title"] or "").lower()):
            lines.append(f"  subtitle     {s['item_id']}: {s['title']} ({s['year'] or '?'})")
            lines.append(f"      {sub_path(s['url'])}")
        lines.append("")
    if offline:
        lines.append("Not reachable (skipped): " + ", ".join(sorted(offline)))
    rf = report_file("missing_files")
    rf.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nVideo files missing: {len(missing):,} of {len(rows):,}   subtitle links missing: "
          f"{len(dead_subs):,} of {len(subs):,}")
    for section in sorted(set(by_section) | set(subs_by)):
        n_lost = len({r["item_id"] for r in by_section[section] if r["item_id"] in lost})
        n_old = sum(1 for r in by_section[section] if r["item_id"] not in lost)
        print(f"  {section}: {n_lost} with no file left, {n_old} old copies, {len(subs_by[section])} subtitles")
        for r in sorted((r for r in by_section[section] if r["item_id"] in lost),
                        key=lambda r: (r["title"] or "").lower()):
            print(f"      NO FILE LEFT: {r['title']} ({r['year'] or '?'})  {r['file']}")
    if offline:
        print("Not reachable (skipped): " + ", ".join(sorted(offline)))
    print(f"\nReport: {rf}")
    if missing or dead_subs:
        print("Next: in Plex scan the library (dead subtitles: also 'Refresh Metadata' on those films), then "
              "'Empty Trash' (here: 'trash') - it removes the entries with NO FILE LEFT and their watch history.")
    return 0


# ─── scan (update a library) ──────────────────────────────────────────────────

def section_info() -> list:
    """[{id, name, roots, items, trash}] for every library (read-only database)."""
    conn = cfg.connect_ro()
    out = []
    for s in conn.execute("SELECT id, name FROM library_sections ORDER BY name").fetchall():
        roots = [r[0] for r in conn.execute("SELECT root_path FROM section_locations WHERE library_section_id=?",
                                            (s["id"],))]
        items = conn.execute("SELECT COUNT(*) FROM metadata_items WHERE library_section_id=? AND metadata_type "
                             "IN (1, 2, 8, 9) AND deleted_at IS NULL", (s["id"],)).fetchone()[0]
        trash = conn.execute("SELECT COUNT(*) FROM metadata_items WHERE library_section_id=? AND metadata_type "
                             "IN (1, 2, 4, 8, 9, 10) AND deleted_at IS NOT NULL", (s["id"],)).fetchone()[0]
        out.append(dict(id=s["id"], name=s["name"], roots=roots, items=items, trash=trash))
    conn.close()
    return out


def chosen(name: str) -> list:
    """Libraries for a --library value: '' / 'all' / 'All libraries' = every one, else name (or part of it)."""
    libs = section_info()
    if not name or name.lower() in ("all", "all libraries"):
        return libs
    hits = [s for s in libs if s["name"].lower() == name.lower()] or \
           [s for s in libs if name.lower() in s["name"].lower()]
    if len(hits) != 1:
        print(f"[!] '{name}' matches {len(hits)} libraries: {', '.join(s['name'] for s in hits) or '-'}")
        return []
    return hits


def unreachable(sec: dict) -> list:
    """Library folders Windows can't open right now. Scanning (or emptying the trash) then would make Plex
    drop every film in them - so both refuse."""
    out = []
    for r in sec["roots"]:
        p = cfg.openable(r)
        if os.path.isdir(p):
            continue
        share = "\\".join(p.split("\\")[:4]) if p.startswith("\\\\") else p[:3]
        if not os.path.isdir(share):
            out.append(r)        # the NAS share itself is off - its films would wrongly count as gone
        else:
            print(f"  [i] {sec['name']}: folder {r} no longer exists (the share is fine) - "
                  "remove it from the library in Plex if it is not needed")
    return out


def refreshing() -> set:
    """Ids of the libraries Plex is scanning right now."""
    mc = cfg.plex_request("GET", "/library/sections")
    return {int(d["key"]) for d in mc.get("Directory", []) if d.get("refreshing")}


def cmd_scan(args) -> int:
    import time
    libs = chosen(args.library)
    if not libs:
        return 1
    if not cfg.plex_running():
        print("[!] Plex is not running - start it first.")
        return 1
    started = []
    for s in libs:
        bad = unreachable(s)
        if bad:
            print(f"  [!] {s['name']}: NOT scanned - folder not reachable: {', '.join(bad)} "
                  "(NAS off? A scan now would mark every film in it as gone)")
            continue
        cfg.plex_request("GET", f"/library/sections/{s['id']}/refresh")
        print(f"  [+] {s['name']}: Plex is scanning ({s['items']:,} items before)")
        started.append(s)
    if not started or args.no_wait:
        return 0
    print("\nWaiting for Plex to finish (Stop is safe - the scan carries on in Plex) …", flush=True)
    t0 = time.time()
    time.sleep(5)
    while True:
        busy = refreshing() & {s["id"] for s in started}
        if not busy:
            break
        mins = (time.time() - t0) / 60
        print(f"  still scanning after {mins:.0f} min: "
              f"{', '.join(s['name'] for s in started if s['id'] in busy)}", flush=True)
        time.sleep(30)
    time.sleep(10)   # Plex writes its last rows
    after = {s["id"]: s for s in section_info()}
    print(f"\nScan finished after {(time.time() - t0) / 60:.1f} min.")
    for s in started:
        a = after.get(s["id"], s)
        print(f"  {s['name']}: {a['items']:,} items ({a['items'] - s['items']:+,}), "
              f"in trash (file gone): {a['trash']:,}")
    if any(after[s["id"]]["trash"] for s in started if s["id"] in after):
        print("\nItems in trash have no file on the NAS any more. Check them first ('Find dead links'), "
              "then 'Empty trash' removes them (and their watch history) from Plex.")
    return 0


# ─── trash ────────────────────────────────────────────────────────────────────

def trash_with_files(section_id: int) -> list:
    """Items in a library's trash that still have a video file on disk (title, year, existing file).
    Plex marks such an item deleted when ONE of its files is gone (e.g. the old SD copy after an HD replace,
    or while a scan is still running); emptying the trash then could drop a film that still plays."""
    conn = cfg.connect_ro()
    rows = conn.execute("""SELECT md.id, md.title, md.year, mp.file FROM metadata_items md
        JOIN media_items mi ON mi.metadata_item_id = md.id JOIN media_parts mp ON mp.media_item_id = mi.id
        WHERE md.library_section_id = ? AND md.deleted_at IS NOT NULL AND mp.file != ''""",
                        (section_id,)).fetchall()
    conn.close()
    with ThreadPoolExecutor(max_workers=16) as pool:
        ok = list(pool.map(lambda r: os.path.exists(cfg.openable(r["file"])), rows))
    seen, out = set(), []
    for r, e in zip(rows, ok):
        if e and r["id"] not in seen:
            seen.add(r["id"])
            out.append((r["title"], r["year"], r["file"]))
    return out


def cmd_trash(args) -> int:
    if args.library:   # one library (or all) chosen in the window, already confirmed there with --yes
        libs = [s for s in chosen(args.library) if s["trash"]]
        if not libs:
            print("Nothing in the trash for that choice.")
            return 0
        if not cfg.plex_running():
            print("[!] Plex is not running - start it first.")
            return 1
        busy = refreshing()
        for s in libs:
            bad = unreachable(s)
            if bad:
                print(f"  [!] {s['name']}: NOT emptied - folder not reachable: {', '.join(bad)} "
                      "(the 'gone' films may only be offline)")
                continue
            if s["id"] in busy:
                print(f"  [!] {s['name']}: NOT emptied - Plex is still scanning it; try again when it is done")
                continue
            alive = trash_with_files(s["id"])
            if alive:
                print(f"  [!] {s['name']}: NOT emptied - {len(alive)} of its {s['trash']} trash item(s) still have "
                      "a file that plays (only an old copy is gone), e.g.:")
                for t, y, f in alive[:5]:
                    print(f"        {t} ({y or '?'})  {f}")
                print("      Fix: in Plex open such a film > ⋯ > 'Refresh Metadata' (or run 'Update library' "
                      "again); emptying now could remove films you still have.")
                continue
            if not args.yes and not confirm(f"Empty trash in {s['name']} ({s['trash']} items)?"):
                continue
            cfg.plex_request("PUT", f"/library/sections/{s['id']}/emptyTrash")
            print(f"  [+] {s['name']}: Plex is removing {s['trash']} item(s) whose file is gone")
        return 0
    conn = cfg.connect_ro()
    rows = conn.execute("""
        SELECT ls.id, ls.name, COUNT(md.id) AS n
        FROM metadata_items md JOIN library_sections ls ON md.library_section_id = ls.id
        WHERE md.deleted_at IS NOT NULL AND md.metadata_type IN (1, 2, 4, 8, 9, 10)
        GROUP BY ls.id ORDER BY ls.name""").fetchall()
    conn.close()
    if not rows:
        print("Plex's trash is empty - nothing to do.")
        return 0
    print("Items Plex has marked as deleted (file gone at the last scan):\n")
    for r in rows:
        print(f"  [{r['id']:>3}] {r['name']:<35} {r['n']:>6}")
    print("\nEmptying the trash removes these entries (and their watch history) from Plex.")
    print("If a NAS was just offline, cancel - the files may come back on the next scan.")
    if not cfg.plex_running():
        print("\n[!] Plex is not running - start it first (the API does the work).")
        return 1
    try:
        sel = input("\nLibrary ids to empty (comma separated), 'all', or Enter to cancel: ").strip().lower()
    except EOFError:
        sel = ""
    if not sel:
        print("Cancelled.")
        return 0
    ids = [r["id"] for r in rows] if sel == "all" else [int(s) for s in sel.replace(" ", "").split(",") if s.isdigit()]
    names = {r["id"]: r["name"] for r in rows}
    ids = [i for i in ids if i in names]
    if not ids or not confirm(f"Empty trash in {', '.join(names[i] for i in ids)}?"):
        print("Cancelled.")
        return 0
    for i in ids:
        try:
            cfg.plex_request("PUT", f"/library/sections/{i}/emptyTrash")
            print(f"  [+] {names[i]}: Plex is emptying the trash")
        except Exception as e:
            print(f"  [!] {names[i]}: {e}")
    return 0


def main():
    p = argparse.ArgumentParser(description="Plex database health tools (local Plex server)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="integrity check (read-only)")
    m = sub.add_parser("missing", help="files in Plex that are gone from disk (read-only report)")
    m.add_argument("--only", default="", help="only paths containing this, e.g. \"Japanese\\Samurai\" or a full V:\\ path")
    m.add_argument("--library", default="", help="only this Plex library (name, part of it, or ? to choose)")
    t = sub.add_parser("trash", help="show Plex's trash and empty it via the Plex API (asks first)")
    t.add_argument("--library", default="", help="empty only this library ('all' = every library with trash)")
    t.add_argument("--yes", action="store_true", help="already confirmed (the window asks first)")
    s = sub.add_parser("scan", help="ask Plex to scan a library for new/changed/removed files")
    s.add_argument("--library", default="all", help="library name (or part of it); 'all' = every library")
    s.add_argument("--no-wait", action="store_true", help="don't wait for Plex to finish")
    a = p.parse_args()
    sys.exit({"check": cmd_check, "missing": cmd_missing, "trash": cmd_trash, "scan": cmd_scan}[a.command](a))


if __name__ == "__main__":
    main()
