#!/usr/bin/env python3
"""
Video Utility Tools

A collection of NAS video library maintenance scripts.
Run via vdo.bat or directly: python vdo_util.py

Tools:
  [1] Subtitle Audit       — Classify video folders: external SRT / embedded English / no subs
  [2] Non-English Sub Scan — Find MKV files with non-English-only subtitle tracks
  [3] Move Files to Folders — Move loose files into individual subfolders (dots -> spaces,
                              subtitles follow their video; was mov2folder.py)
  [4] Sync SRT to Video    — Auto-sync subtitle timing to audio (sync_srt.py, faster-whisper)
  [5] Match SRTs to Videos — Identify which subtitle belongs to which episode (sync_srt.py --match)
  [6] Movie Folder Org (AI) — Translate + IMDb-year + rename loose files into
                              "English Title (Year) Foreign Title" folders (movieorg/)

Requires: ffprobe (part of ffmpeg) on PATH for subtitle scanning.
          faster-whisper (pip install faster-whisper) for SRT sync/match.
          DEEPSEEK_API_KEY for SRT matching.
"""

import os
import json
import shutil
import subprocess
import sys

from menu_common import explain

# Ensure UTF-8 I/O on Windows
if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VIDEO_EXTENSIONS = (
    '.mkv', '.mp4', '.m4v', '.mov',
    '.ts', '.m2ts', '.mts',
    '.avi', '.divx',
    '.wmv', '.asf',
    '.webm', '.flv',
    '.mpg', '.mpeg', '.m2v',
    '.vob', '.ogv', '.ogg',
    '.3gp', '.3g2',
)


# ─── Shared Utilities ─────────────────────────────────────────────────────────

def prompt_folder(label: str = "folder to scan") -> str:
    """Prompt until the user enters a valid existing directory path."""
    while True:
        path = input(f"  Enter {label}: ").strip().strip('"').strip("'")
        if os.path.isdir(path):
            return path
        print(f"  Not a valid directory: {path}")


def get_subtitle_streams(video_file: str) -> list:
    """Return subtitle stream list from ffprobe, or [] on failure."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_streams', '-select_streams', 's', video_file],
            capture_output=True, text=True, encoding='utf-8', errors='replace'
        )
        data = json.loads(result.stdout)
        return data.get('streams', [])
    except Exception:
        return []


# ─── Tool 1: Subtitle Audit ───────────────────────────────────────────────────

def tool_subtitle_audit() -> None:
    """
    Walk a folder tree and classify each subfolder by subtitle type:
      - External Subs : folder name contains 'sub' OR contains .srt files
      - Internal Subs : video file has embedded English subtitle track
      - No Subs       : no subtitles found at all

    Outputs three report files in the scanned folder:
      External Subs.txt, Internal Subs.txt, No Subs.txt
    """
    print("\n" + "=" * 60)
    print("  SUBTITLE AUDIT")
    print("=" * 60)
    main_folder = prompt_folder("root folder to scan")
    print(f"\n  Scanning: {main_folder}\n")

    folders_external   = []
    folders_internal   = []
    folders_no_subs    = []
    folders_subs_info  = []   # [(folder, [subtitle_desc, ...])]

    def has_external_subs(folder: str) -> bool:
        base = os.path.basename(folder).lower()
        if "sub" in base:
            return True
        try:
            return any(f.lower().endswith('.srt') for f in os.listdir(folder))
        except OSError:
            return False

    for root, _, files in os.walk(main_folder):
        if root == main_folder:
            continue

        if has_external_subs(root):
            folders_external.append(root)
            print(f"  [EXT] {os.path.relpath(root, main_folder)}")
            continue

        video_files = sorted(f for f in files if f.lower().endswith(VIDEO_EXTENSIONS))
        if not video_files:
            folders_no_subs.append(root)
            continue

        has_eng   = False
        sub_descs = []

        for vf in video_files:
            vpath   = os.path.join(root, vf)
            streams = get_subtitle_streams(vpath)
            for s in streams:
                lang  = s.get('tags', {}).get('language', 'unknown')
                codec = s.get('codec_name', 'unknown')
                sub_descs.append(f"{codec} [{lang}]")
                if lang.lower() == 'eng':
                    has_eng = True

        if has_eng:
            folders_internal.append(root)
            folders_subs_info.append((root, sub_descs))
            print(f"  [INT] {os.path.relpath(root, main_folder)}")
        else:
            folders_no_subs.append(root)

    # Promote parent folders whose external subs live in a Subs/ subfolder
    promoted   = set()
    ext_set    = set(folders_external)

    for folder in list(folders_no_subs):
        if any(e.startswith(folder + os.sep) for e in ext_set):
            folders_no_subs.remove(folder)
            folders_external.append(folder)
            promoted.add(folder)

    for folder in list(folders_internal):
        if any(e.startswith(folder + os.sep) for e in ext_set):
            folders_internal.remove(folder)
            folders_subs_info = [(f, s) for f, s in folders_subs_info if f != folder]
            folders_external.append(folder)
            promoted.add(folder)

    # Remove subfolder entries already represented by their promoted parent
    folders_external = [f for f in folders_external if os.path.dirname(f) not in promoted]

    folders_external.sort()
    folders_internal.sort()
    folders_no_subs.sort()

    def write_report(path: str, lines: list) -> None:
        with open(path, 'w', encoding='utf-8') as fh:
            for line in lines:
                fh.write(line + '\n')

    ext_path = os.path.join(main_folder, 'External Subs.txt')
    int_path = os.path.join(main_folder, 'Internal Subs.txt')
    nos_path = os.path.join(main_folder, 'No Subs.txt')

    write_report(ext_path, folders_external)
    write_report(int_path, [f for f, _ in folders_subs_info])
    write_report(nos_path, folders_no_subs)

    print(f"\n  Results:")
    print(f"    External subs : {len(folders_external):>5}  → {ext_path}")
    print(f"    Internal subs : {len(folders_internal):>5}  → {int_path}")
    print(f"    No subs       : {len(folders_no_subs):>5}  → {nos_path}")


# ─── Tool 2: Non-English Subtitle Scan ───────────────────────────────────────

def tool_non_english_scan() -> None:
    """
    Walk a folder tree and find MKV files that have at least one non-English
    subtitle track AND no English subtitle track at all.

    Filtering rules:
      - Missing language field → treated as English (file excluded)
      - Language 'unknown'     → skipped
      - Starts with 'en' or is 'eng' / 'english' → English (file excluded)
      - Anything else → non-English (included if no English track found)

    Outputs: Non-English Subs.txt in the scanned folder.
    """
    print("\n" + "=" * 60)
    print("  NON-ENGLISH SUBTITLE SCAN  (MKV only)")
    print("=" * 60)
    folder = prompt_folder("root folder to scan")
    print(f"\n  Scanning: {folder}\n  " + "-" * 56)

    matches     = []
    total_mkv   = 0

    for root, _, files in os.walk(folder):
        mkv_files = [f for f in files if f.lower().endswith('.mkv')]
        if mkv_files:
            print(f"  Processing: {os.path.relpath(root, folder)}")

        for fname in mkv_files:
            total_mkv += 1
            fpath    = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, folder)
            streams  = get_subtitle_streams(fpath)

            has_english     = False
            has_non_english = False
            non_eng_labels  = []

            for s in streams:
                if s.get('codec_type') != 'subtitle':
                    continue
                lang = s.get('tags', {}).get('language', s.get('language', '')).lower()
                if not lang:
                    has_english = True        # treat unknown as English
                    continue
                if lang == 'unknown':
                    continue
                if lang.startswith('en') or lang in ('eng', 'english'):
                    has_english = True
                else:
                    has_non_english = True
                    codec = s.get('codec_name', 'unknown')
                    non_eng_labels.append(f"{lang} ({codec})")

            if has_non_english and not has_english:
                matches.append({'path': rel_path, 'subtitles': ', '.join(non_eng_labels)})
                print(f"    Found: {fname}  [{', '.join(non_eng_labels)}]")

    report_path = os.path.join(folder, 'Non-English Subs.txt')
    with open(report_path, 'w', encoding='utf-8') as fh:
        fh.write(f"Scan Location : {folder}\n")
        fh.write(f"Total MKV     : {total_mkv}\n")
        fh.write(f"Matched       : {len(matches)}\n")
        fh.write("\n" + "-" * 80 + "\n\n")
        for item in matches:
            fh.write(f"File      : {item['path']}\n")
            fh.write(f"Subtitles : {item['subtitles']}\n\n")

    print(f"\n  " + "-" * 56)
    print(f"  Total MKV scanned : {total_mkv}")
    print(f"  Non-English only  : {len(matches)}")
    print(f"  Report            : {report_path}")


# ─── Tool 3: Move Files to Folders ────────────────────────────────────────────

def folder_name_for(filename: str) -> str:
    """Subfolder name for a file: its name without extension, dots -> spaces
    (from the old mov2folder.py: 'The.Movie.2020.mkv' -> 'The Movie 2020')."""
    return " ".join(os.path.splitext(filename)[0].replace(".", " ").split())


def plan_moves(folder: str) -> list:
    """(file, subfolder) for every loose file. Each video gets its own subfolder;
    subtitles and other files whose name starts with a video's name
    ('Movie.en.srt', 'Movie-poster.jpg') go into that video's subfolder
    instead of getting a folder of their own."""
    files = sorted(f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f)))
    videos = [f for f in files if f.lower().endswith(VIDEO_EXTENSIONS)]
    stems = sorted(((os.path.splitext(v)[0], v) for v in videos), key=lambda s: -len(s[0]))
    moves = []
    for f in files:
        owner = f
        if f not in videos:  # longest video name that this file's name starts with
            owner = next((v for stem, v in stems
                          if f.casefold().startswith(stem.casefold()) and f[len(stem):][:1] in ".-_ "), f)
        moves.append((f, folder_name_for(owner)))
    return moves


def plex_named(folder: str) -> bool:
    """Plex-standard names via Movie Folder Org's PlexRename step (movieorg\\plex_names.py): each loose film is
    found on TMDB, its folder + file + subtitles get PlexRename's names after you accept (owner, 19 Nov 2026).
    False when PlexRename's naming can't be loaded (then the old file-name folders are offered)."""
    from pathlib import Path
    org = Path(__file__).resolve().parent.parent / "movieorg"
    if str(org) not in sys.path:
        sys.path.insert(0, str(org))
    try:
        import movie_folder_organizer as mfo
        import plex_names as pnm
    except Exception as e:  # noqa: BLE001
        print(f"  [!] Movie Folder Org could not be loaded ({e})")
        return False
    cfg = mfo.load_config(mfo.DEFAULT_CONFIG_PATH)
    if not pnm.load(cfg.plexrename_folder):
        print(f"  [!] {pnm.why()}")
        return False
    logger = mfo.setup_logging(False)
    mfo.plex_names_run(Path(folder), cfg, mfo.read_api_key(cfg, logger),
                       {e.lower() for e in cfg.video_extensions}, pnm, logger, preview=False)
    rest = [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
    if rest:
        print(f"\n  {len(rest)} other loose file(s) left in the folder (skipped films, posters, notes …) - "
              "run this tool again with [2] to put them into folders named after the files.")
    return True


def tool_move_to_folders() -> None:
    """
    Move each loose file in a folder into its own subfolder named after the
    file (without extension, dots turned into spaces). Subtitles follow
    their video.

    Example:
      My.Movie.2020.mkv     →  My Movie 2020/My.Movie.2020.mkv
      My.Movie.2020.en.srt  →  My Movie 2020/My.Movie.2020.en.srt

    Only files directly in the selected folder are moved (not recursive).
    Merged in from V:\\Work\\vdo_util\\mov2folder.py (owner, 1 Nov 2026).
    """
    print("\n" + "=" * 60)
    print("  MOVE FILES INTO SUBFOLDERS")
    print("=" * 60)
    print("  (press Enter without typing to pick the folder in a window)")
    path = input("  Enter folder containing loose files: ").replace("﻿", "").strip().strip('"').strip("'")
    if not path:
        from tkinter import Tk, filedialog
        root = Tk()
        root.withdraw()
        path = filedialog.askdirectory(title="Select the folder to organize")
        root.destroy()
    if not path or not os.path.isdir(path):
        print(f"  Not a valid directory: {path or '(none chosen)'}")
        return
    folder = path

    print("\n  How should the new folders be named?")
    print("   [1] Plex standard from TMDB (default): English Title (Year) {tmdb-ID} Native title [RES]")
    print("       - the film file is renamed too, subtitles follow; you accept each film")
    print("   [2] After the file name, file names kept (as before)")
    how = input("  Choice [Enter = 1]: ").strip()
    if how != "2":
        if plex_named(folder):
            return
        print("  Falling back to naming the folders after the files.")

    moves = plan_moves(folder)
    if not moves:
        print("  No files found in that folder.")
        return

    print(f"\n  Found {len(moves)} file(s) -> {len({d for _, d in moves})} folder(s). Preview:\n")
    for f, dest in moves[:15]:
        print(f"    {f}  →  {dest}/")
    if len(moves) > 15:
        print(f"    ... and {len(moves) - 15} more")

    confirm = input(f"\n  Move all {len(moves)} file(s)? (y/n): ").strip().lower()
    if confirm != 'y':
        print("  Cancelled.")
        return

    moved  = 0
    errors = 0

    for filename, subfolder_name in moves:
        src = os.path.join(folder, filename)
        if not os.path.isfile(src):
            continue

        dest_dir = os.path.join(folder, subfolder_name)
        if os.path.exists(os.path.join(dest_dir, filename)):
            print(f"  [SKIP] {filename}: already in {subfolder_name}/")
            errors += 1
            continue

        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.move(src, os.path.join(dest_dir, filename))
            print(f"  Moved: {filename}  →  {subfolder_name}/")
            moved += 1
        except Exception as e:
            print(f"  [ERROR] {filename}: {e}")
            errors += 1

    print(f"\n  Done. Moved: {moved}  Errors: {errors}")


# ─── Tool 4: Sync SRT to Video ────────────────────────────────────────────────

def tool_sync_srt() -> None:
    """
    Launch sync_srt.py — auto-syncs an SRT file to a video's audio track
    using faster-whisper. Kept as a separate script so it can also be run
    directly: python sync_srt.py <video>
    """
    print("\n" + "=" * 60)
    print("  SYNC SRT TO VIDEO")
    print("=" * 60)
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_srt.py")
    subprocess.run([sys.executable, script])


# ─── Tool 5: Match SRTs to Videos ─────────────────────────────────────────────

def tool_match_srt() -> None:
    """
    Launch sync_srt.py --match — pairs every subtitle file in a folder with
    its video via a 5-minute transcript + DeepSeek comparison, writes
    'SRT Match Report.txt', and offers to rename mislabeled subtitles.
    """
    print("\n" + "=" * 60)
    print("  MATCH SRTs TO VIDEOS")
    print("=" * 60)
    folder = prompt_folder("folder containing videos and subtitles")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_srt.py")
    subprocess.run([sys.executable, script, "--match", folder, "--rename"])


# ─── Tool 6: Movie Folder Organizer (AI) ──────────────────────────────────────

def tool_movie_organizer() -> None:
    """
    Launch the AI Movie Folder Organizer (movieorg/movie_folder_organizer.py).

    Finds loose video files, translates the (foreign) title to English with
    DeepSeek, recovers the release year from the filename or IMDb, and moves
    each file into an "English Title (Year) Foreign Title" folder. Dry-runs
    first and asks for confirmation. Also runnable directly: movieorg.bat
    """
    print("\n" + "=" * 60)
    print("  MOVIE FOLDER ORGANIZER (AI)")
    print("=" * 60)
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(repo_root, "movieorg", "movie_folder_organizer.py")
    if not os.path.isfile(script):
        print(f"  [ERROR] Not found: {script}")
        return
    print(f"  Launching: {script}\n")
    subprocess.run([sys.executable, script, *sys.argv[1:]])


# ─── Interactive Menu ─────────────────────────────────────────────────────────

MENU = [
    ("audit",    "Subtitle Audit          — classify folders: external / internal / no subs"),
    ("nonengl",  "Non-English Sub Scan    — find MKV files with non-English-only subtitles"),
    ("move",     "Move Files to Folders   — loose films into Plex-named folders (TMDB)"),
    ("sync",     "Sync SRT to Video       — auto-sync subtitle timing via Whisper"),
    ("match",    "Match SRTs to Videos    — identify which subtitle belongs to which episode"),
    ("org",      "Movie Folder Org (AI)   — translate + IMDb-year + rename into folders"),
]

MENU_HELP = {
    "audit":   ("Subtitle Audit", [
        "Classifies each subfolder: External Subs (folder name/contents suggest .srt files),",
        "Internal Subs (embedded English track detected via ffprobe), or No Subs.",
        "Writes 3 report files (External/Internal/No Subs.txt) to the scanned folder.",
    ]),
    "nonengl": ("Non-English Subtitle Scan", [
        "Scans MKV files for a non-English subtitle track with no matching English track.",
        "Writes 'Non-English Subs.txt' to the scanned folder.",
    ]),
    "move":    ("Move Files to Folders", [
        "Moves top-level loose films into their own folder. Default [1]: the film is found",
        "on TMDB (PlexRename's rules) and folder + file get the Plex-standard name",
        "'English Title (Year) {tmdb-ID} Native title [RES]'; subtitles follow with a",
        "language code. You accept each film (Enter / e name / i id / s skip / q stop);",
        "PlexRename's 'Undo a rename...' reverses it. [2]: folder named after the file.",
        "Type or drag the folder in, or press Enter to pick it in a window.",
    ]),
    "sync":    ("Sync SRT to Video", [
        "Extracts audio, transcribes speech timing with faster-whisper, and computes",
        "the offset (or framerate drift) needed to align a subtitle to the video.",
        "Writes '<video>.synced.srt'. Optional DeepSeek content check catches",
        "wrong-episode subtitles before syncing.",
    ]),
    "match":   ("Match SRTs to Videos", [
        "Transcribes the first 5 minutes of every video in a folder (cached),",
        "asks DeepSeek which subtitle belongs to which video, and writes",
        "'SRT Match Report.txt'. Offers to rename mislabeled subtitles.",
    ]),
    "org":     ("Movie Folder Org (AI)", [
        "Translates a foreign title to English (DeepSeek), recovers the release year",
        "(filename or IMDb), and moves the file into an",
        "\"English Title (Year) Foreign Title\" folder.",
        "Always dry-runs first and asks for confirmation before applying.",
    ]),
}


def interactive_menu() -> None:
    while True:
        print("\n" + "=" * 60)
        print("  VIDEO UTILITIES")
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
            if field == "audit":
                tool_subtitle_audit()
            elif field == "nonengl":
                tool_non_english_scan()
            elif field == "move":
                tool_move_to_folders()
            elif field == "sync":
                tool_sync_srt()
            elif field == "match":
                tool_match_srt()
            elif field == "org":
                tool_movie_organizer()
        except KeyboardInterrupt:
            print("\n  Interrupted.")
            continue

        input("\n  Press Enter to continue...")


# ─── Main ─────────────────────────────────────────────────────────────────────

TOOLS = {"audit": "tool_subtitle_audit", "nonengl": "tool_non_english_scan", "move": "tool_move_to_folders",
         "sync": "tool_sync_srt", "match": "tool_match_srt", "org": "tool_movie_organizer"}


def main():
    # 'vdo_util.py move' = run just that tool (used by the Plex Utility window), no argument = the menu
    if len(sys.argv) > 1 and sys.argv[1] in TOOLS:
        field = sys.argv.pop(1)
        explain(*MENU_HELP[field])
        try:
            globals()[TOOLS[field]]()
        except KeyboardInterrupt:
            print("\n  Interrupted.")
        return
    interactive_menu()


if __name__ == "__main__":
    main()
