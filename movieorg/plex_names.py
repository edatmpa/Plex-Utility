"""Plex-standard names for the Movie Folder Organizer, using PlexRename's own code (plexname.py), so a loose film
gets the same folder and file name PlexRename would give it:

    English Title (Year) {tmdb-ID}[ {edition-X}] Native title [RES]\\
        English Title (Year) {tmdb-ID}[ {edition-X}] Native title [RES][ [EngHardSub]][ - ptN].ext
        ... same stem .en.srt / .ru.srt ...

Who the film is (never invented): Plex's own match of the file -> Plex's IMDb id on TMDB -> TMDB search in the
chosen language -> DeepSeek choosing among the TMDB results only. Titles and year live from TMDB; the resolution
is measured (Plex, else MediaInfo).
Every move is logged as PlexRename's logs\\manual_rename_*.csv, so PlexRename's 'Undo a rename…' reverses it.
"""
from __future__ import annotations

import csv
import os
import re
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_FOLDER = r"E:\PlexRename"

_pn = None
_why = ""
_tmdb = None
_ds = None
_facts = {}


def load(folder: str = ""):
    """PlexRename's plexname module, or None (reason in why())."""
    global _pn, _why
    if _pn is not None:
        return _pn
    folder = folder or DEFAULT_FOLDER
    if not Path(folder, "plexname.py").is_file():
        _why = f"PlexRename not found in {folder} (config.json 'plexrename_folder')"
        return None
    if folder not in sys.path:
        sys.path.append(folder)
    try:
        import plexname as pn
    except (Exception, SystemExit) as e:  # noqa: BLE001 - e.g. PlexRename's config.json broken
        _why = f"PlexRename's naming could not be loaded ({e})"
        return None
    _pn = pn
    return pn


def why() -> str:
    return _why


def tmdb():
    global _tmdb
    if _tmdb is None:
        _tmdb = _pn.TMDB()
    return _tmdb


def deepseek():
    """PlexRename's DeepSeek chooser, or None when it can't start (no key / no openai package)."""
    global _ds
    if _ds is None:
        try:
            _ds = _pn.DeepSeek()
        except Exception:  # noqa: BLE001
            _ds = False
    return _ds or None


def save_cache():
    if _tmdb is not None:
        _tmdb.save()


def read_plex(directory: Path):
    """Plex's facts for every file under the folder (read-only database)."""
    global _facts
    try:
        _facts = _pn.PlexDB().parts_under(directory)
    except Exception as e:  # noqa: BLE001 - no Plex DB on this PC
        _facts = {}
        return f"Plex database not read ({e}) - films are found on TMDB only"
    return ""


# ---------------------------------------------------------------- language

LANG_CHOICES = {"JP": "ja", "KR": "ko", "CN": "zh", "RU": "ru", "EN": "en"}
FOLDER_HINTS = [("ja", r"japan|j\.\w"), ("ko", r"korea"), ("zh", r"chin|hong ?kong|taiwan|wuxia"),
                ("ru", r"russia|soviet|ussr")]


def guess_lang(directory: Path, names) -> str:
    """Language code from the letters in the file names, else from the folder path, else ''."""
    text = " ".join(names)
    counts = [("ja", len(re.findall(r"[\u3040-\u30ff]", text))), ("ko", len(re.findall(r"[\uac00-\ud7af]", text))),
              ("ru", len(re.findall(r"[\u0400-\u04ff]", text))), ("zh", len(re.findall(r"[\u4e00-\u9fff]", text)))]
    best = max(counts, key=lambda c: c[1])
    if best[1] >= 3:
        return best[0] if not (best[0] == "zh" and counts[0][1]) else "ja"
    for code, pat in FOLDER_HINTS:
        if re.search(pat, str(directory), re.I):
            return code
    return ""


def file_lang(name: str, default: str) -> str:
    """Per file: native letters in the name win over the run's language (a Russian name in a mixed folder)."""
    g = guess_lang(Path(""), [name])
    return g or default


# ---------------------------------------------------------------- one film = its video file(s) + subtitles

class Film:
    def __init__(self, videos, subs):
        self.videos = videos          # [Path] - several = parts (CD1/CD2)
        self.subs = subs              # [Path] loose subtitles that belong to it
        self.prop = {}                # proposal (see propose)

    @property
    def main(self):
        return self.videos[0]


def _part_no(stem: str):
    m = _pn.PART_RE.search(stem)
    return int(m.group(1)) if m else None


def _base(stem: str) -> str:
    return re.sub(r"\W+", "", _pn.PART_RE.sub("", stem).casefold())


def group_films(videos, others):
    """Loose videos -> films. Parts (CD1/CD2, Part 1/2) of one film go together; samples/trailers are left alone.
    Subtitles whose name starts with a video's name go with that film."""
    vids = sorted(v for v in videos if not _pn.EXTRA_RE.search(v.stem))
    groups, used = [], set()
    for v in vids:
        if v in used:
            continue
        g = [v]
        if _part_no(v.stem):
            g = sorted((w for w in vids if w not in used and _part_no(w.stem) and _base(w.stem) == _base(v.stem)),
                       key=lambda w: _part_no(w.stem))
        used.update(g)
        groups.append(g)
    subs = [p for p in others if p.suffix.lower() in _pn.SUB_EXT]
    films = []
    for g in groups:
        stems = [v.stem.casefold() for v in g]
        mine = [s for s in subs if any(s.name.casefold().startswith(st + ".") or s.stem.casefold() == st
                                       for st in stems)]
        films.append(Film(g, mine))
    return films


# ---------------------------------------------------------------- proposal

def _facts_of(p: Path):
    try:
        return _pn.lookup(_facts, p)
    except Exception:  # noqa: BLE001
        return None


def _res(p: Path, f) -> str:
    return (f or {}).get("res") or _pn.mediainfo_res(p)


def identify(film: Film, lang: str):
    """-> (tmdb, imdb, source, confidence, note)."""
    f = _facts_of(film.main)
    folder = film.main.with_suffix("")   # the file name stands in for the folder name in PlexRename's search
    try:
        return _pn.identify(folder, film.videos, f, tmdb(), deepseek(), lang)
    except Exception as e:  # noqa: BLE001 - network
        return "", "", "", "0", f"TMDB search failed ({type(e).__name__})"


def parse_id(text: str):
    """A TMDB id/link or an IMDb tt-id/link typed by the owner -> (tmdb, imdb)."""
    m = re.search(r"(tt\d{5,})", text)
    if m:
        return "", m.group(1)
    m = re.search(r"themoviedb\.org/movie/(\d+)|^\s*(\d+)\s*$", text)
    if m:
        return m.group(1) or m.group(2), ""
    return "", ""


def propose(film: Film, directory: Path, tid="", imdb="", source="", conf="", note=""):
    """Proposed folder + file names. Returns a dict:
       ok, folder (name), files [(old Path, new name)], title, year, tmdb, imdb, source, notes [..]."""
    pn = _pn
    notes = [note] if note else []
    if not tid and imdb:
        try:
            tid = tmdb().find_imdb(imdb) or ""
        except Exception:  # noqa: BLE001
            pass
    meta = None
    if tid:
        try:
            meta = tmdb().movie(tid)
        except Exception as e:  # noqa: BLE001
            return dict(ok=False, notes=[f"TMDB did not answer ({type(e).__name__})"])
        if not meta:
            return dict(ok=False, notes=[f"TMDB has no film {tid}"])
    if not meta and not imdb:
        return dict(ok=False, notes=notes or ["not identified"])
    f = _facts_of(film.main)
    if meta:
        title, year, orig = meta["title"], meta["year"], meta["orig"]
    else:   # IMDb id only: Plex's title for it
        title, year, orig = (f or {}).get("title") or pn.clean_from_filename(film.main.stem)[0], \
            (f or {}).get("year") or pn.clean_from_filename(film.main.stem)[1], ""
        notes.append("not on TMDB - named with the IMDb id")
    fy = pn.YEAR_RE.search(film.main.stem)
    if fy and year and abs(int(fy.group(1)) - int(year)) > 1:
        notes.append(f"!! TMDB year {year} differs from the file name's {fy.group(1)} - wrong film?")
    name = pn.safe_name(title)
    series = pn.approved_series(tid, imdb, str((f or {}).get("mid") or ""))
    if series and not pn.has_series(name, series):
        name = f"{pn.safe_name(series)} - {name}"
    idtag = f"{{tmdb-{tid}}}" if meta else f"{{imdb-{imdb}}}"
    ed = pn.EDITION_RE.search(re.sub(r"[._]+", " ", film.main.stem))
    edition = "{edition-" + ed.group(1).title().replace("'S", "'s") + "}" if ed else ""
    native = pn.safe_name(orig) if orig and pn.words_of(orig) != pn.words_of(title) else ""
    if orig and not pn.words_of(orig) and pn.safe_name(orig) != pn.safe_name(title):
        native = pn.safe_name(orig)   # words_of drops non-Latin letters: a CJK/Cyrillic title is always kept
    ress = {v: _res(v, _facts_of(v)) for v in film.videos}
    best = max(ress.values(), key=lambda r: pn.RES_ORDER.get(r, 0)) if ress else ""
    hard = any(pn.HARDSUB_RE.search(v.stem) for v in film.videos)

    def compose(nat, res):
        parts = [f"{name} ({year})" if year else name, idtag, edition, nat, f"[{res}]" if res else ""]
        return pn.safe_name(" ".join(p for p in parts if p))

    def build(nat):
        files, used = [], set()
        for i, v in enumerate(film.videos, 1):
            stem = compose(nat, ress[v]) + (" [EngHardSub]" if hard else "") + \
                (f" - pt{i}" if len(film.videos) > 1 else "")
            nf = stem + pn.out_ext(v)
            used.add(nf.casefold())
            files.append((v, nf))
        for s in film.subs:
            own = next((nf for v, nf in files if s.name.casefold().startswith(v.stem.casefold())), files[0][1])
            idx = s.with_suffix(".idx")
            code, flags = pn.sub_language(idx if s.suffix.lower() == ".sub" and idx.exists() else s)
            base = own[: own.rfind(".")] + (f".{code}" if code else "") + flags
            nf, k = base + s.suffix.lower(), 2
            while nf.casefold() in used:
                nf, k = f"{base}.{k}{s.suffix.lower()}", k + 1
            used.add(nf.casefold())
            files.append((s, nf))
        return compose(nat, best), files

    nat = native
    while True:   # paths kept <= 240 characters by shortening the native title only
        folder, files = build(nat)
        if max(len(str(directory / folder / nf)) for _, nf in files) <= pn.MAX_PATH or not nat:
            break
        nat = nat[:-1].rstrip()
    if nat != native:
        notes.append("native title shortened (path length)" if nat else "native title left out (path length)")
    if (directory / folder).exists():
        notes.append(f"!! a folder with this name already exists - the film may already be in the library "
                     f"(accepting moves the file into it)")
    return dict(ok=True, folder=folder, files=files, title=title, year=year, tmdb=tid if meta else "",
                imdb=imdb, source=source, conf=conf, notes=[n for n in notes if n])


def restem(prop: dict, new_stem: str) -> dict:
    """The owner typed a different name: it becomes the folder name, and the files follow it
    (their own resolution, parts and subtitle codes are kept)."""
    pn = _pn
    core = lambda x: re.sub(r"\s*\[(2160p|1080p|720p|SD)\]$", "", x)  # noqa: E731
    new_stem = pn.safe_name(new_stem)
    oc, nc = core(prop["folder"]), core(new_stem)
    files = [(old, nc + nf[len(oc):] if nf.startswith(oc) else nf) for old, nf in prop["files"]]
    return dict(prop, folder=new_stem, files=files, notes=prop["notes"] + ["name typed by you"])


# ---------------------------------------------------------------- doing it

def log_path(name: str) -> Path:
    _pn.LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _pn.LOG_DIR / (f"manual_rename_{re.sub(r'[^0-9A-Za-z]+', '_', name)[:40]}_"
                          f"{datetime.now():%Y-%m-%d_%H%M%S}.csv")


def apply(prop: dict, directory: Path) -> tuple:
    """Make the folder (if new), move + rename every file into it. Logged step by step (MKDIR, MOVE).
    Returns (log path, [problems])."""
    target = directory / prop["folder"]
    clash = [nf for _, nf in prop["files"] if (target / nf).exists()]
    if clash:
        return "", [f"file already exists in the folder: {clash[0]}"]
    log = log_path(prop["folder"])
    problems = []
    with open(log, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=_pn.LOG_FIELDS)
        w.writeheader()
        if not target.exists():
            target.mkdir()
            w.writerow(dict(op="MKDIR", src="", dst=str(target), result="ok"))
        for old, nf in prop["files"]:
            dst = target / nf
            try:
                os.rename(old, dst)     # same share: a rename, no copying
                w.writerow(dict(op="MOVE", src=str(old), dst=str(dst), result="ok"))
            except OSError as e:
                problems.append(f"{old.name}: {e.strerror or e}")
                w.writerow(dict(op="MOVE", src=str(old), dst=str(dst), result=f"failed: {e}"))
            fh.flush()
    return str(log), problems
