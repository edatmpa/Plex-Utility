"""Plex Utility - the start window (owner, 1 Nov 2026: "make the interface in Plex Utility.bat to be GUI").

Read-only searches and reports run inside the window and show their output there. Tools that ask questions as
they go (DB repair, database management, the video tools, Movie Folder Org) open in their own console window,
exactly as from the old text menu ('Plex Utility.bat menu'). Fix Audio Location opens its own window.
Tk only (stdlib), same look as the Fix Audio Location window.
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plex_config as pc  # noqa: E402

ROOT = pc.PROJECT_ROOT
SCRIPTS = ROOT / "scripts"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
BG, PANEL, HEAD, HEAD_SUB, ACCENT, GO, WARN, MUTED = ("#f3f5f8", "#ffffff", "#1f3b57", "#c9d6e3", "#2f6f9f",
                                                      "#2e7d32", "#b23c17", "#5f6b76")
FIND_BY = {  # what the search box looks in -> plex_search.py arguments
    "Title": ["search"], "File path": ["find-file"], "Director": ["tag", "--type", "director"],
    "Actor": ["tag", "--type", "actor"], "Genre": ["tag", "--type", "genre"],
    "Collection": ["tag", "--type", "collection"], "Item id (details)": ["info"],
}
REPORTS = (("Library stats", ["stats"]), ("Recently added", ["recent", "--limit", "40"]),
           ("List libraries", ["list-sections"]), ("Duplicate files", ["duplicates"]),
           ("Orphaned parts", ["orphaned"]), ("Integrity check", ["check"]))
VIDEO = (("Subtitle audit", "audit", "Which film folders have external, built-in or no English subtitles"),
         ("Non-English subtitles", "nonengl", "MKV files whose only subtitles are not English"),
         ("Move files to folders", "move", "Each loose film into its own folder, Plex-standard name from TMDB"),
         ("Sync SRT to video", "sync", "Fix subtitle timing from the film's sound (Whisper)"),
         ("Match SRTs to videos", "match", "Find which subtitle belongs to which episode (AI)"),
         ("Movie Folder Org (AI)", "org", "Same, as its own tool (--old-style: AI translation + year)"))


def python_exe() -> str:
    """The console Python next to this one (pythonw has no output to show)."""
    exe = Path(sys.executable)
    py = exe.with_name("python.exe")
    return str(py if py.exists() else exe)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Plex Utility")
        self.geometry("1100x880")
        self.minsize(860, 600)
        self.style()
        self.proc, self.lines = None, queue.Queue()

        band = ttk.Frame(self, style="Head.TFrame", padding=(14, 10))
        band.pack(fill="x")
        ttk.Label(band, text="Plex Utility", style="Head.TLabel").pack(anchor="w")
        ttk.Label(band, style="HeadSub.TLabel", wraplength=1000, justify="left",
                  text="Tools for the Plex server on this PC. Searches and reports only read the Plex database "
                       "and are safe at any time. Buttons marked ▸ open the tool in its own window.").pack(anchor="w")

        body = ttk.Frame(self, padding=(10, 8))
        body.pack(fill="x")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        # -- search + reports (output here)
        sf = ttk.LabelFrame(body, text="Search the Plex library (read-only)", padding=8)
        sf.grid(row=0, column=0, columnspan=2, sticky="we")
        self.query = tk.StringVar()
        e = ttk.Entry(sf, textvariable=self.query, width=46)
        e.grid(row=0, column=0, sticky="we")
        e.bind("<Return>", lambda _e: self.search())
        self.by = tk.StringVar(value="Title")
        ttk.Combobox(sf, textvariable=self.by, values=list(FIND_BY), state="readonly", width=16).grid(
            row=0, column=1, padx=6)
        ttk.Button(sf, text="Search", style="Accent.TButton", command=self.search, width=12).grid(row=0, column=2)
        ttk.Label(sf, style="Muted.TLabel", text="  e.g. 47 Ronin  ·  V:\\Japanese\\Samurai\\Harakiri  ·  "
                                                 "Kurosawa (Director)  ·  12345 (Item id)").grid(row=0, column=3,
                                                                                                   sticky="w")
        rf = ttk.Frame(sf)
        rf.grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        for i, (label, args) in enumerate(REPORTS):
            ttk.Button(rf, text=label, command=lambda a=args, t=label: self.run_search(a, t)).grid(
                row=0, column=i, padx=(0, 6))
        ttk.Button(rf, text="CSV export (all films + episodes)", command=self.export).grid(row=0, column=len(REPORTS),
                                                                                            padx=(12, 6))
        ttk.Button(rf, text="Open reports folder", command=lambda: os.startfile(ROOT / "reports")).grid(
            row=0, column=len(REPORTS) + 1)
        sf.columnconfigure(3, weight=1)

        # -- video tools (own console)
        vf = ttk.LabelFrame(body, text="Video files on the NAS", padding=8)
        vf.grid(row=1, column=0, sticky="nsew", pady=(8, 0), padx=(0, 5))
        for i, (label, key, text) in enumerate(VIDEO):
            ttk.Button(vf, text="▸ " + label, width=24, command=lambda k=key, t=label: self.video(k, t)).grid(
                row=i, column=0, sticky="w", pady=2)
            ttk.Label(vf, text=text, style="Muted.TLabel").grid(row=i, column=1, sticky="w", padx=6)
        ttk.Button(vf, text="▸ Tidy folder names", width=24, command=self.tidy).grid(
            row=len(VIDEO), column=0, sticky="w", pady=2)
        ttk.Label(vf, text="Dots in folder names become spaces (preview, log, undo)", style="Muted.TLabel").grid(
            row=len(VIDEO), column=1, sticky="w", padx=6)
        ttk.Button(vf, text="▸ Fix Audio Location", width=24, style="Accent.TButton",
                   command=self.fix_audio).grid(row=len(VIDEO) + 1, column=0, sticky="w", pady=(6, 2))
        ttk.Label(vf, text="Films that stall on the Shield (\"network too slow\") - opens its window",
                  style="Muted.TLabel").grid(row=len(VIDEO) + 1, column=1, sticky="w", padx=6, pady=(6, 2))

        # -- database care (own console)
        df = ttk.LabelFrame(body, text="Plex database care", padding=8)
        df.grid(row=1, column=1, sticky="nsew", pady=(8, 0), padx=(5, 0))
        ttk.Button(df, text="▸ Health & Repair", width=24, command=self.repair).grid(row=0, column=0, sticky="w",
                                                                                    pady=2)
        ttk.Label(df, style="Muted.TLabel", wraplength=300, justify="left",
                  text="Check the database, rebuild it if damaged, list files that are gone from the NAS, "
                       "empty Plex's trash. Each step asks first.").grid(row=0, column=1, sticky="w", padx=6)
        ttk.Button(df, text="▸ Database management", width=24, style="Warn.TButton", command=self.manage).grid(
            row=1, column=0, sticky="w", pady=(10, 2))
        ttk.Label(df, style="Muted.TLabel", wraplength=300, justify="left",
                  text="Delete a stale media part or old backups. STOP PLEX FIRST (the tool checks). "
                       "Backup + dry run + typed YES.").grid(row=1, column=1, sticky="w", padx=6, pady=(10, 2))
        ttk.Button(df, text="▸ Old text menu", width=24, command=self.text_menu).grid(row=2, column=0, sticky="w",
                                                                                     pady=(10, 2))
        ttk.Label(df, style="Muted.TLabel", text="Everything as before, in a console").grid(
            row=2, column=1, sticky="w", padx=6, pady=(10, 2))
        # one library pick list for the three library tools below (owner, 19 Nov 2026)
        ttk.Label(df, text="Library:").grid(row=3, column=0, sticky="e", pady=(12, 2))
        try:
            import plex_health
            libs = ["All libraries"] + [n for _, n in plex_health.libraries()]
        except Exception:  # noqa: BLE001 - no Plex database on this PC
            libs = ["All libraries"]
        self.dead_lib = ttk.Combobox(df, values=libs, state="readonly", width=28)
        self.dead_lib.set(libs[0])
        self.dead_lib.grid(row=3, column=1, sticky="w", padx=6, pady=(12, 2))
        for i, (text, cmd, note, style) in enumerate((
                ("▸ Update library (scan)", self.scan_lib, "Plex looks for new, changed and removed files; "
                 "waits until done", "Accent.TButton"),
                ("▸ Find dead links", self.dead_links, "Films and subtitles whose file is gone (read-only)",
                 "TButton"),
                ("▸ Empty trash", self.empty_trash, "Removes films whose file is gone from Plex (asks first)",
                 "Warn.TButton")), 4):
            ttk.Button(df, text=text, width=24, style=style, command=cmd).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Label(df, style="Muted.TLabel", text=note).grid(row=i, column=1, sticky="w", padx=6)

        # -- output
        lw = ttk.Frame(self, padding=(10, 4, 10, 0))
        lw.pack(fill="both", expand=True)
        self.out = tk.Text(lw, wrap="none", font=("Consolas", 9), bg=PANEL, relief="flat")
        sb = ttk.Scrollbar(lw, command=self.out.yview)
        hs = ttk.Scrollbar(lw, orient="horizontal", command=self.out.xview)
        self.out.configure(yscrollcommand=sb.set, xscrollcommand=hs.set)
        sb.pack(side="right", fill="y")
        hs.pack(side="bottom", fill="x")
        self.out.pack(side="left", fill="both", expand=True)

        bar = ttk.Frame(self, padding=(10, 4, 10, 8))
        bar.pack(fill="x")
        self.status = ttk.Label(bar, text="Ready.")
        self.status.pack(side="left")
        self.stop_btn = ttk.Button(bar, text="■ Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="right")
        ttk.Button(bar, text="Clear", command=lambda: self.out.delete("1.0", "end")).pack(side="right", padx=6)
        e.focus_set()
        self.after(100, self.poll)

    def style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        self.configure(bg=BG)
        s.configure(".", background=BG, font=("Segoe UI", 9))
        for name in ("TFrame", "TLabel", "TLabelframe", "TCheckbutton", "TRadiobutton"):
            s.configure(name, background=BG)
        s.configure("Muted.TLabel", foreground=MUTED)
        s.configure("TLabelframe.Label", background=BG, foreground=HEAD, font=("Segoe UI", 9, "bold"))
        s.configure("TButton", padding=(8, 3))
        s.configure("Accent.TButton", background=ACCENT, foreground="white", bordercolor=ACCENT)
        s.map("Accent.TButton", background=[("active", "#24587f"), ("disabled", "#9db4c7")])
        s.configure("Warn.TButton", background=WARN, foreground="white", bordercolor=WARN)
        s.map("Warn.TButton", background=[("active", "#8f2f12")])
        s.configure("Head.TFrame", background=HEAD)
        s.configure("Head.TLabel", background=HEAD, foreground="white", font=("Segoe UI", 15, "bold"))
        s.configure("HeadSub.TLabel", background=HEAD, foreground=HEAD_SUB)

    # ---------------------------------------------------------------- output in the window
    def log(self, text):
        self.out.insert("end", text)
        self.out.see("end")

    def poll(self):
        try:
            while True:
                item = self.lines.get_nowait()
                if isinstance(item, tuple):
                    self.finished(item[1])
                else:
                    self.log(item)
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def run(self, script, args, what):
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("Busy", "Something is still running - wait or press Stop.")
            return
        self.log(f"\n> {what}\n")
        self.status.config(text=f"Running: {what} …")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", PYTHONNOUSERSITE="1")
        self.proc = subprocess.Popen([python_exe(), str(SCRIPTS / script), *args], cwd=str(ROOT), env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        self.stop_btn.config(state="normal")

        def read(p=self.proc):
            for line in p.stdout:
                self.lines.put(line)
            p.wait()
            self.lines.put(("done", p.returncode))

        threading.Thread(target=read, daemon=True).start()

    def finished(self, code):
        self.stop_btn.config(state="disabled")
        self.status.config(text="Finished." if code == 0 else f"Finished with a problem (code {code}) - see above.")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True,
                           creationflags=NO_WINDOW)
            self.log("\n(stopped)\n")

    def run_search(self, args, what):
        self.run("plex_search.py", args, what)

    def search(self):
        q = self.query.get().strip().strip('"')
        if not q:
            messagebox.showinfo("Search", "Type what to look for first.")
            return
        by = self.by.get()
        if by.startswith("Item id") and not q.isdigit():
            messagebox.showinfo("Search", "An item id is a number (shown in the search results).")
            return
        self.run_search(FIND_BY[by] + [q], f"{by}: {q}")

    def export(self):
        self.run("export_report.py", [], "CSV export -> reports\\")

    def dead_links(self):
        lib = self.dead_lib.get()
        args = ["missing"] + (["--library", lib] if lib and lib != "All libraries" else [])
        self.run("plex_health.py", args, f"Dead links - {lib}")

    def scan_lib(self):
        lib = self.dead_lib.get() or "All libraries"
        self.run("plex_health.py", ["scan", "--library", "all" if lib == "All libraries" else lib],
                 f"Update library (scan) - {lib}")

    def empty_trash(self):
        import plex_health
        lib = self.dead_lib.get() or "All libraries"
        try:
            secs = [s for s in plex_health.chosen("all" if lib == "All libraries" else lib) if s["trash"]]
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Empty trash", f"Could not read the Plex database:\n{e}")
            return
        if not secs:
            messagebox.showinfo("Empty trash", f"Nothing in the trash for {lib}.\n\n(Run 'Update library' first "
                                               "if you just removed files.)")
            return
        lines = "\n".join(f"  {s['name']}: {s['trash']} item(s)" for s in secs)
        if not messagebox.askokcancel(
                "Empty trash",
                f"Plex will remove these items whose file is gone from the NAS:\n\n{lines}\n\n"
                "Their watch history and any changes you made to them (posters, titles) are lost.\n"
                "Skipped (with the reason shown): libraries whose folder can't be reached, libraries Plex is "
                "still scanning, and libraries where a film in the trash still has a file that plays.\n\n"
                "Tip: 'Find dead links' lists them first.\n\nEmpty the trash?", icon="warning"):
            return
        self.run("plex_health.py", ["trash", "--library", "all" if lib == "All libraries" else lib, "--yes"],
                 f"Empty trash - {lib}")

    # ---------------------------------------------------------------- tools in their own window
    def console(self, title, command):
        """Open a console window running `command` (a cmd line); it stays open for reading."""
        env = dict(os.environ, PYTHONNOUSERSITE="1")
        subprocess.Popen(f'cmd /k "title {title} & chcp 65001 >nul & {command}"', cwd=str(ROOT), env=env,
                         creationflags=NEW_CONSOLE)
        self.status.config(text=f"Opened in its own window: {title}")

    def video(self, key, title):
        self.console(title, f'"{python_exe()}" "{SCRIPTS / "vdo_util.py"}" {key}')

    def repair(self):
        self.console("Plex DB Health & Repair", f'call "{ROOT / "DBRepair" / "Windows" / "DBRepair-Windows-Launcher.bat"}"')

    def manage(self):
        if messagebox.askokcancel("Database management",
                                  "These tools change the Plex database.\n\nStop Plex Media Server first "
                                  "(the tool checks and refuses while it runs). Every change makes a backup "
                                  "and asks you to type YES.\n\nOpen it?"):
            self.console("Plex database management", f'"{python_exe()}" "{SCRIPTS / "plex_db.py"}" --interactive')

    def tidy(self):
        import tidy_folder_names
        tidy_folder_names.TidyWindow(self)

    def fix_audio(self):
        subprocess.Popen(["cmd", "/c", str(ROOT / "fix_audio_location.bat")], cwd=str(ROOT), creationflags=NO_WINDOW)
        self.status.config(text="Fix Audio Location window opened.")

    def text_menu(self):
        self.console("Plex Tools", f'call "{ROOT / "Plex Utility.bat"}" menu')


if __name__ == "__main__":
    App().mainloop()
