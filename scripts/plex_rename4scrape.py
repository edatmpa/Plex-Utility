import os
import re
import shutil
import requests
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path
from dotenv import load_dotenv

# ================= PROJECT ROOT & ENV =================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# ================= DEFAULTS FROM .env =================
_DEFAULT_INPUT  = os.getenv("INPUT_DIR",  str(PROJECT_ROOT / "raw_media"))
_DEFAULT_OUTPUT = os.getenv("OUTPUT_DIR", str(PROJECT_ROOT / "plex_library"))
_DEFAULT_TYPE   = os.getenv("MEDIA_TYPE",  "tv")
_DEFAULT_ACTION = os.getenv("FILE_ACTION", "copy")   # "copy" or "move"
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
TMDB_API_KEY     = os.getenv("TMDB_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

VIDEO_EXTENSIONS    = {".mkv", ".mp4", ".avi", ".mov", ".m4v"}
COMPANION_EXTENSIONS = {".srt", ".sub", ".idx", ".ass", ".ssa", ".vtt", ".nfo"}


# ===================== HELPERS ========================

def companion_suffix(path: Path) -> str:
    """Return the full extension chain to preserve (.en.srt, .srt, .nfo …)."""
    suffixes = path.suffixes
    if len(suffixes) >= 2:
        lang = suffixes[-2].lstrip(".")
        if re.fullmatch(r"[a-z]{2,8}", lang):
            return "".join(suffixes[-2:])
    return path.suffix


def find_companions(video_path: Path) -> list:
    """Find companion subtitle/nfo files sharing the video's stem."""
    stem = video_path.stem
    results = []
    for sib in video_path.parent.iterdir():
        if sib == video_path:
            continue
        if sib.suffix.lower() not in COMPANION_EXTENSIONS:
            continue
        if sib.name.startswith(stem):
            results.append((sib, companion_suffix(sib)))
    return results


def sanitize_windows(name: str) -> str:
    """Remove/replace characters illegal in Windows file/folder names."""
    # Replace : with space-dash to preserve readability
    name = name.replace(":", " -")
    # Remove remaining illegal chars
    name = re.sub(r'[<>"/\|?*]', "", name)
    # Collapse multiple spaces
    name = re.sub(r"  +", " ", name).strip()
    return name


# ===================== API HELPERS ====================

def ask_deepseek_to_parse(raw_folder: str, raw_file: str, api_key: str):
    prompt = (
        "Analyze the following media path/filename and extract structured information.\n"
        f"Folder Name: {raw_folder}\n"
        f"File Name: {raw_file}\n\n"
        "Return ONLY a valid JSON object with these exact keys:\n"
        '- "title_en": English or transliterated title\n'
        '- "title_ru": Original Russian Cyrillic title (if applicable, else empty string)\n'
        '- "year": 4-digit release year (or null)\n'
        '- "season": Season number as integer (or null if movie)\n'
        '- "episode": Episode number as integer (or null if movie)\n'
        "IMPORTANT: If episode number is present, always provide a season number (use 1 if not stated explicitly).\n"
    )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    try:
        r = requests.post(DEEPSEEK_API_URL, json=payload, headers=headers, timeout=30)
        r.raise_for_status()
        raw_text = r.json()["choices"][0]["message"]["content"]
        cleaned  = re.sub(r"^```(?:json)?\s*", "", raw_text.strip())
        cleaned  = re.sub(r"\s*```$", "", cleaned)
        return json.loads(cleaned), prompt, raw_text, None
    except Exception as e:
        err = str(e)
        print(f"[-] DeepSeek error for '{raw_file}': {err}")
        return None, prompt, "", err


def _tmdb_search(title: str, year, media_type: str) -> list:
    """Try TMDB title search, stripping special chars, with year fallback."""
    clean = re.sub(r"[:\-]", " ", title)
    clean = re.sub(r"  +", " ", clean).strip()
    url   = f"https://api.themoviedb.org/3/search/{media_type}"
    year_key = "first_air_date_year" if media_type == "tv" else "year"
    for q in ([clean, title] if clean != title else [clean]):
        for use_year in ([True, False] if year else [False]):
            params = {"api_key": TMDB_API_KEY, "query": q}
            if use_year:
                params[year_key] = year
            try:
                r = requests.get(url, params=params, timeout=10)
                r.raise_for_status()
                results = r.json().get("results", [])
                if results:
                    return results
            except Exception:
                pass
    return []


def get_tmdb_ids(title: str, year, media_type: str = "tv"):
    """Returns (tmdb_id, imdb_id). Either may be None."""
    if not TMDB_API_KEY:
        return None, None
    results = _tmdb_search(title, year, media_type)
    # Fallback: try the other media type (show catalogued as movie or vice-versa)
    alt_type = "movie" if media_type == "tv" else "tv"
    if not results:
        results = _tmdb_search(title, year, alt_type)
    if not results:
        return None, None
    tmdb_id = results[0]["id"]
    # Use whichever type actually returned results
    if results:
        found_type = alt_type if not _tmdb_search(title, year, media_type) else media_type
    else:
        found_type = media_type
    try:
        r2 = requests.get(
            f"https://api.themoviedb.org/3/{found_type}/{tmdb_id}/external_ids",
            params={"api_key": TMDB_API_KEY}, timeout=10
        )
        r2.raise_for_status()
        return tmdb_id, r2.json().get("imdb_id")
    except Exception as e:
        print(f"[-] TMDB external_ids error: {e}")
        return tmdb_id, None


def build_names(metadata: dict, media_type: str, tmdb_id, video_ext: str, output_path: Path):
    """Return (show_name, new_stem, target_dir, new_video_name)."""
    title_en = sanitize_windows(metadata.get("title_en") or "Unknown")
    title_ru = sanitize_windows(metadata.get("title_ru") or "")
    year     = metadata.get("year")
    season   = metadata.get("season")
    episode  = metadata.get("episode")

    id_tag    = f" {{tmdb-{tmdb_id}}}" if tmdb_id else ""
    year_str  = f" ({year})"     if year    else ""
    ru_str    = f" ({title_ru})" if title_ru else ""
    show_name = f"{title_en}{ru_str}{year_str}{id_tag}"

    if media_type == "tv":
        s_num, e_num = (season or 1), (episode or 1)
        target_dir = output_path / show_name / f"Season {s_num:02d}"
        if episode:  # season defaults to 1 if AI didn't detect it
            stem      = f"{title_en} - S{s_num:02d}E{e_num:02d}"
            new_video = stem + video_ext
        else:
            stem      = None
            new_video = None       # keep original name (no episode detected)
    else:
        target_dir = output_path / show_name
        stem       = show_name
        new_video  = f"{show_name}{video_ext}"

    return show_name, stem, target_dir, new_video



def get_tmdb_top3(title: str, year, media_type: str = "tv") -> list:
    """Return up to 3 TMDB search hits as dicts with id/name/year keys."""
    if not TMDB_API_KEY:
        return []
    results = _tmdb_search(title, year, media_type)
    if not results:
        alt = "movie" if media_type == "tv" else "tv"
        results = _tmdb_search(title, year, alt)

    hits = []
    for item in results[:3]:
        date = item.get("first_air_date") or item.get("release_date") or ""
        hits.append({
            "id":   item["id"],
            "name": item.get("name") or item.get("title", ""),
            "year": date[:4] if date else "?",
        })
    return hits


# ===================== GUI — SETTINGS =================
# Both dialog classes use tk.Toplevel so there is only ONE tk.Tk() root.

def _centre(win):
    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    win.geometry(f"+{(win.winfo_screenwidth()-w)//2}+{(win.winfo_screenheight()-h)//2}")


class SettingsDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Plex Media Renamer — Settings")
        self.resizable(False, False)
        self.grab_set()          # modal
        self.result = None

        self.configure(padx=18, pady=14)
        lbl = dict(anchor="w")
        frm = dict(fill="x", pady=4)

        # Input folder
        tk.Label(self, text="Input folder  (messy files):", **lbl).pack(fill="x")
        f_in = tk.Frame(self); f_in.pack(**frm)
        self.var_input = tk.StringVar(value=_DEFAULT_INPUT)
        tk.Entry(f_in, textvariable=self.var_input, width=62).pack(side="left", expand=True, fill="x")
        tk.Button(f_in, text="Browse…", command=self._browse_input).pack(side="left", padx=(6,0))

        # Output folder
        tk.Label(self, text="Output folder  (Plex library):", **lbl).pack(fill="x", pady=(8,0))
        f_out = tk.Frame(self); f_out.pack(**frm)
        self.var_output = tk.StringVar(value=_DEFAULT_OUTPUT)
        tk.Entry(f_out, textvariable=self.var_output, width=62).pack(side="left", expand=True, fill="x")
        tk.Button(f_out, text="Browse…", command=self._browse_output).pack(side="left", padx=(6,0))

        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=10)

        # Media type
        tk.Label(self, text="Media type:", **lbl).pack(fill="x")
        self.var_type = tk.StringVar(value=_DEFAULT_TYPE)
        f_type = tk.Frame(self); f_type.pack(**frm)
        tk.Radiobutton(f_type, text="TV Series", variable=self.var_type, value="tv"   ).pack(side="left", padx=(0,20))
        tk.Radiobutton(f_type, text="Movies",    variable=self.var_type, value="movie").pack(side="left")

        # File action
        tk.Label(self, text="After rename:", **lbl).pack(fill="x", pady=(8,0))
        self.var_action = tk.StringVar(value=_DEFAULT_ACTION)
        f_act = tk.Frame(self); f_act.pack(**frm)
        tk.Radiobutton(f_act, text="Copy files  (keep originals)",
                       variable=self.var_action, value="copy").pack(side="left", padx=(0,20))
        tk.Radiobutton(f_act, text="Move files  (delete originals)",
                       variable=self.var_action, value="move").pack(side="left")

        # Dry run
        tk.Label(self, text="Mode:", **lbl).pack(fill="x", pady=(8,0))
        self.var_dryrun = tk.BooleanVar(value=True)
        f_dry = tk.Frame(self); f_dry.pack(**frm)
        tk.Checkbutton(f_dry,
                       text="Dry Run — preview renames & IMDB links, no files touched",
                       variable=self.var_dryrun).pack(side="left")

        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=10)

        # API key status
        api_ok  = "✓ loaded" if DEEPSEEK_API_KEY else "✗ missing — set DEEPSEEK_API_KEY in .env"
        tmdb_ok = "✓ loaded" if TMDB_API_KEY     else "not set  (optional — needed for IMDB links)"
        tk.Label(self, text=f"DeepSeek API key:  {api_ok}",
                 fg="green" if DEEPSEEK_API_KEY else "red",  **lbl).pack(fill="x")
        tk.Label(self, text=f"TMDB API key:      {tmdb_ok}",
                 fg="green" if TMDB_API_KEY     else "gray", **lbl).pack(fill="x")

        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=10)

        # Buttons
        f_btn = tk.Frame(self); f_btn.pack(fill="x")
        tk.Button(f_btn, text="Start", width=18,
                  bg="#1a7abf", fg="white", command=self._ok).pack(side="right")
        tk.Button(f_btn, text="Cancel", width=10,
                  command=self.destroy).pack(side="right", padx=(0,8))

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        _centre(self)

    def _browse_input(self):
        d = filedialog.askdirectory(title="Select input folder",
                                    initialdir=self.var_input.get() or str(PROJECT_ROOT))
        if d: self.var_input.set(d)

    def _browse_output(self):
        d = filedialog.askdirectory(title="Select output folder",
                                    initialdir=self.var_output.get() or str(PROJECT_ROOT))
        if d: self.var_output.set(d)

    def _ok(self):
        if not self.var_input.get().strip():
            messagebox.showerror("Missing input", "Please select an input folder.", parent=self)
            return
        if not DEEPSEEK_API_KEY:
            messagebox.showerror("API key missing",
                                 f"DEEPSEEK_API_KEY not set.\nAdd it to:\n{PROJECT_ROOT / '.env'}",
                                 parent=self)
            return
        self.result = {
            "input_dir":   self.var_input.get().strip(),
            "output_dir":  self.var_output.get().strip() or _DEFAULT_OUTPUT,
            "media_type":  self.var_type.get(),
            "file_action": self.var_action.get(),
            "dry_run":     self.var_dryrun.get(),
        }
        self.destroy()


# ===================== GUI — RESULTS ==================

class ResultsWindow(tk.Toplevel):
    T_HEAD   = "head"
    T_SOURCE = "src"
    T_PARSE  = "parse"
    T_TARGET = "target"
    T_COMP   = "comp"
    T_IMDB   = "imdb"
    T_TMDB   = "tmdb"
    T_WARN   = "warn"
    T_SEP    = "sep"
    T_AI_SEND = "ai_send"
    T_AI_RECV = "ai_recv"
    T_MATCH   = "match"
    T_MISS    = "miss"

    def __init__(self, master, settings: dict):
        super().__init__(master)
        self.title("Dry Run Results — Plex Media Renamer")
        self.geometry("960x660")
        self.settings = settings

        tb = tk.Frame(self, bg="#2b2b2b"); tb.pack(fill="x")
        tk.Label(tb, text="  DRY RUN — no files changed",
                 bg="#2b2b2b", fg="#f0c040",
                 font=("Segoe UI", 10, "bold")).pack(side="left", pady=6)
        tk.Button(tb, text="Save log…", command=self._save_log,
                  bg="#444", fg="white", relief="flat", padx=8).pack(side="right", padx=8, pady=4)

        self.txt = scrolledtext.ScrolledText(
            self, wrap="word", font=("Consolas", 9),
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="white", relief="flat"
        )
        self.txt.pack(fill="both", expand=True)
        self.txt.config(state="disabled")

        self.txt.tag_config(self.T_HEAD,   foreground="#ffffff", font=("Consolas", 9, "bold"))
        self.txt.tag_config(self.T_SOURCE, foreground="#6699cc")
        self.txt.tag_config(self.T_PARSE,  foreground="#b5cea8")
        self.txt.tag_config(self.T_TARGET, foreground="#c586c0")
        self.txt.tag_config(self.T_COMP,   foreground="#9cdcfe")
        self.txt.tag_config(self.T_IMDB,   foreground="#f0a050")
        self.txt.tag_config(self.T_TMDB,   foreground="#4ec9b0")
        self.txt.tag_config(self.T_WARN,   foreground="#f44747")
        self.txt.tag_config(self.T_SEP,    foreground="#3a3a3a")
        self.txt.tag_config(self.T_AI_SEND, foreground="#dcdcaa")
        self.txt.tag_config(self.T_AI_RECV, foreground="#ce9178")
        self.txt.tag_config(self.T_MATCH,   foreground="#6ac26a", font=("Consolas", 9, "bold"))
        self.txt.tag_config(self.T_MISS,    foreground="#f44747", font=("Consolas", 9, "bold"))

        self.status_var = tk.StringVar(value="Running…")
        tk.Label(self, textvariable=self.status_var, anchor="w",
                 bg="#333", fg="#aaa", font=("Segoe UI", 8)).pack(fill="x")

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        _centre(self)
        self.after(100, self._run)

    def _write(self, text: str, tag: str = ""):
        self.txt.config(state="normal")
        if tag:
            self.txt.insert("end", text, tag)
        else:
            self.txt.insert("end", text)
        self.txt.see("end")
        self.txt.config(state="disabled")
        self.update_idletasks()

    def _save_log(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")],
            initialfile="plex_dryrun_log.txt",
            parent=self,
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.txt.get("1.0", "end"))

    def _run(self):
        s           = self.settings
        input_path  = Path(s["input_dir"])
        output_path = Path(s["output_dir"])
        media_type  = s["media_type"]

        self._write(f"Input  : {input_path}\n", self.T_SOURCE)
        self._write(f"Output : {output_path}\n", self.T_SOURCE)
        self._write(f"Type   : {media_type}   Action: {s['file_action']} (DRY RUN)\n")
        self._write("─" * 95 + "\n", self.T_SEP)

        if not input_path.exists():
            self._write(f"ERROR: Input folder not found: {input_path}\n", self.T_WARN)
            self.status_var.set("Error — input folder not found.")
            return

        count_ok = count_skip = 0

        for root, _dirs, files in os.walk(input_path):
            for file in files:
                if Path(file).suffix.lower() not in VIDEO_EXTENSIONS:
                    continue

                video_path = Path(root) / file
                rel_folder = os.path.relpath(root, input_path)

                self._write("\n▶ SOURCE\n", self.T_HEAD)
                self._write(f"  Folder : {rel_folder}\n", self.T_SOURCE)
                self._write(f"  File   : {file}\n",        self.T_SOURCE)

                companions = find_companions(video_path)
                if companions:
                    self._write(f"  Companions ({len(companions)}):\n", self.T_HEAD)
                    for c, _ in companions:
                        self._write(f"    {c.name}\n", self.T_COMP)

                self.status_var.set(f"Querying DeepSeek: {file}")
                metadata, ds_prompt, ds_raw, ds_err = ask_deepseek_to_parse(rel_folder, file, DEEPSEEK_API_KEY)

                self._write("▶ AI REQUEST\n", self.T_HEAD)
                self._write(ds_prompt + "\n", self.T_AI_SEND)

                if not metadata:
                    self._write("▶ AI RESPONSE (error)\n", self.T_HEAD)
                    self._write(f"  ERROR: {ds_err or 'Empty/invalid response'}\n", self.T_WARN)
                    count_skip += 1
                    self._write("─" * 95 + "\n", self.T_SEP)
                    continue

                self._write("▶ AI RESPONSE (raw)\n", self.T_HEAD)
                self._write(ds_raw.strip() + "\n", self.T_AI_RECV)
                title_en = metadata.get("title_en") or "Unknown"
                title_ru = metadata.get("title_ru") or ""
                year     = metadata.get("year")
                season   = metadata.get("season")
                episode  = metadata.get("episode")

                self._write("▶ DEEPSEEK PARSED\n", self.T_HEAD)
                line = f"  Title  : {title_en}"
                if title_ru: line += f"  /  {title_ru}"
                line += f"\n  Year   : {year or '—'}   Season: {season or '—'}   Episode: {episode or '—'}\n"
                self._write(line, self.T_PARSE)

                self.status_var.set(f"TMDB lookup: {title_en}")
                tmdb_id, imdb_id = get_tmdb_ids(title_en, year, media_type)

                _, stem, target_dir, new_video = build_names(
                    metadata, media_type, tmdb_id, video_path.suffix, output_path
                )

                self._write("▶ PROPOSED RENAME\n", self.T_HEAD)
                self._write(f"  Folder : {target_dir}\n",         self.T_TARGET)
                self._write(f"  Video  : {new_video or file}\n",  self.T_TARGET)
                if stem and companions:
                    for c, csuf in companions:
                        self._write(f"  Companion: {stem}{csuf}\n", self.T_COMP)

                self._write("▶ PLEX MATCHING / SCRAPER TEST\n", self.T_HEAD)
                kind = "tv" if media_type == "tv" else "movie"

                # Always show clickable TMDB search URL (no key needed)
                import urllib.parse
                search_q = urllib.parse.quote(re.sub(r"[:\-]", " ", title_en).strip())
                year_q   = f"&year={year}" if year else ""
                self._write(f"  Search : https://www.themoviedb.org/search?query={search_q}{year_q}\n", self.T_TMDB)

                if TMDB_API_KEY:
                    top3 = get_tmdb_top3(title_en, year, media_type)
                    if top3:
                        self._write("  ─── TMDB top results (what Plex scraper sees) ───\n", self.T_HEAD)
                        for rank, hit in enumerate(top3, 1):
                            name_match = hit["name"].lower().replace(":", " -").replace("-", " ").strip() ==                                          title_en.lower().replace("-", " ").strip()
                            year_match = str(hit["year"]) == str(year) if year else True
                            ok = name_match and year_match
                            tag   = self.T_MATCH if ok else (self.T_TMDB if rank == 1 else self.T_SOURCE)
                            mark  = "  ✓ MATCH" if ok else ("  ≈ likely" if rank == 1 else "")
                            self._write(
                                f"  [{rank}] {hit['name']} ({hit['year']})  id={hit['id']}{mark}\n", tag
                            )
                        # Pinned id
                        if tmdb_id:
                            self._write(f"  Pin    : https://www.themoviedb.org/{kind}/{tmdb_id}\n", self.T_TMDB)
                            if imdb_id:
                                self._write(f"  IMDB   : https://www.imdb.com/title/{imdb_id}/\n", self.T_IMDB)
                        # Warn if top result doesn't match
                        if top3 and not (top3[0]["name"].lower().replace(":", " -").replace("-", " ").strip() ==
                                         title_en.lower().replace("-", " ").strip()):
                            self._write("  ⚠ Top TMDB result name differs — Plex may pick wrong show!\n", self.T_MISS)
                    else:
                        self._write("  ✗ Not found on TMDB — Plex auto-scrape will fail.\n", self.T_MISS)
                    self._write("    → Fix in Plex: right-click episode → Fix Incorrect Match\n", self.T_WARN)
                    self._write("    → Or add the show to TMDB at https://www.themoviedb.org/tv/new\n", self.T_WARN)
                else:
                    self._write("  Add TMDB_API_KEY to .env to enable scraper test\n", self.T_WARN)

                count_ok += 1
                self._write("─" * 95 + "\n", self.T_SEP)

        self._write(f"\nDone.  {count_ok} video(s) would be renamed,  {count_skip} skipped.\n",
                self.T_HEAD)
        self.status_var.set(f"Finished — {count_ok} OK, {count_skip} skipped. No files changed.")


# ===================== LIVE ORGANIZER =================

def organize_media(settings: dict) -> None:
    input_path  = Path(settings["input_dir"])
    output_path = Path(settings["output_dir"])
    media_type  = settings["media_type"]
    file_action = settings["file_action"]
    transfer    = shutil.copy2 if file_action == "copy" else shutil.move

    if not input_path.exists():
        print(f"[-] Input folder does not exist: {input_path}")
        return
    output_path.mkdir(parents=True, exist_ok=True)

    for root, _dirs, files in os.walk(input_path):
        for file in files:
            if Path(file).suffix.lower() not in VIDEO_EXTENSIONS:
                continue

            video_path = Path(root) / file
            rel_folder = os.path.relpath(root, input_path)
            print(f"\n[+] Processing: {rel_folder} / {file}")

            metadata, _p, _r, _e = ask_deepseek_to_parse(rel_folder, file, DEEPSEEK_API_KEY)
            if not metadata:
                print(f"[-] Skipping '{file}' — parse failed.")
                continue

            episode_end = metadata.get("episode_end")

            tmdb_id, imdb_id = get_tmdb_ids(
                metadata.get("title_en") or "Unknown", metadata.get("year"), media_type
            )

            _, stem, target_dir, new_video = build_names(
                metadata, media_type, tmdb_id, video_path.suffix, output_path
            )
            if stem and episode_end and metadata.get("episode"):
                title_part = stem.split(" - S")[0]
                s_num = metadata.get("season") or 1
                stem      = f"{title_part} - S{s_num:02d}E{int(metadata['episode']):02d}E{int(episode_end):02d}"
                new_video = stem + video_path.suffix
            target_dir.mkdir(parents=True, exist_ok=True)

            if imdb_id:
                print(f"    IMDB   -> https://www.imdb.com/title/{imdb_id}/")

            new_video_name = new_video or file
            target_file    = target_dir / new_video_name
            print(f"    Target -> {target_file}")
            transfer(str(video_path), str(target_file))
            print(f"[✓] {'Copied' if file_action=='copy' else 'Moved'}: {new_video_name}")

            if stem:
                for c_path, c_suf in find_companions(video_path):
                    c_new = target_dir / f"{stem}{c_suf}"
                    print(f"    Companion -> {c_new.name}")
                    transfer(str(c_path), str(c_new))
                    print(f"[✓] {'Copied' if file_action=='copy' else 'Moved'}: {c_new.name}")


# ===================== ENTRY POINT ====================

if __name__ == "__main__":
    # Single Tk root — hidden. Both dialogs are Toplevel children.
    root = tk.Tk()
    root.withdraw()

    dlg = SettingsDialog(root)
    root.wait_window(dlg)
    settings = dlg.result

    if settings is None:
        print("[INFO] Cancelled.")
        root.destroy()
    elif settings["dry_run"]:
        results = ResultsWindow(root, settings)
        root.wait_window(results)
        root.destroy()
    else:
        root.destroy()
        print("\n[INFO] Starting with settings:")
        for k, v in settings.items():
            print(f"       {k}: {v}")
        print()
        organize_media(settings)
        print("\n[INFO] Done.")
