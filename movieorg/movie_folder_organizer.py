#!/usr/bin/env python3
"""
Movie Folder Organizer
=======================

Scans a directory for video files that are not already inside their own folder,
translates the (foreign-language) movie title to English using the DeepSeek AI,
looks up any missing release year via the IMDb scraper (falling back to the AI),
and moves each file into a folder named:

    English Title (Year) Original Foreign Title

Usage
-----
    python movie_folder_organizer.py "U:\\war (russia)"               # dry run
    python movie_folder_organizer.py "U:\\war (russia)" --apply       # apply changes
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Set

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "config.json"
CACHE_PATH = SCRIPT_DIR / "translations_cache.json"
LOG_DIR = SCRIPT_DIR / "logs"
LOG_PATH = LOG_DIR / "movie_organizer.log"

# Characters that are not allowed in Windows folder names.
INVALID_WIN_CHARS = '<>:"/\\|?*'
MAX_PATH_SEG = 200  # safety cap for folder-name length


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool = False) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("movie_folder_organizer")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.addHandler(ch)

    return logger


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MovieFile:
    path: Path
    name_no_ext: str          # original filename without extension
    extension: str            # ".mkv", ".avi", ...
    year: Optional[str] = None
    foreign_title: str = ""   # the cleaned, original-language title
    english_title: str = ""   # translated title
    language: str = ""        # detected source language, e.g. "Russian"
    year_source: str = ""     # filename | imdb | ai | none
    imdb_id: str = ""         # tt0123456 when found via IMDb
    target_folder: str = ""   # final folder name
    status: str = "pending"   # pending | skipped | moved | error
    note: str = ""
    merged: bool = False      # True if moved into an already-existing folder


@dataclass
class Config:
    api_key_path: str = ""   # file with the DeepSeek key on line 1 (config.json), or DEEPSEEK_API_KEY
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    default_language: str = "auto"
    video_extensions: List[str] = field(default_factory=list)
    enable_imdb: bool = True          # scrape IMDb for missing release years
    imdb_min_score: float = 0.60      # similarity threshold for accepting a match
    plexrename_folder: str = r"E:\PlexRename"   # PlexRename's naming (TMDB id, native title, resolution)


# ---------------------------------------------------------------------------
# Config + API key
# ---------------------------------------------------------------------------

def load_config(path: Path) -> Config:
    cfg = Config()
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    if not cfg.video_extensions:
        cfg.video_extensions = [
            ".mp4", ".avi", ".mkv", ".mov", ".flv", ".wmv", ".m4v",
            ".ts", ".mts", ".m2ts", ".webm", ".mpg", ".mpeg", ".vob",
            ".3gp", ".ogv", ".rm", ".rmvb", ".divx", ".asf",
        ]
    return cfg


def read_api_key(cfg: Config, logger: logging.Logger) -> Optional[str]:
    # 1. environment variable
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        logger.debug("Using DEEPSEEK_API_KEY from environment.")
        return key.strip()

    # 2. file from config
    key_path = Path(cfg.api_key_path)
    if key_path.exists():
        try:
            return key_path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            logger.error("Could not read API key file %s: %s", key_path, exc)
    else:
        logger.warning("API key file not found: %s", key_path)

    return None


# ---------------------------------------------------------------------------
# Translation cache
# ---------------------------------------------------------------------------

def load_cache() -> Dict[str, dict]:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cache(cache: Dict[str, dict]) -> None:
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2),
                          encoding="utf-8")


# ---------------------------------------------------------------------------
# Directory scanning
# ---------------------------------------------------------------------------

def scan_video_files(directory: Path,
                     extensions: Set[str],
                     logger: logging.Logger) -> List[MovieFile]:
    """Return video files that sit *directly* inside `directory`."""
    found: List[MovieFile] = []
    if not directory.is_dir():
        logger.error("Directory does not exist: %s", directory)
        return found

    for entry in directory.iterdir():
        # Skip directories entirely - we only care about loose files.
        if entry.is_dir():
            continue
        if entry.suffix.lower() in extensions:
            found.append(MovieFile(
                path=entry,
                name_no_ext=entry.stem,
                extension=entry.suffix.lower(),
            ))
    logger.info("Found %d loose video file(s) in %s", len(found), directory)
    return found


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

# Matches a 4-digit year, optionally wrapped in parentheses, often surrounded
# by separators like '.', '_', '-', '[', ']', ' '.
YEAR_PATTERNS = [
    re.compile(r"[\(\[]?(19\d{2}|20\d{2})[\)\]]?"),     # (2024) / [2024] / 2024
]

# Tokens that are common release / rip tags we want to strip from the title.
RELEASE_TAGS = re.compile(
    r"(?i)\b("
    r"web-?dl(?:rip)?|hdrip|bdrip|brrip|dvdrip|satrip|tvrip|camrip|"
    r"ts|tc|scr|r5|hdrip|remux|bluray|blu-ray|1080p|720p|480p|2160p|"
    r"4k|uhd|x264|x265|h264|h265|hevc|aac|ac3|mp3|dts|ddp|atmos|"
    r"megapeer|exkinoray|fenixclub|nicodem|files-x|rus|eng|sub|"
    r"amzn|nf|disney|hmax|hulu|atvp|pcok|stz|crchd|mkv|avi|mp4"
    r")\b"
)

SIZE_OR_HINT = re.compile(r"(?i)\b\d+(\.\d+)?\s*(gb|mb|tb|gб|мб|б)\b")


def extract_year(name: str) -> Optional[str]:
    for pat in YEAR_PATTERNS:
        m = pat.search(name)
        if m:
            return m.group(1)
    return None


def clean_foreign_title(name: str, year: Optional[str]) -> str:
    """Strip extension-like release tags, the year token and size hints."""
    title = name

    # Remove anything from the year token onward (the year and everything
    # after it is usually technical metadata).
    if year:
        # match the year possibly wrapped in brackets/parens
        yr_pat = re.compile(r"[\(\[]?" + re.escape(year) + r"[\)\]]?")
        m = yr_pat.search(title)
        if m:
            title = title[:m.start()]

    # Replace separators with spaces and strip release tags.
    title = title.replace(".", " ").replace("_", " ").replace("-", " ")
    title = RELEASE_TAGS.sub(" ", title)
    title = SIZE_OR_HINT.sub(" ", title)

    # Collapse repeated whitespace.
    title = re.sub(r"\s+", " ", title).strip(" ._-")
    return title


# ---------------------------------------------------------------------------
# DeepSeek translation
# ---------------------------------------------------------------------------

def _strip_api_key(s: str) -> str:
    return (s[:4] + "..." + s[-4:]) if s and len(s) > 10 else "***"


class DeepSeekTranslator:
    def __init__(self, cfg: Config, api_key: str, logger: logging.Logger):
        self.cfg = cfg
        self.api_key = api_key
        self.logger = logger
        self.base_url = cfg.deepseek_base_url.rstrip("/")
        self.model = cfg.deepseek_model
        if requests is None:
            raise RuntimeError(
                "The 'requests' package is required. Run: pip install requests"
            )

    def _chat(self, system: str, user: str, retries: int = 3) -> Optional[str]:
        url = f"{self.base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
            "max_tokens": 256,
            "response_format": {"type": "json_object"},
        }
        for attempt in range(1, retries + 1):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=60)
                if resp.status_code == 429:
                    wait = 5 * attempt
                    self.logger.warning(
                        "Rate limited (429). Waiting %ss before retry %d/%d",
                        wait, attempt, retries,
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except Exception as exc:
                self.logger.warning(
                    "DeepSeek API call failed (attempt %d/%d): %s",
                    attempt, retries, exc,
                )
                if attempt < retries:
                    time.sleep(3 * attempt)
        return None

    def translate_title(self, foreign_title: str,
                        hint_lang: str = "auto") -> Optional[dict]:
        """Return dict with keys: english_title, language, suggested_year."""
        if not foreign_title.strip():
            return None

        system_msg = (
            "You are a movie database expert. The user gives you a movie title "
            "in a foreign language (often transliterated Latin or Cyrillic). "
            "Translate it to the OFFICIAL English movie title and identify the "
            "source language. Also provide the original cinematic release YEAR "
            "as a 4-digit string if you are confident; use null otherwise. "
            "Reply ONLY with a JSON object: "
            '{"english_title": "...", "language": "...", "suggested_year": '
            '"YYYY" or null} where language is the full English name of the '
            "source language (e.g. 'Russian'). If the title is already "
            "English, return it unchanged."
        )
        user_msg = f'Movie title: "{foreign_title}"'

        content = self._chat(system_msg, user_msg)
        if not content:
            return None
        try:
            parsed = json.loads(content)
            year = parsed.get("suggested_year")
            if year is not None:
                year = str(year).strip()[:4] if str(year).strip() else None
                if year and not year.isdigit():
                    year = None
            return {
                "english_title": str(parsed.get("english_title", "")).strip(),
                "language": str(parsed.get("language", "")).strip(),
                "suggested_year": year,
            }
        except json.JSONDecodeError:
            # Fallback: treat the raw response as the title.
            return {"english_title": content.strip(), "language": ""}


# ---------------------------------------------------------------------------
# IMDb media scraper  (fills in missing release years, no API key needed)
# ---------------------------------------------------------------------------

class ImdbScraper:
    """Look up a movie's release year via IMDb's public suggestion API.

    The endpoint used is the same one the IMDb search box calls; it returns
    JSON without authentication::

        https://v3.sg.media-imdb.com/suggestion/{first_letter}/{query}.json
    """

    SUGGEST_URL = "https://v3.sg.media-imdb.com/suggestion/{letter}/{query}.json"
    HEADERS = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self, logger: logging.Logger,
                 min_score: float = 0.60, timeout: int = 15):
        self.logger = logger
        self.min_score = min_score
        self.timeout = timeout
        if requests is None:
            raise RuntimeError(
                "The 'requests' package is required for IMDb scraping."
            )

    @staticmethod
    def _first_letter(query: str) -> str:
        for ch in query.lower():
            if ch.isalnum():
                return ch
        return "0"

    @staticmethod
    def _normalize(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[^\w\s]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    @staticmethod
    def _ratio(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()

    def _query(self, query: str) -> List[dict]:
        if not query.strip():
            return []
        letter = self._first_letter(query)
        encoded = urllib.parse.quote_plus(self._normalize(query))
        url = self.SUGGEST_URL.format(letter=letter, query=encoded)
        try:
            resp = requests.get(url, timeout=self.timeout, headers=self.HEADERS)
            if resp.status_code != 200:
                self.logger.debug("IMDb returned %s for %r", resp.status_code, query)
                return []
            data = resp.json()
            return data.get("d", []) or []
        except Exception as exc:
            self.logger.debug("IMDb query failed for %r: %s", query, exc)
            return []

    def lookup_year(self, title: str) -> Optional[dict]:
        """Return the best match as
        {'year': '2005', 'imdb_id': 'tt0374298', 'matched_title': '...',
         'score': 0.95} or None when nothing confident is found.
        """
        results = self._query(title)
        if not results:
            return None
        norm_query = self._normalize(title)
        scored = []
        for item in results:
            item_title = item.get("l", "")
            item_year = item.get("y")
            if not item_title or not item_year:
                continue
            score = self._ratio(norm_query, self._normalize(item_title))
            # Boost feature films (cinema) over TV/shorts.
            if item.get("q") == "feature":
                score += 0.05
            scored.append((score, item))
        if not scored:
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best = scored[0]
        if best_score < self.min_score:
            self.logger.debug("IMDb best match %r too weak (%.2f) for %r",
                              best.get("l"), best_score, title)
            return None
        return {
            "year": str(best.get("y")),
            "imdb_id": str(best.get("id", "")),
            "matched_title": best.get("l", ""),
            "score": round(best_score, 3),
        }

    def lookup_year_multi(self, *titles: str) -> Optional[dict]:
        """Try several title variants, return the first confident match."""
        for t in titles:
            if not t or not t.strip():
                continue
            hit = self.lookup_year(t)
            if hit:
                return hit
        return None


# ---------------------------------------------------------------------------
# Folder naming
# ---------------------------------------------------------------------------

def sanitize_for_path(name: str) -> str:
    for ch in INVALID_WIN_CHARS:
        name = name.replace(ch, "")
    name = name.strip().rstrip(".")
    if len(name) > MAX_PATH_SEG:
        name = name[:MAX_PATH_SEG].rstrip()
    return name


def build_folder_name(english: str, year: Optional[str], foreign: str) -> str:
    parts = []
    if english:
        parts.append(english)
    if year:
        parts.append(f"({year})")
    if foreign and foreign != english:
        parts.append(foreign)
    return sanitize_for_path(" ".join(parts))


def resolve_target_folder(parent: Path, name: str) -> Path:
    """Return the folder to move a file into.

    If a folder with the given name already exists, REUSE it (the file is
    merged into the existing folder) instead of creating a suffixed copy.
    """
    target = parent / name
    target.mkdir(parents=True, exist_ok=True)
    return target


def unique_file_path(folder: Path, filename: str) -> Path:
    """Return a non-existing destination path inside `folder`.

    If `filename` already exists there, append a counter to the stem, e.g.
    `name (1).mkv`.
    """
    candidate = folder / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1
    while True:
        candidate = folder / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _safe_who_locks(path: str):
    """Return a list of (pid, app_name, exe) locking `path`, or []."""
    try:
        from who_locks import who_locks  # local module
        return who_locks(path)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def process_directory(directory: Path,
                      cfg: Config,
                      api_key: Optional[str],
                      hint_lang: str,
                      extensions: Set[str],
                      apply: bool,
                      no_cache: bool,
                      logger: logging.Logger) -> List[MovieFile]:

    movies = scan_video_files(directory, extensions, logger)
    if not movies:
        logger.info("Nothing to do - no loose video files found.")
        return movies

    # Prepare translator if we have a key.
    translator: Optional[DeepSeekTranslator] = None
    if api_key:
        try:
            translator = DeepSeekTranslator(cfg, api_key, logger)
            logger.info("DeepSeek translator ready (key %s).",
                        _strip_api_key(api_key))
        except Exception as exc:
            logger.warning("Could not initialise translator: %s", exc)
    else:
        logger.warning(
            "No DeepSeek API key available - folder names will use the "
            "foreign title only (no English translation)."
        )

    # Prepare IMDb scraper for filling missing release years.
    imdb: Optional[ImdbScraper] = None
    if getattr(cfg, "enable_imdb", True):
        try:
            imdb = ImdbScraper(logger,
                               min_score=getattr(cfg, "imdb_min_score", 0.60))
            logger.info("IMDb scraper ready (min score %.2f).", imdb.min_score)
        except Exception as exc:
            logger.warning("Could not initialise IMDb scraper: %s", exc)

    cache: Dict[str, dict] = {} if no_cache else load_cache()
    cache_dirty = False
    year_cache: Dict[str, dict] = {}  # keyed by english|foreign title -> imdb hit

    for idx, movie in enumerate(movies, 1):
        logger.info("[%d/%d] %s", idx, len(movies), movie.path.name)

        # 1. parse
        movie.year = extract_year(movie.name_no_ext)
        movie.foreign_title = clean_foreign_title(movie.name_no_ext, movie.year)
        if not movie.foreign_title:
            movie.foreign_title = movie.name_no_ext
        logger.debug("  foreign title: %r | year: %s",
                     movie.foreign_title, movie.year)

        # 2. translate (cached by foreign title + year)
        cache_key = f"{movie.foreign_title.lower()}|{movie.year or ''}"
        translated = cache.get(cache_key)
        if translated is None and translator is not None:
            translated = translator.translate_title(movie.foreign_title, hint_lang)
            if translated:
                cache[cache_key] = translated
                cache_dirty = True
                time.sleep(0.3)  # be gentle on the API

        if translated:
            movie.english_title = translated.get("english_title", "") or ""
            movie.language = translated.get("language", "") or ""
        if not movie.english_title:
            movie.english_title = movie.foreign_title

        # 2b. resolve release year (filename is authoritative; otherwise scrape
        #     IMDb, finally fall back to the AI's suggested_year).
        if movie.year:
            movie.year_source = "filename"
        else:
            hit = None
            if imdb is not None:
                # Try the cache first, then the live IMDb API.
                ck = movie.english_title.lower()
                if ck in year_cache:
                    hit = year_cache[ck]
                else:
                    hit = imdb.lookup_year_multi(movie.english_title,
                                                 movie.foreign_title)
                    if hit:
                        year_cache[ck] = hit
                        cache_dirty = True
                        time.sleep(0.2)
            if hit:
                movie.year = hit["year"]
                movie.imdb_id = hit.get("imdb_id", "")
                movie.year_source = "imdb"
                logger.info("  year via IMDb: %s (%s, score %s)",
                            movie.year, hit.get("matched_title"),
                            hit.get("score"))
            elif translated and translated.get("suggested_year"):
                movie.year = translated["suggested_year"]
                movie.year_source = "ai"
                logger.info("  year via AI: %s", movie.year)
            else:
                movie.year_source = "none"

        # 3. build folder name
        movie.target_folder = build_folder_name(
            movie.english_title, movie.year, movie.foreign_title
        )
        logger.info("  -> folder: %s", movie.target_folder)

        # 4. apply
        if not apply:
            movie.status = "skipped"
            movie.note = "dry-run"
            continue

        try:
            # Reuse an existing folder with the same name (merge) instead of
            # creating a suffixed copy.
            existed_before = (directory / movie.target_folder).exists()
            target_dir = resolve_target_folder(directory, movie.target_folder)
            dest = unique_file_path(target_dir, movie.path.name)
            # If the exact same file is already inside, skip to avoid clobber.
            if dest.resolve() == movie.path.resolve():
                movie.status = "skipped"
                movie.note = "already inside target folder"
                logger.info("  already inside target folder, skipped.")
                continue
            shutil.move(str(movie.path), str(dest))
            movie.status = "moved"
            movie.merged = existed_before
            note = str(dest.relative_to(directory))
            if existed_before:
                note += "  [merged into existing folder]"
            movie.note = note
            logger.info("  moved -> %s%s", dest,
                        "  [merged]" if existed_before else "")
        except PermissionError as exc:
            winerr = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
            # winerror 5 / errno 13  -> ERROR_ACCESS_DENIED  (ACL/ownership)
            # winerror 32            -> ERROR_SHARING_VIOLATION (process holds it)
            if winerr in (32,):
                # Genuine process lock - try to name the culprit.
                holders = _safe_who_locks(str(movie.path))
                if holders:
                    names = ", ".join(f"{a}(pid {p})" for p, a, _e in holders)
                    hint = (f"HELD OPEN by: {names}. Close that app and "
                            "re-run. " + str(exc))
                else:
                    hint = ("LOCKED by another process (Plex scan/player/"
                            "antivirus). Close it and re-run. " + str(exc))
                movie.status = "locked"
            else:
                # Access denied = broken/restrictive ACL on the file itself.
                movie.status = "denied"
                hint = ("ACCESS DENIED - restrictive/missing file permissions "
                        "(ACL). In an ADMIN terminal run:  takeown /F \""
                        + movie.path.name + "\"  &&  icacls \"" + movie.path.name
                        + "\" /grant \"%USERNAME%:(F)\"  -- then re-run. "
                        + str(exc))
            movie.note = hint
            logger.error("  %s", hint)
        except Exception as exc:
            movie.status = "error"
            movie.note = str(exc)
            logger.error("  failed to move: %s", exc)
            logger.error("  failed to move: %s", exc)

    if cache_dirty:
        save_cache(cache)
        logger.debug("Translation cache updated.")

    return movies


# ---------------------------------------------------------------------------
# Plex-standard names, one film at a time (PlexRename's rules)
# ---------------------------------------------------------------------------

def ask(prompt: str) -> str:
    try:
        return input(prompt).replace("﻿", "").strip()
    except EOFError:
        return "q"


def ask_language(directory: Path, names: List[str], pnm) -> str:
    guess = pnm.guess_lang(directory, names)
    codes = {v: k for k, v in pnm.LANG_CHOICES.items()}
    default = codes.get(guess, "")
    print("\n  Original language of these films (TMDB search is limited to it):")
    print("  JP Japanese / KR Korean / CN Chinese / RU Russian / EN English")
    while True:
        raw = ask(f"  Language{f' [Enter = {default}]' if default else ''}> ").upper() or default
        if raw in pnm.LANG_CHOICES:
            return pnm.LANG_CHOICES[raw]
        if raw == "Q":
            raise SystemExit("Stopped.")
        print("  Please type JP, KR, CN, RU or EN.")


def old_style_proposal(film, translator, cache, hint_lang: str) -> dict:
    """Not found on TMDB: the organizer's own name (AI translation + year from the file name), no id,
    file names kept as they are."""
    stem = film.main.stem
    year = extract_year(stem)
    foreign = clean_foreign_title(stem, year) or stem
    key = f"{foreign.lower()}|{year or ''}"
    tr = cache.get(key)
    if tr is None and translator is not None:
        tr = translator.translate_title(foreign, hint_lang)
        if tr:
            cache[key] = tr
    english = (tr or {}).get("english_title") or foreign
    return dict(ok=True, folder=build_folder_name(english, year, foreign),
                files=[(p, p.name) for p in film.videos + film.subs], title=english, year=year or "",
                tmdb="", imdb="", source="old style", conf="",
                notes=["!! NOT IDENTIFIED - no id; English title translated by AI, file names unchanged"])


SOURCES = {"plex": "Plex's match", "plex-imdb": "Plex's IMDb id", "tmdb-search": "TMDB search (only match)",
           "deepseek": "DeepSeek chose among TMDB results", "typed": "the id you typed"}


def show_proposal(i: int, n: int, film, prop: dict) -> None:
    print("\n" + "-" * 78)
    size = sum(p.stat().st_size for p in film.videos) / 1e9
    print(f"  [{i}/{n}] {film.main.name}   ({size:.1f} GB" +
          (f", {len(film.videos)} parts" if len(film.videos) > 1 else "") +
          (f", {len(film.subs)} subtitle(s)" if film.subs else "") + ")")
    if prop.get("tmdb"):
        ids = f"TMDB {prop['tmdb']}"
    elif prop.get("imdb"):
        ids = f"IMDb {prop['imdb']}"
    else:
        ids = "no id"
    src = SOURCES.get(prop.get("source"), prop.get("source") or "")
    print(f"  Found:   {prop.get('title')} ({prop.get('year') or '?'}) - {ids}" + (f", via {src}" if src else ""))
    print(f"  Folder:  {prop['folder']}")
    for old, nf in prop["files"]:
        if old.name != nf:
            print(f"    {old.name}\n      -> {nf}")
        else:
            print(f"    {old.name}  (name kept)")
    for note in prop.get("notes") or []:
        print(f"  Note:    {note}")


def plex_names_run(directory: Path, cfg: Config, api_key: Optional[str], extensions: Set[str],
                   pnm, logger: logging.Logger, preview: bool) -> int:
    entries = [e for e in directory.iterdir() if e.is_file()]
    videos = [e for e in entries if e.suffix.lower() in extensions]
    films = pnm.group_films(videos, [e for e in entries if e not in videos])
    if not films:
        logger.info("Nothing to do - no loose video files found in %s", directory)
        return 0
    print(f"\n  {len(films)} loose film(s) in {directory}")
    lang = ask_language(directory, [f.main.stem for f in films], pnm)
    note = pnm.read_plex(directory)
    if note:
        print("  " + note)
    translator = None
    if api_key:
        try:
            translator = DeepSeekTranslator(cfg, api_key, logger)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not initialise translator: %s", exc)
    cache = load_cache()
    if preview:
        print("\n  PREVIEW - nothing is moved.")
    else:
        print("\n  For each film: Enter = accept, e = type another name, i = type a TMDB/IMDb id or link,")
        print("  s = skip (leave it loose), q = stop.  Nothing moves until you accept.")
    done = skipped = failed = 0
    logs = []
    try:
        for i, film in enumerate(films, 1):
            print(f"\n  looking up {film.main.name} ...", flush=True)
            tid, imdb, source, conf, why = pnm.identify(film, pnm.file_lang(film.main.stem, lang))
            if tid or imdb:
                prop = pnm.propose(film, directory, tid, imdb, source, conf, why)
            else:
                prop = dict(ok=False)
            if not prop.get("ok"):
                prop = old_style_proposal(film, translator, cache, "auto")
                if why:
                    prop["notes"].append(why)
            while True:
                show_proposal(i, len(films), film, prop)
                if preview:
                    break
                ans = ask("  Accept? [Enter / e / i / s / q] > ").lower()
                if ans in ("", "y", "yes"):
                    log, problems = pnm.apply(prop, directory)
                    if log:
                        logs.append(log)
                    if problems:
                        failed += 1
                        for pr in problems:
                            print(f"  !! {pr}")
                            logger.error("  %s", pr)
                    else:
                        done += 1
                        print(f"  Done -> {prop['folder']}")
                        logger.info("  renamed + moved %s -> %s (log %s)", film.main.name, prop["folder"], log)
                    break
                if ans == "e":
                    typed = ask("  New folder name (files follow it), Enter = keep > ")
                    if typed:
                        prop = pnm.restem(prop, typed)
                    continue
                if ans == "i":
                    typed = ask("  TMDB id / TMDB link / IMDb tt-id / IMDb link > ")
                    t2, i2 = pnm.parse_id(typed)
                    if not (t2 or i2):
                        print("  Not an id or link I can read.")
                        continue
                    p2 = pnm.propose(film, directory, t2, i2, "typed", "1.0", "")
                    if p2.get("ok"):
                        prop = p2
                    else:
                        print("  " + "; ".join(p2.get("notes") or []))
                    continue
                if ans == "s":
                    skipped += 1
                    break
                if ans == "q":
                    raise KeyboardInterrupt
                print("  Please press Enter, or type e, i, s or q.")
    except KeyboardInterrupt:
        print("\n  Stopped.")
    finally:
        pnm.save_cache()
        save_cache(cache)
    print("\n" + "=" * 78)
    print(f"  Renamed and moved: {done}   skipped: {skipped}   problems: {failed}   (of {len(films)})")
    if logs:
        print("  Undo: PlexRename's 'Undo a rename...' button, with one of these logs:")
        for lg in logs:
            print(f"    {lg}")
        print("  Then scan this library in Plex (and 'Refresh Metadata' on films already in Plex).")
    print("=" * 78)
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(movies: List[MovieFile], apply: bool) -> None:
    print("\n" + "=" * 70)
    mode = "APPLY" if apply else "DRY-RUN (nothing was moved)"
    print(f"  MOVIE FOLDER ORGANIZER - {mode}")
    print("=" * 70)
    moved = sum(1 for m in movies if m.status == "moved")
    skipped = sum(1 for m in movies if m.status == "skipped")
    errors = sum(1 for m in movies if m.status == "error")
    locked = sum(1 for m in movies if m.status == "locked")
    denied = sum(1 for m in movies if m.status == "denied")
    for m in movies:
        flag = {"moved": "OK ", "skipped": "-- ", "error": "ERR",
                "locked": "LCK", "denied": "ACL", "pending": ".. "}.get(m.status, "?? ")
        yr = m.year or "????"
        yrsrc = f" ({m.year_source})" if m.year and m.year_source else ""
        print(f"  [{flag}] {m.english_title} ({yr}){yrsrc}  "
              f"[{m.language or '?'}]")
        if m.imdb_id:
            print(f"        imdb:   {m.imdb_id}")
        print(f"        file:   {m.path.name}")
        print(f"        folder: {m.target_folder}")
        if m.note:
            print(f"        note:   {m.note}")
    print("-" * 70)
    print(f"  Total: {len(movies)}  |  moved: {moved}  |  "
          f"skipped: {skipped}  |  locked: {locked}  |  "
          f"denied(ACL): {denied}  |  errors: {errors}")
    if locked:
        print("  (!) 'locked' files are held open by another process.")
        print("      Close that app (shown per-file) and re-run.")
    if denied:
        print("  (!) 'ACL' files have broken/restrictive permissions. In an")
        print("      ADMIN terminal run: takeown /F <file>  &  icacls <file>")
        print("      /grant \"%USERNAME%:(F)\"  then re-run.")
    print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def prompt_for_directory(default: Optional[str] = None) -> Path:
    """Interactively ask the user for the target movie directory."""
    print("\n" + "=" * 70)
    print("  MOVIE FOLDER ORGANIZER - interactive mode")
    print("=" * 70)
    print("  Enter the folder that contains your loose movie files.")
    if default:
        print(f"  (press Enter to use: {default})")
    while True:
        raw = input("  Folder path> ").strip().strip('"').strip("'")
        if not raw and default:
            raw = default
        if not raw:
            print("  Please enter a path.")
            continue
        path = Path(raw)
        if path.is_dir():
            return path
        print(f"  That path does not exist or is not a folder: {path}")
        print("  Try again, or press Ctrl+C to cancel.")


def prompt_confirm_apply(directory: Path) -> bool:
    print(f"\n  Target folder: {directory}")
    print("  A DRY-RUN will be performed first. After reviewing the plan you")
    print("  will be asked whether to actually move the files.")
    return True


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Organize loose foreign-language movie files into named folders."
    )
    # Directory is OPTIONAL on the command line - if missing we prompt.
    parser.add_argument("directory", nargs="?", default=None,
                        help="Target movie directory (if omitted you will be prompted).")
    parser.add_argument("--apply", action="store_true",
                        help="Actually create folders and move files (default: dry-run).")
    parser.add_argument("--lang", default=None,
                        help=argparse.SUPPRESS)  # kept for compat; auto-detect is default
    parser.add_argument("--extensions", nargs="*", default=None,
                        help="Override the list of video extensions to scan.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH),
                        help="Path to config.json.")
    parser.add_argument("--no-cache", action="store_true",
                        help="Ignore the translation cache (force re-translation).")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging.")
    parser.add_argument("--old-style", action="store_true",
                        help="Old behaviour: 'English (Year) Foreign' folders, file names kept, no TMDB id.")
    parser.add_argument("--preview", action="store_true",
                        help="Plex-standard names: show every proposal, move nothing.")
    args = parser.parse_args(argv)

    logger = setup_logging(args.verbose)
    logger.info("Movie Folder Organizer started at %s",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    cfg = load_config(Path(args.config))
    if args.extensions:
        cfg.video_extensions = [e.lower() if e.startswith(".") else f".{e.lower()}"
                                for e in args.extensions]
    extensions = {e.lower() for e in cfg.video_extensions}
    # Language is ALWAYS auto-detected from the filename by the DeepSeek model.
    hint_lang = "auto"

    # Resolve target directory - prompt interactively if not supplied.
    directory_arg = args.directory
    if directory_arg:
        directory = Path(directory_arg)
        if not directory.is_dir():
            logger.error("Target directory does not exist: %s", directory)
            return 2
    else:
        try:
            directory = prompt_for_directory()
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    api_key = read_api_key(cfg, logger)

    # Default: Plex-standard names from PlexRename, accepted one film at a time.
    if not args.old_style:
        import plex_names as pnm
        if pnm.load(cfg.plexrename_folder):
            return plex_names_run(directory, cfg, api_key, extensions, pnm, logger, args.preview)
        logger.warning("%s - using the old style names.", pnm.why())

    # Step 1: always run a DRY-RUN first so the user can review the plan.
    logger.info("Running DRY-RUN to build the plan...")
    movies = process_directory(
        directory=directory,
        cfg=cfg,
        api_key=api_key,
        hint_lang=hint_lang,
        extensions=extensions,
        apply=False,           # dry-run first
        no_cache=args.no_cache,
        logger=logger,
    )
    print_report(movies, apply=False)

    movable = [m for m in movies if m.status == "skipped" and m.note == "dry-run"]
    if not movable:
        logger.info("No files need moving. Done.")
        return 0

    # Step 2: decide whether to actually apply.
    should_apply = args.apply
    if not should_apply:
        try:
            answer = input(
                "  Apply these changes now? Files will be moved into the "
                "folders shown above.\n  Type 'yes' to apply (anything else "
                "to abort) > "
            ).strip().lower()
            should_apply = answer in ("yes", "y")
        except KeyboardInterrupt:
            print("\nAborted.")
            return 130

    if not should_apply:
        print("\n  No files were moved (dry-run only).")
        return 0

    # Step 3: apply for real. Translations are cached, so this is cheap.
    logger.info("Applying changes...")
    movies = process_directory(
        directory=directory,
        cfg=cfg,
        api_key=api_key,
        hint_lang=hint_lang,
        extensions=extensions,
        apply=True,
        no_cache=args.no_cache,
        logger=logger,
    )
    print_report(movies, apply=True)
    needs_attention = any(m.status in ("error", "locked", "denied") for m in movies)
    return 1 if needs_attention else 0


if __name__ == "__main__":
    sys.exit(main())
