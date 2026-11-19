# Movie Folder Organizer

## Plex-standard names (default since 16 Nov 2026)

The organizer now names films the way **PlexRename** (`E:\PlexRename`) does, using its own code
(`plexname.py`, loaded by `plex_names.py`; folder set in `config.json` → `plexrename_folder`):

```
U:\war (russia)\Tureckiy_gambit.avi + Tureckiy_gambit.srt
  -> U:\war (russia)\The Turkish Gambit (2005) {tmdb-53129} Турецкий гамбит [SD]\
         The Turkish Gambit (2005) {tmdb-53129} Турецкий гамбит [SD].avi
         The Turkish Gambit (2005) {tmdb-53129} Турецкий гамбит [SD].ru.srt
```

- **Who the film is:** Plex's own match of the file → Plex's IMDb id on TMDB → TMDB search limited to
  the language you choose at the start (JP/KR/CN/RU/EN; Cyrillic/kana/hangul letters in a file name win) →
  DeepSeek choosing **only among the TMDB results**. Title, year and native title come live from TMDB;
  the resolution is measured (Plex, else MediaInfo).
- Parts (`CD1`/`CD2`, `Part 1/2`) stay together as ` - pt1`, ` - pt2`; loose subtitles whose name starts
  with the video's name follow it with a language code. Paths are kept ≤ 240 characters by shortening
  the native title only.
- **One film at a time:** the proposal is shown and nothing moves until you answer:
  `Enter` accept · `e` type another folder name (files follow it) · `i` type a TMDB id/link or IMDb
  tt-id/link · `s` skip · `q` stop.
- Not found on TMDB: the old-style name (AI translation, year from the file name, no id, file name kept)
  is proposed, marked **NOT IDENTIFIED** - press `i` if you know the id.
- Every accepted film is logged as `E:\PlexRename\logs\manual_rename_*.csv`; PlexRename's
  **Undo a rename…** reverses it (files back, new folder removed). Afterwards scan the library in Plex.
- `--preview` shows every proposal and moves nothing; `--old-style` runs the previous behaviour
  described below (IMDb year lookup, file names kept, no id).

---

An agentic tool that scans a foreign-language movie directory, finds video files that
are **not** already inside their own folder, translates the title to English using the
DeepSeek AI, looks up any **missing release year via IMDb** (falling back to the AI),
and moves each file into a correctly named folder.

The resulting folder name follows this format:

```
English Title (Year) Original Foreign Title
```

### Example

Source file:

```
U:\war (russia)\Двадцать дней без войны.1976.mkv
```

Result:

```
U:\war (russia)\Twenty Days Without War (1976) Двадцать дней без войны\Двадцать дней без войны.1976.mkv
```

A file with **no year in its name** is resolved automatically:

```
U:\war (russia)\Tureckiy_gambit.avi   ->   The Turkish Gambit (2005) Tureckiy gambit
```

---

## Features

- **Detects loose video files** in any format (`.avi`, `.mkv`, `.mp4`, `.mov`, `.flv`,
  `.wmv`, `.m4v`, `.ts`, `.mts`, `.m2ts`, `.webm`, `.mpg`, `.mpeg`, `.vob`, `.3gp`, …).
  Only files sitting **directly** in the target directory are touched.
- **Parses the year** out of the filename (several release-name patterns supported).
- **Translates** the foreign title to the official English title with DeepSeek
  (`deepseek-chat`) and auto-detects the source language (Russian, Belarusian, …).
- **IMDb year scraper** — when a year is missing from the filename, the tool queries
  IMDb's public suggestion API (no API key) and picks the best title match to recover
  the release year. The DeepSeek model's own year knowledge is used as a final fallback.
- **Duplicate folders are merged** — if a folder with the computed name already exists,
  the file is moved *inside* that existing folder instead of creating a copy.
  Colliding filenames get a `(1)`, `(2)` suffix.
- **Dry-run first** — always previews the plan and asks for confirmation before moving.
- **Caching** — every translation is cached to `translations_cache.json` so re-runs are
  cheap and offline-friendly.
- **Logging** — to both the console and `logs/movie_folder_organizer.log`.

---

## Setup

### 1. Install Python packages

```powershell
cd E:\Plex\movieorg
pip install -r requirements.txt
```

Only `requests` is required.

### 2. API key

The tool reads the DeepSeek API key from the file referenced in [`config.json`](config.json):

```json
{
  "api_key_path": "C:\\keys\\deepseek.txt"
}
```

You can instead set the environment variable `DEEPSEEK_API_KEY`. IMDb scraping needs
no key.

---

## Usage

### Interactive (prompts you for the folder, then confirms)

```powershell
run.bat
```

### Direct, with explicit folder

```powershell
python movie_folder_organizer.py "U:\war (russia)"
```

This does a **dry-run** first, prints the full plan (English title, year + its source,
language, target folder), and only moves files after you type `yes`. Add `--apply` to
skip the confirmation.

### Useful options

| Flag                     | Description                                                        |
| ------------------------ | ----------------------------------------------------------------- |
| `--apply`                | Skip the confirmation and move immediately.                       |
| `--extensions .mkv .avi` | Override the list of video extensions to scan.                    |
| `--config config.json`   | Use a different config file.                                      |
| `--no-cache`             | Ignore the translation cache (force re-translation).              |
| `--verbose`              | Verbose logging.                                                  |

### config.json options

| Key               | Default | Purpose                                              |
| ----------------- | ------- | --------------------------------------------------- |
| `enable_imdb`     | `true`  | Toggle the IMDb year scraper.                        |
| `imdb_min_score`  | `0.60`  | Minimum title-similarity score to accept a match.   |

---

## How the year is chosen (priority)

1. **Year in the filename** — authoritative (e.g. `.1976`).
2. **IMDb scraper** — when missing, query IMDb by the English then the foreign title.
3. **AI suggestion** — DeepSeek's own release-year knowledge as a final fallback.

The dry-run report shows the source for every year, e.g. `(2005) (imdb)`.

---

## Files

| File                          | Purpose                                    |
| ----------------------------- | ------------------------------------------ |
| `movie_folder_organizer.py`   | Main script.                               |
| `config.json`                 | API key path + IMDb/scanner settings.      |
| `requirements.txt`            | Python dependencies.                       |
| `translations_cache.json`     | Auto-generated translation cache.          |
| `logs/movie_folder_organizer.log` | Auto-generated log file.               |
| `run.bat`                     | Windows launcher.                          |
