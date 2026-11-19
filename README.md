# Plex & Video Library Tools

Windows command-line tools for a Plex Media Server on the same Windows PC and the NAS video library.

Start here: **`Plex Utility.bat`**: double-click opens the **Plex Utility window** (`scripts\plex_utility_gui.py`):

- **Search the Plex library** (read-only, output in the window): type a title, file path, director, actor, genre,
  collection or item id, pick what it is, press Enter / Search. Buttons for Library stats, Recently added, List
  libraries, Duplicate files, Orphaned parts, Integrity check, **CSV export** and Open reports folder. ■ Stop ends a run.
- **Video files on the NAS**: each video tool (subtitle audit, non-English subtitles, move files to folders, SRT
  sync/match, Movie Folder Org) opens in its own console window (they ask questions as they go); **Fix Audio Location**
  opens its own window.
- **Tidy folder names** (`scripts\tidy_folder_names.py`, own window): dots in the names of the folders directly
  inside a chosen folder become spaces (`The.Last.Samurai.2003.1080p` → `The Last Samurai 2003 1080p`). Dots that
  belong in a name stay (`Mr.`, `Vol.`, `S.W.A.T.`, `L.A.`, `...`, `5.1`). 1 Preview → 2 Rename now (in place,
  confirmed, logged in `reports\tidy_folder_names_<time>.csv`) → **Undo a run…** puts the old names back. Skips a
  folder whose new name already exists, a folder in use, and @eaDir/#recycle. Files inside are not touched.
  **Afterwards, in Plex: scan the library, then Refresh All Metadata** (subtitles otherwise keep the old path and a
  film may not play). Locked posters (all set by the PlexRename tools are locked) are not changed by the refresh.
- **Plex database care**: Health & Repair (console), Database management (red; stop Plex first), and **Old text
  menu** (= `Plex Utility.bat menu`, everything as before).

| Tool group | Entry point | When to use |
|---|---|---|
| **DB Health & Repair** | `DBRepair\Windows\DBRepair-Windows-Launcher.bat` | Check the database, repair it, find missing files, empty Plex's trash |
| **DB Query Tools** | `plex.bat` | Search, stats, duplicates, delete parts |
| **Video Utilities** | `vdo.bat` | Subtitle audit, sub scan, SRT sync/match, simple organiser |
| **Movie Folder Org (AI)** | `movieorg.bat` | Translate + IMDb-year + rename loose files into folders |
| **CSV Export** | `export_report.bat` | All movies/episodes with resolution, size and path → `reports\` |
| **Fix Audio Location** | `fix_audio_location.bat` | Films that stall on the Shield ("network too slow"): find badly stored MP4s, make fixed copies in the background |

> Related, separate projects: `E:\PlexRename` (GUI renamer / artwork tools, `PlexRename.bat`) and
> `E:\Plex vdo2srt` (video → subtitles; moved out of this folder on 2 Oct 2026, renamed from `E:\vdo2srt` on 18 Oct).
> DB repair adapted from https://github.com/ChuckPa/DBRepair

---

## Plex on this PC: no copying, no mounting

Earlier versions ran Plex on another machine and these tools mapped its database share as `M:`
and worked on copies. **Plex now runs on this PC, and every tool opens the live database in place:**

```
E:\PlexData\Plex Media Server\Plug-in Support\Databases\com.plexapp.plugins.library.db
```

- **Read-only tools** (search, reports, check, missing files) open it with `mode=ro`. They cannot write,
  they are safe while Plex runs, and they see Plex's latest changes (WAL).
- **Write tools** (`plex.bat delete-part`, Repair) refuse to run while `Plex Media Server.exe` is running.
- **Emptying the trash** is done by Plex itself through its web API (`http://127.0.0.1:32400`, token from
  `Preferences.xml`). Nothing edits the database directly for that.

All locations live in **`scripts\plex_config.py`**. To override them, put a line in `.env` (or set an environment variable):

| Name | Default |
|---|---|
| `PLEX_DATA` | `E:\PlexData\Plex Media Server` |
| `PLEX_SQLITE` | `C:\Program Files\Plex\Plex Media Server\Plex SQLite.exe` |
| `PLEX_URL` | `http://127.0.0.1:32400` |
| `DEEPSEEK_API_KEY` | the key itself (optional) |
| `DEEPSEEK_KEY_FILE` | else a file with the key on line 1 |
| `NAS_HOSTS` | NAS name → IP, e.g. `diskstation=192.168.1.10;syno2=192.168.1.11` |
| `DRIVE_SHARES` | fallback drive → share, e.g. `U:=\\192.168.1.10\Share1;V:=\\192.168.1.11\Share2` |

Put your own values in `.env` (copy `.env.example`); it stays on your PC and is never uploaded.

> **Claude desktop app note:** programs started from inside the Claude app run in a sandbox. Run these
> `.bat` files from Explorer or a normal Command Prompt. Never start or stop Plex from inside Claude.

---

## Prerequisites

- **Python 3.12+** on PATH
- **Plex Media Server** installed (provides `Plex SQLite.exe`, used for the integrity check and repair)
- **ffmpeg / ffprobe** on PATH: needed for subtitle scanning and SRT sync
- **faster-whisper**: needed only for the SRT sync tool (`python -m pip install faster-whisper`)
- **Subtitle Edit**: optional; the SRT sync tool uses its CLI for non-SRT formats
- **DeepSeek API key**: optional; used by the SRT sync AI check and the Movie Folder Organizer (see table above)
- **requests**: needed by the Movie Folder Organizer (`movieorg.bat` installs it)

---

## Network Layout (example)

The tools assume a layout like this one - set your own NAS names, addresses and drives in `.env`.

```
this PC        Plex Media Server + database (E:\PlexData)
192.168.1.10   Synology DiskStation   — share Syno1      → U:   (Software → X:)
192.168.1.11   Synology Syno2         — share Syno1517   → V:  S:   (Pinku → T:)
```

Plex stores file paths in the form each library was added with: `\\192.168.1.1x\...`, `V:\...`,
`S:\...` or `\\?\V:\...`. `plex_config.canonical()` turns all of them into one form
(NAS address with IP). Searches match on the part after the share, so any form you type finds the file.

---

## Folder Structure

```
E:\Plex Utility\
  Plex Utility.bat  Opens the Plex Utility window; "Plex Utility.bat menu" = the old text menu
                    (DB Health / Query / Video Utilities / Movie Folder Org / CSV Export / Fix Audio Location)
  plex.bat                  DB query tool (plex_search.py read-only, plex_db.py write)
  export_report.bat         CSV export of all movies/episodes → reports\
  fix_audio_location.bat      Fix Audio Location (was mp4fix.bat): opens its window - scan → plan → fixed copies → swap
  vdo.bat                   Video utility menu
  movieorg.bat              Movie Folder Organizer (AI)
  plex_rename4scrape.bat    Rename-for-scraping GUI (creates its own venv\ on first run)
  .env                      API keys + optional overrides (never share this file)
  scripts\
    plex_config.py          Shared settings: DB location, Plex SQLite, NAS paths, Plex token
    plex_search.py          Read-only search & reports
    plex_db.py              Write operations (delete a stale part, clean up backups) — Plex stopped
    plex_health.py          check / missing / trash (used by the DB Health menu)
    export_report.py        CSV export
    fix_audio_timing.py     Fix Audio Location: interleave check (MP4 index only) + ffmpeg remux at idle priority
    fix_audio_timing_gui.py Fix Audio Location window (the main way in)
    schema_dump.py          Table/column listing
    vdo_util.py, sync_srt.py, chk4vdosrt.py, plex_rename4scrape.py
  movieorg\                 Movie Folder Organizer (AI) app
  DBRepair\                 Upstream DBRepair files + Windows\ launcher and repair batch
  reports\                  Output of check / missing / CSV export, fix_audio_timing_plan.xlsx + fix_audio_timing_log_*.csv
                            (older Fix Audio Location plans in reports\old\)
  _archive\                 Mac-era scripts and docs, one-off rename scripts (reference only)
  _to_delete\               Stale database copies, old reports, broken venv — empty when happy
```

---

## plex.bat — Plex Database Tool

Opens the live Plex database on this PC and sends each command to the right Python script.
Nothing is mounted or copied.

**Interactive mode** (no arguments) asks which branch you want:

```
plex.bat

  [1] Search & Reports      (read-only — safe while Plex is running)
  [2] Database Management   (write operations — STOP PLEX FIRST)
  [q] Quit
```

**CLI mode** sends the command straight through, with no prompt:

```
plex.bat search "Yojimbo"
plex.bat delete-part 310534
```

Use another database file (e.g. one of Plex's dated backups) with `--db` before the command:
`python scripts\plex_search.py --db "E:\PlexData\...\com.plexapp.plugins.library.db-2026-09-30" stats`

---

### [1] Search & Reports — `plex_search.py` (read-only)

**Interactive menu options:**

| # | Option | Description |
|---|---|---|
| 1 | Search by title | `LIKE '%…%'` scan across all metadata items |
| 2 | Search by file path | Match a NAS path fragment; any path form matches |
| 3 | Search by scraped metadata | Search genre, director, actor, writer, studio, etc. |
| 4 | Recent additions | Most recently added items |
| 5 | Full item details | Files, streams, tags for one metadata item by ID |
| 6 | List library sections | All Plex library sections with IDs and timestamps |
| 7 | Library statistics | Item counts per section and type, year range, rated count |
| 8 | Find duplicate file references | Files referenced by more than one metadata item |
| 9 | Find orphaned media parts | `media_parts` rows with no parent `media_item` |
| 10 | Database integrity check | Row counts + orphan relationship checks (skips FTS/spellfix) |
| 11 | Inspect DB tables & columns | Full schema listing |

**CLI commands:**

| Command | Description |
|---|---|
| `plex.bat search "title"` | Search by title |
| `plex.bat search --type movie "title"` | Filter by type: `movie`, `show`, `episode`, `artist`, `album`, `track` |
| `plex.bat search --year 1961 "Ronin"` | Filter by year |
| `plex.bat search --section "J.Samurai" "title"` | Filter by library section name |
| `plex.bat search --limit 100 "title"` | Override result limit (default 50) |
| `plex.bat find-file "path fragment"` | Find by file path |
| `plex.bat find-file "V:\Japanese\Samurai\47 Ronin"` | Full paths work in any form (`V:\`, `\\192.168.1.11\…`, `\\Syno2\…`) |
| `plex.bat tag "Kurosawa"` | Search all tag types for value |
| `plex.bat tag --type director "Kurosawa"` | Search specific tag type |
| `plex.bat recent` | 20 most recently added items |
| `plex.bat recent --limit 50 --type movie` | Recent with filters |
| `plex.bat info <metadata ID>` | Full details: title, year, summary, tags, files, streams |
| `plex.bat stats` | Library statistics |
| `plex.bat duplicates` | Files referenced by multiple metadata items |
| `plex.bat orphaned` | List orphaned media parts (use `delete-part` to remove) |
| `plex.bat check` | Row counts + relationship checks across all regular tables |
| `plex.bat list-sections` | All library sections |

---

### [2] Database Management — `plex_db.py` (write operations)

**Exit Plex Media Server first (tray icon → Exit).** The script checks this and stops if Plex is running.
A timestamped backup is written next to the database before any delete.

| Command | Description |
|---|---|
| `plex.bat delete-part <Part ID>` | Delete stale/orphaned media part by Part ID |
| `plex.bat cleanup-backups` | Remove old `.backup.*` files (keeps 3 most recent) |

**delete-part flow:** checks Plex is stopped → backup `com.plexapp.plugins.library.db.backup.YYYYMMDD_HHMMSS`
→ shows the part and whether the file still exists → dry-run row counts → typed `YES` → deletes child-first
(`media_streams`, `media_part_settings`, `download_queue_items`, `media_parts`, and `media_items` only if no other
parts remain) → rolls back on error. Afterwards: start Plex, scan the library, then `plex.bat cleanup-backups`.

For files that are simply gone from the NAS, prefer **DB Health → [3] Find missing files → [4] Empty Plex trash**:
Plex then removes the entries itself.

---

## DB Health & Repair — `DBRepair\Windows\DBRepair-Windows-Launcher.bat`

| # | Operation | What it does | Plex |
|---|---|---|---|
| 1 | Check database | `quick_check` + `integrity_check` on the library and blobs databases with Plex's own SQLite, read-only. Report → `reports\db_check_*.txt` | may run |
| 2 | Repair database | `DBRepair-Windows.bat` (ChuckPa's Windows script): export → rebuild → verify → reindex → swap in. Old databases kept in `Databases\dbtmp\`. Only when [1] says FAIL. | **must be stopped** (checked) |
| 3 | Find missing files | Every file Plex knows about is checked on disk (16 at a time). Optional folder filter. Unreachable NAS shares are skipped, not reported as missing. Report → `reports\missing_files_*.txt` | may run |
| 4 | Empty Plex trash | Lists items Plex marked deleted, per library; you choose libraries; Plex empties them via `PUT /library/sections/<id>/emptyTrash` | must run |

Direct: `python scripts\plex_health.py check | missing [--only "Japanese\Samurai"] | trash`

Plex's "Empty trash automatically after every scan" is deliberately **off** on this server (a NAS that
drops off the network would otherwise wipe items). Use [3] then [4] instead.

---

## vdo.bat — Video Utility Tools

Run `vdo.bat` for an interactive menu. No network share required — works against local or mapped NAS paths.

```
vdo.bat

  [1] Subtitle Audit          — classify folders: external / internal / no subs
  [2] Non-English Sub Scan    — find MKV files with non-English-only subtitles
  [3] Move Files to Folders   — move loose files into per-file subfolders
  [4] Sync SRT to Video       — auto-sync subtitle timing via Whisper
  [5] Match SRTs to Videos    — identify which subtitle belongs to which episode
```

### [1] Subtitle Audit

Recursively walks a folder tree and classifies each subfolder by subtitle coverage. Useful for identifying which titles still need subtitles added.

**Classification rules:**

| Result | Condition |
|---|---|
| **External Subs** | Folder name contains `sub`, OR folder contains `.srt` files |
| **Internal Subs** | A video file in the folder has an embedded English (`eng`) subtitle track |
| **No Subs** | No external or embedded English subtitles found |

Subfolders containing only external subs (e.g. a `Subs/` subdirectory) are promoted to their parent folder automatically.

**Output files** written to the scanned root folder:
- `External Subs.txt` — folders with external `.srt` files
- `Internal Subs.txt` — folders with embedded English subtitles
- `No Subs.txt` — folders with no subtitles at all

**Supported video formats:** `.mkv .mp4 .m4v .mov .ts .m2ts .mts .avi .divx .wmv .asf .webm .flv .mpg .mpeg .m2v .vob .ogv .ogg .3gp .3g2`

---

### [2] Non-English Subtitle Scan

Scans MKV files recursively and reports files that have **at least one non-English subtitle track** and **no English subtitle track**. Useful for finding files where English subs need to be added.

**Filtering rules:**

| Language field | Treatment |
|---|---|
| Missing / empty | Treated as English → file excluded |
| `unknown` | Skipped (insufficient data) |
| Starts with `en`, or is `eng` / `english` | English → file excluded |
| Anything else (e.g. `jpn`, `chi`, `spa`) | Non-English → file included if no English track found |

**Output:** `Non-English Subs.txt` written to the scanned root folder, containing scan summary and per-file subtitle language details.

---

### [3] Move Files to Folders

Moves each loose file directly inside a selected folder into its own subfolder named after the file (without extension, dots turned into spaces). Subtitles and other files whose name starts with a video's name (`Movie.en.srt`, `Movie-poster.jpg`) go into that video's folder. Only top-level files are moved — existing subfolders are untouched. This is the former stand-alone `mov2folder.py` (merged 1 Nov 2026).

**Example:**
```
Before:                          After:
  MyMovie.mkv            →         MyMovie/MyMovie.mkv
  Another.Film.mkv       →         Another Film/Another.Film.mkv
  Another.Film.en.srt    →         Another Film/Another.Film.en.srt
  Documentary.mp4        →         Documentary/Documentary.mp4
```

Type or drag in the folder, or press Enter to pick it in a window. Shows a preview of the moves before asking for confirmation; a file already in its target folder is skipped.

---

### [4] Sync SRT to Video — `sync_srt.py`

Auto-syncs an out-of-sync subtitle file to a video's audio track. Also runnable directly:

```
python scripts\sync_srt.py                             # prompts for video file
python scripts\sync_srt.py myvideo.mkv                 # subtitle found next to video
python scripts\sync_srt.py myvideo.mkv subs.en.srt     # explicit subtitle file
python scripts\sync_srt.py myvideo.mkv subs.srt --check   # AI match check only (~5 min of audio), no sync
python scripts\sync_srt.py myvideo.mkv --model base
```

**AI content check (DeepSeek):** when `DEEPSEEK_API_KEY` is set, the script first transcribes only the opening 5 minutes of audio and asks DeepSeek whether the subtitle content semantically matches the transcript (works across languages — e.g. English subs vs Russian audio). A wrong-episode or wrong-film subtitle is caught in ~1 minute instead of after a full transcription. Verdict `YES` also suppresses the heuristic mismatch prompts later; verdict `NO` asks for confirmation before continuing. Use `--check` to run only this stage (exit code 0 = match, 1 = uncertain/no key result, 2 = mismatch). Without an API key the stage is skipped and the old duration/gap heuristics still apply.

**Language-agnostic:** alignment correlates cue *timing* with detected speech timing — the subtitle text is never compared against the transcription. English (or any other language) subs sync correctly over a foreign soundtrack.

**Subtitle formats:** plain `.srt` is parsed directly; `.ass`/`.ssa`/`.vtt`/`.sub`/`.smi` and SRT files with unreadable encodings are converted on the fly via the Subtitle Edit CLI (`SubtitleEdit /convert ... SubRip /encoding:utf-8`).

**How it works:**

1. Finds the subtitle next to the video (same stem, any supported extension; prompts if several exist) — or takes it as the second argument
2. **AI content check** (if `DEEPSEEK_API_KEY` set): transcribes the first 5 minutes, asks DeepSeek whether the subtitle matches this video's content
3. Extracts mono 16 kHz audio with ffmpeg
4. Detects speech timing with **faster-whisper** (model `small` by default, language auto-detected, CUDA GPU if available)
5. Tries each known framerate ratio (1.0, 25↔23.976, 25↔24, 24↔23.976) as a candidate time scale, finds the best global offset under each, and keeps the best-scoring combination — this corrects both plain offsets and PAL/NTSC framerate drift
6. Polishes with a least-squares fit on cue↔speech pairs and writes `<video stem>.synced.srt` next to the video

Alignment is computed from cue *timing* vs detected speech timing (deterministic, sub-second precision) — DeepSeek's offset estimate is shown for reference but the final numbers come from the local cross-correlation.

**Mismatch detection** — before aligning, warns and asks to confirm if the SRT looks wrong for the video: very different duration, entries past the end of the video, or first cue more than 2 minutes from first detected speech (wrong episode / multi-episode SRT).

**Model choice** (`--model`): `base` is fastest, `small` (default) is a good balance, `medium` for difficult audio. Only speech *timing* matters for sync, so bigger models rarely help. First use of a model downloads it automatically.

---

### [5] Match SRTs to Videos — `sync_srt.py --match`

Answers "which subtitle file belongs to which episode?" for a whole folder in one command. Catches mislabeled subtitles that name- and duration-based checks cannot (e.g. an episode-9 SRT named as episode 5).

```
python scripts\sync_srt.py --match "V:\Series\Season 1"            # report only
python scripts\sync_srt.py --match "V:\Series\Season 1" --rename   # + interactive renames
```

**How it works:**

1. Transcribes the **first 5 minutes of every video** with faster-whisper — cached in `%LOCALAPPDATA%\sync_srt\transcripts` keyed on path/size/mtime/model, so reruns take seconds
2. For each subtitle file (all supported formats, non-SRT converted via Subtitle Edit), sends its opening lines plus a short transcript digest of every video to DeepSeek in **one API call per subtitle** and asks which video it belongs to (semantic, cross-language)
3. Prints and writes `SRT Match Report.txt` to the folder: matches with confidence and reasoning (mislabeled files flagged `⚠ MISLABELED`), unmatched subtitles, videos with no subtitles, and ready-to-paste sync commands for each matched pair
4. With `--rename`: proposes `<video stem><lang suffix>.srt` names for high-confidence mislabeled files, confirms each rename (`y`/`n`/`a`=all), never overwrites existing files

Requires `DEEPSEEK_API_KEY`. Typical cost: one 16-episode folder ≈ 3 minutes first run (GPU transcription), seconds thereafter, ~1 cent of API usage.

---

## fix_audio_location.bat — Fix Audio Location (films that stall: "network too slow")

**The problem (found 24 Oct 2026).** The Nvidia Shield sometimes says the network is too slow, even for films
of only 2 Mbit/s on a gigabit network. Some MP4 files store **all the picture first and the whole
soundtrack after it** (not "interleaved"). To play one, the player has to jump between two places in the
file all the time, about 7 small requests a second (seen in the Plex log). It never gets enough data per
second, so it stalls and blames the network. The network, the NAS and the Shield are all fine.

**The fix.** ffmpeg copies picture, sound and subtitles **unchanged** (`-c copy`, no quality loss) into a new
MP4 with both mixed together and the table of contents at the front (`-movflags +faststart`). Each copy is
checked afterwards: it must be interleaved, otherwise it is thrown away and the row is logged as failed.
**Originals are never changed.**

| Menu | What it does |
|---|---|
| [1] Scan | Read-only. Takes every MP4/MOV film and episode from the Plex database and reads only each file's index (a few KB). Writes `reports\fix_audio_timing_plan.xlsx`, about 15 min for ~4,900 files |
| [2] Open the plan | Review in Excel. **Y** = clear case (sound starts ≥ 20 % of the file away from the picture), already marked. **Yellow** = mild case (5–20 %): type Y only if the film stalls. **Red** = file can't be read |
| [3] Dry run | Lists what would be copied and estimates the time |
| [4] Fix in the background | Asks, then opens a **minimised window at lowest priority** (`start /low`; Python in Windows background mode = low disk priority; ffmpeg at idle priority). **Pauses while anyone is watching Plex** (checks `/status/sessions` every 30 s and freezes ffmpeg) and doesn't start a new film during playback. Close the window to stop; starting again skips the copies that are already done |
| [5] Open the newest log | `reports\fix_audio_timing_log_<date>.csv`: time, ok / error, seconds, source, new file |
| [6] Fix one film now | No plan needed. **Drag the film's file or folder into the window** and press Enter. It shows *NEEDS FIX* or *fine* for each MP4, and where the copy would go. Then **y** makes the fixed copy right away, in the same window (low priority, a few minutes per film), or **f** copies it even if it looks fine. Same target folder and log as [4] |

**Where the copies go:** `<library folder> (mp4 fixed)\<same subfolder>\<same file name>`, e.g.
`\\192.168.1.10\Syno1\War (China) (mp4 fixed)\The Volunteers ...\The Volunteers ....mp4`. The NewFile column
can be edited. Plex doesn't see these folders.

**Afterwards (by hand):** play a fixed copy. If it's good, move the original to a holding folder and put the
copy in its place under the same name. Plex keeps the film, its watch history and its artwork, because the
path stays the same. Delete the originals and the `(mp4 fixed)` folders only after checking.

**How long:** measured on 24 Oct, 1.7 GB took 8 min 13 s (≈ 12.5 GB per hour). First full scan: 209 clear
cases = 180 GB, so about **15–20 hours** of copying, plus any time paused for viewing. Run it overnight
over a few nights; each restart carries on where it stopped.

CLI: `python scripts\fix_audio_timing.py scan [--library NAME] [--limit N]`, `python scripts\fix_audio_timing.py fix [PLAN] [--go]`,
`python scripts\fix_audio_timing.py one FILE_OR_FOLDER [--to DIR] [--force] [--go]` (one film; `--to` = other folder for the copy).
A film outside every Plex library folder gets its copy in `<its folder> (mp4 fixed)\`, never beside the original.
Needs ffmpeg (`winget install Gyan.FFmpeg.Essentials`, installed) and `openpyxl`.

---

## SQLite Connection Strategy

Plex uses a customised SQLite build with extensions that standard Python `sqlite3` does not support:

| Extension | Purpose | How these tools handle it |
|---|---|---|
| ICU collation (`icu_root`) | Unicode-aware sorting | Python: dummy collation registered (read-only use only) |
| FTS4 `collating` tokenizer | Full-text search indexes | Python never touches FTS tables; integrity check/repair use `Plex SQLite.exe` |
| spellfix1 | Fuzzy search | Skipped by `plex.bat check`; covered by `plex_health.py check` |

Python reads use `mode=ro` on the live file (not `immutable=1`, which would ignore Plex's WAL and could read
half-written pages while Plex runs). The integrity check and the repair run `Plex SQLite.exe`, which has all of
Plex's extensions built in, so there are no "unknown tokenizer" warnings and no home-built sqlite3/ICU.

---

## Search Tips

| Situation | What to do |
|---|---|
| Title is in Japanese / Chinese / Cyrillic | Use `find-file` with part of the folder name |
| You have a full path (`V:\...`, `\\192.168.1.11\...`) | Paste as-is; it matches whichever form Plex stored |
| Plex app can't find a CJK title | Use `plex.bat search`, which bypasses Plex's FTS index and matches any substring |
| File exists on NAS but not found | Plex hasn't scanned it yet; scan the library in Plex |
| Search returns too many results | Add `--type`, `--year`, or `--section` filters |
| Want to see streams and subtitles | Use `plex.bat info <ID>` with the ID shown in any other search result |
| Files gone from the NAS | DB Health → [3] Find missing files, then [4] Empty Plex trash |
| Database seems corrupt | DB Health → [1] Check; if FAIL, exit Plex and run [2] Repair |

---

## Script Function Reference

### `scripts/plex_config.py`

| Name | Description |
|---|---|
| `LIBRARY_DB`, `BLOBS_DB`, `PLEX_SQLITE`, `PLEX_URL`, `PROJECT_ROOT` | Locations (overridable via `.env` / environment) |
| `env_value(name, default)` | Environment variable, else `NAME=value` from `.env` |
| `connect_ro(db_path=None)` | Read-only connection to the live DB with dummy ICU collations, `sqlite3.Row` rows |
| `plex_sqlite(db, sql, readonly=True)` | Runs SQL with `Plex SQLite.exe`, returns its output |
| `plex_running()` | True while `Plex Media Server.exe` runs |
| `drive_share(drive)` / `ip_form(path)` | `V:` → `\\192.168.1.11\Syno1517`; NAS names → IPs |
| `canonical(path)` | One comparable form for `V:\…`, `\\?\V:\…`, `\\Syno2\…` paths |
| `share_relative(path)` | Path after the NAS share (`\Japanese\Samurai\…`), used for matching |
| `plex_token()` / `plex_request(method, path, **params)` | Plex web API with the server token (never printed) |
| `deepseek_key()` | DeepSeek key from `DEEPSEEK_API_KEY`, else the file `DEEPSEEK_KEY_FILE` names |

---

### `scripts/plex_health.py`

| Function | Description |
|---|---|
| `cmd_check` | Size/pages + `quick_check(1)` + `integrity_check(1)` on both DBs via Plex SQLite; report file |
| `cmd_missing` | All `media_parts` (optional `--only` filter) checked with `os.path.exists` in 16 threads; offline shares skipped; grouped report |
| `cmd_trash` | Per-library count of `deleted_at` items; asks; `PUT /library/sections/<id>/emptyTrash` |

---

### `scripts/plex_search.py`

| Function | Signature | Description |
|---|---|---|
| `normalize_path_query` | `(q: str) → str` | Reduces a typed path to the part after the NAS share (`plex_config.share_relative`) so any stored form matches |
| `list_db_files` | `(share_path: str) → None` | Lists `.db` files in a directory — used for diagnostics when schema check fails |
| `inspect_schema` | `(conn) → list` | Returns list of all table names in the connected database |
| `verify_schema` | `(conn) → bool` | Confirms the four required Plex tables exist; prints missing tables if not |
| `connect_to_db` | `(db_path: str) → Connection` | Read-only `mode=ro` connection via `plex_config.connect_ro` |
| `fmt_duration` | `(ms: int\|None) → str` | Formats millisecond duration as `Xh Ym Zs` |
| `fmt_size` | `(size_bytes: int\|None) → str` | Formats byte count as human-readable `KB / MB / GB` |
| `fmt_timestamp` | `(ts: int\|None) → str` | Formats Unix timestamp as `YYYY-MM-DD HH:MM:SS UTC` |
| `fmt_type_name` | `(metadata_type: int) → str` | Maps Plex metadata type integer to name (`movie`, `show`, `episode`, …) |
| `fmt_tag_type` | `(tag_type: int) → str` | Maps Plex tag type integer to name (`genre`, `director`, `actor`, …) |
| `fmt_stream_type` | `(stream_type: int) → str` | Maps stream type integer to name (`video`, `audio`, `subtitle`, …) |
| `truncate` | `(text: str\|None, max_len: int) → str` | Truncates a string and appends `...` if over `max_len` characters |
| `resolve_media_type` | `(name: str) → int` | Converts type name string to Plex integer; exits with error if unknown |
| `fetch_file_paths` | `(conn, metadata_ids: list) → dict` | Returns `{metadata_item_id: [file, …]}` for a list of metadata IDs |
| `fmt_path` | `(path: str, max_len: int) → str` | Truncates a file path from the left, preserving the tail |
| `print_media_table` | `(rows, paths: dict) → None` | Renders a formatted table of metadata items with file paths below each row |
| `prompt` | `(label: str, default: str) → str` | Console input with a default value shown in brackets |
| `prompt_int` | `(label: str, default: int) → int` | Console integer input with default; returns default on non-numeric entry |
| `cmd_list_sections` | `(conn) → None` | Prints all library sections with ID, name, type, and timestamps |
| `cmd_search` | `(conn, args) → None` | Title `LIKE` search with optional `--type`, `--year`, `--section`, `--limit` filters |
| `cmd_search_tag` | `(conn, tag_value, tag_type_name, limit) → None` | Searches `tags` table via `taggings` join; optional tag type filter |
| `cmd_recent` | `(conn, args) → None` | Lists most recently added items ordered by `added_at DESC` |
| `cmd_info` | `(conn, item_id) → None` | Full details for one metadata item: title, year, summary, tags, file parts, streams |
| `cmd_find_file` | `(conn, query, limit) → None` | Searches `media_parts.file` with path translation applied before the query |
| `cmd_stats` | `(conn) → None` | Counts items per section and type; shows year range and rated count |
| `cmd_check` | `(conn) → None` | Row counts for all regular tables + five orphan relationship checks; skips FTS/spellfix tables |
| `cmd_duplicates` | `(conn) → None` | Finds `media_parts.file` values referenced by more than one `metadata_item` |
| `cmd_find_orphaned` | `(conn) → None` | Lists `media_parts` rows that have no parent `media_item` (read-only; use `delete-part` to remove) |
| `interactive_mode` | `(conn, db_path: str) → None` | 11-option interactive menu covering all search and reporting commands |
| `parse_args` | `() → Namespace` | Defines argparse subcommands: `search`, `tag`, `recent`, `info`, `find-file`, `stats`, `duplicates`, `check`, `orphaned`, `list-sections` |
| `main` | `() → None` | Entry point: opens read-only connection, verifies schema, dispatches CLI command or interactive mode |

---

### `scripts/plex_db.py`

| Function | Signature | Description |
|---|---|---|
| `connect_to_db` | `(db_path: str) → Connection\|None` | Opens a standard writable connection; registers dummy ICU collations; returns `None` on error |
| `create_backup` | `(db_path: str) → str\|None` | Copies the DB to a timestamped `.backup.YYYYMMDD_HHMMSS` file before any write |
| `cmd_cleanup_backups` | `(db_dir: str, keep_count: int = 3) → None` | Removes old `.backup.*` files from `db_dir`, keeping the `keep_count` most recent |
| `fmt_size` | `(size_bytes: int\|None) → str` | Formats byte count as human-readable `KB / MB / GB` |
| `cmd_delete_part` | `(db_path: str, part_id: int) → None` | Full delete flow: backup → verify part → dry-run counts → `YES` confirmation → delete child-first → commit or rollback |
| `interactive_menu` | `(db_path: str) → None` | 2-option menu: [1] delete stale part by ID, [2] cleanup old backups |
| `parse_args` | `() → Namespace` | Defines argparse subcommands: `delete-part <id>`, `cleanup-backups`; `--interactive` flag |
| `main` | `() → None` | Entry point: validates DB path (default: live DB), then routes to interactive menu or specific command; `cmd_delete_part` aborts while Plex runs |

---

### `scripts/vdo_util.py`

| Function | Signature | Description |
|---|---|---|
| `prompt_folder` | `(label: str) → str` | Prompts until the user enters a valid existing directory path; strips surrounding quotes |
| `get_subtitle_streams` | `(video_file: str) → list` | Runs `ffprobe` on a video file and returns its subtitle stream list; returns `[]` on any failure |
| `tool_subtitle_audit` | `() → None` | Recursively classifies subfolders as External / Internal / No Subs; promotes parent folders with a `Subs/` child; writes three `.txt` report files |
| `tool_non_english_scan` | `() → None` | Scans MKV files and reports those with at least one non-English subtitle track and no English track; writes `Non-English Subs.txt` |
| `tool_move_to_folders` | `() → None` | Moves each top-level loose file in a folder into a subfolder named after the file (without extension); shows a 10-item preview before confirming |
| `tool_sync_srt` | `() → None` | Launches `sync_srt.py` in a subprocess (kept separate so it can also be run directly) |
| `tool_match_srt` | `() → None` | Prompts for a folder and launches `sync_srt.py --match <folder> --rename` |
| `tool_movie_organizer` | `() → None` | Launches `movieorg/movie_folder_organizer.py` in a subprocess (also reachable directly via `movieorg.bat`) |
| `interactive_menu` | `() → None` | 6-option menu: Subtitle Audit, Non-English Sub Scan, Move Files to Folders, Sync SRT, Match SRTs, Movie Folder Org |
| `main` | `() → None` | Entry point: calls `interactive_menu()` |

---

### `scripts/sync_srt.py`

| Function | Signature | Description |
|---|---|---|
| `parse_timecode` | `(tc: str) → float` | Converts SRT timecode `HH:MM:SS,mmm` to float seconds |
| `format_timecode` | `(seconds: float) → str` | Converts float seconds to SRT timecode; clamps negatives to zero |
| `read_srt_text` | `(path: Path) → str` | Reads an SRT file trying `utf-8-sig`, `utf-8`, `cp1251`, `latin-1` in order |
| `parse_srt` | `(path: Path) → list[Cue]` | Parses SRT blocks into `Cue(start, end, text)` dataclasses; skips malformed blocks |
| `write_srt` | `(cues, path) → None` | Writes cues as UTF-8 SRT, renumbering from 1 |
| `find_subtitle_edit` | `() → str\|None` | Locates `SubtitleEdit.exe` in standard install paths |
| `convert_to_srt` | `(sub_path, out_dir) → Path` | Converts any subtitle format/encoding to UTF-8 SRT via the Subtitle Edit CLI |
| `load_cues` | `(sub_path, tmpdir) → list[Cue]` | Parses subtitles; non-SRT formats or undecodable SRTs go through Subtitle Edit conversion |
| `extract_audio` | `(video_path, out_wav, duration=None) → None` | ffmpeg extraction of mono 16 kHz WAV; optional duration limit for the AI-check chunk |
| `get_video_duration` | `(video_path) → float` | ffprobe duration in seconds, `0.0` if unavailable |
| `resolve_device` | `() → str` | `cuda` if the CTranslate2 backend supports float16 on GPU, else `cpu` |
| `load_whisper_model` | `(model_name) → WhisperModel` | Loads faster-whisper once; reused for the AI-check chunk and the full run |
| `whisper_transcribe` | `(model, wav_path, duration) → list[dict]` | faster-whisper speech detection with VAD filter; prints inline progress; returns `{start, end, text}` segments |
| `get_deepseek_key` | `() → str\|None` | `DEEPSEEK_API_KEY` from the environment, else from the project's `.env` |
| `timestamped_lines` | `(items, max_lines) → str` | Renders (seconds, text) pairs as `[mmm:ss] text` lines for the LLM prompt |
| `deepseek_chat` | `(api_key, system, user) → dict\|None` | One JSON-mode DeepSeek call (urllib, 120 s timeout); shared by verify and match; `None` on any failure |
| `deepseek_verify` | `(api_key, video_name, language, cues, chunk_segments) → dict\|None` | Asks DeepSeek if the subtitles semantically match the transcribed chunk; returns `{match, estimated_offset_seconds, reason}`; `None` on API failure (pipeline continues) |
| `find_media` | `(folder) → (videos, subs)` | Videos (extensions from `vdo_util.VIDEO_EXTENSIONS`) and subtitles in a folder; `.synced.srt` outputs excluded |
| `transcript_cache_path` | `(video_path, model_name) → Path` | Cache file in `%LOCALAPPDATA%\sync_srt\transcripts`, keyed on path/size/mtime/model/chunk length |
| `get_chunk_transcript` | `(get_model, video_path, model_name, tmpdir) → dict` | First-5-minutes transcript, served from cache; Whisper model loaded lazily only on cache miss |
| `split_lang_suffix` | `(stem) → (base, suffix)` | Splits a trailing language suffix: `'Ep 5.en'` → `('Ep 5', '.en')` |
| `deepseek_match` | `(api_key, sub_name, sub_lines, video_digests) → dict\|None` | Asks DeepSeek which video a subtitle belongs to; batches >25 videos per call; rejects hallucinated filenames |
| `build_match_report` | `(folder, videos, results) → str` | Report text: matches (with `⚠ MISLABELED` flags), unmatched subs, videos without subs, suggested sync commands |
| `apply_renames` | `(candidates) → None` | Interactive rename of mislabeled subtitles (`y`/`n`/`a`); skips existing targets |
| `run_match` | `(args) → None` | `--match` orchestrator: discover → cached chunk transcripts → DeepSeek matching → report → optional renames |
| `nearest_value` | `(sorted_times, t) → float` | Nearest neighbour lookup via `bisect` |
| `find_global_offset` | `(s_starts, w_starts) → (float, float)` | Cross-correlation offset search in 0.5 s steps over ±600 s; returns best offset and its average error |
| `fit_linear_drift` | `(pairs) → (float, float)` | Least-squares fit `w = a·s + b` over matched (SRT, speech) time pairs |
| `align_cues` | `(cues, whisper_segments) → (list[Cue], str)` | Full alignment: framerate-ratio candidates × offset search, then least-squares polish; returns adjusted cues and a diagnosis line |
| `diagnose_mismatch` | `(cues, whisper_segments, video_duration) → str\|None` | Human-readable warnings when the SRT looks wrong for the video, else `None` |
| `resolve_video_path` | `(arg: str\|None) → Path` | Video path from CLI argument or interactive prompt; exits if missing |
| `find_subtitles_for` | `(video_path) → Path` | Same-stem subtitle (any supported extension), else the single subtitle in the folder, else numbered picker |
| `parse_args` | `() → Namespace` | argparse: optional `video` and `srt` positionals, `--model` (default `small`), `--check` (AI check only), `--match FOLDER`, `--rename` |
| `main` | `() → None` | Entry point: `--match` → `run_match`; otherwise parse → AI content check → extract → transcribe → diagnose → align → write `.synced.srt` |

---

### `scripts/fix_audio_timing.py`

| Function | Description |
|---|---|
| `inspect(path)` | Reads only the MP4 index (moov box): where the first video and audio chunks are → `ok` / `bad` (gap > 5 %) / `fragmented` / `one track` / `unreadable`, plus index at start/end |
| `plex_files(library)` | MP4/MOV parts of film and TV libraries from the live DB (read-only) + each library's folders |
| `row_for` / `write_plan` | One plan row per problem file; Excel plan with colours; previous plan moved to `reports\old\` |
| `remux(exe, src, dst)` | ffmpeg `-map 0 -c copy -movflags +faststart` into `<name>.part.mp4`, verified with `inspect`, then renamed |
| `cmd_one` / `library_roots()` | Single film (file or folder): verdict per MP4, target via `new_name`, `--go` remuxes at once |
| `low_priority()` / `run_polite(cmd)` | Idle CPU + background I/O for itself; ffmpeg at idle priority, suspended (`NtSuspendProcess`) while `/status/sessions` shows playback |

---

## Known Limitations

- **CJK title search in Plex app** — Plex's FTS4 ICU tokenizer cannot tokenize CJK characters written without spaces. Our `LIKE '%…%'` scan bypasses this entirely.
- **WAL timing**: read-only tools see everything Plex has committed; a scan still in progress shows up when Plex commits it.
- **ffprobe required** — subtitle scanning tools (`vdo.bat` options 1 and 2) return empty stream lists if ffprobe is not on PATH; no error is raised.
- **Move Files to Folders: top-level only** — the Move Files to Folders tool only processes files directly inside the selected folder, not files in subdirectories.
