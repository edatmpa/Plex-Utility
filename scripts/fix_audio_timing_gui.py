"""Fix Audio Location window - the main way to use scripts\\fix_audio_timing.py (fix_audio_location.bat opens it).

Films that stall on the Shield ("network too slow"): some MP4 files store all the picture first and the whole
soundtrack after it. The fix rewrites picture and sound unchanged with both mixed together (work file in
'<library folder> (mp4 fixed)'), then the fixed file takes the original's place (owner, 24 Oct: "just work from
original to fix"). The original is not deleted: it moves to '<library folder> (mp4 originals)'.

Every button runs fix_audio_timing.py (scan / fix / one). Scan, dry runs and checks show their output in the window.
'Fix in the background' starts its own minimised console window at lowest priority, so it keeps running when this
window is closed (close that console to stop; a new start skips copies already made).
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import plex_config as pc  # noqa: E402
import fix_audio_timing as mf  # noqa: E402

SCRIPT = HERE / "fix_audio_timing.py"
REPORTS = pc.PROJECT_ROOT / "reports"
PLAN = REPORTS / "fix_audio_timing_plan.xlsx"
ALL = "★ ALL LIBRARIES (every film and TV library)"

BG, PANEL, HEAD, HEAD_SUB, ACCENT, GO, MUTED = ("#f3f5f8", "#ffffff", "#1f3b57", "#c9d6e3", "#2f6f9f", "#2e7d32",
                                                "#5f6b76")


def python_exe() -> str:
    """The console Python next to this one (pythonw has no output to show in the window)."""
    exe = Path(sys.executable)
    py = exe.with_name("python.exe")
    return str(py if py.exists() else exe)


def libraries() -> list:
    try:
        con = pc.connect_ro()
        try:
            return [r[0].strip() for r in con.execute(
                "SELECT name FROM library_sections WHERE section_type IN (1, 2) ORDER BY name")]
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - the window still opens; 'All' works
        return []


def is_locked(p: Path) -> bool:
    try:
        with open(p, "r+b"):
            return False
    except PermissionError:
        return True
    except FileNotFoundError:
        return False


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Fix Audio Location")
        self.geometry("900x760")
        self.minsize(760, 520)
        self.style()
        band = ttk.Frame(self, style="Head.TFrame", padding=(14, 10))
        band.pack(fill="x")
        ttk.Label(band, text="Fix Audio Location - films that stall on the Shield", style="Head.TLabel").pack(anchor="w")
        ttk.Label(band, style="HeadSub.TLabel", wraplength=860, justify="left",
                  text="Finds MP4 films whose sound is stored after the picture instead of mixed in, and makes "
                       "fixes them in place (nothing re-encoded, no quality loss; same folder, same name - Plex "
                       "keeps the film). The old file is kept in '<library folder> (mp4 originals)' until you "
                       "delete that folder.").pack(anchor="w")

        body = ttk.Frame(self, padding=(10, 8))
        body.pack(fill="x")
        lf = ttk.LabelFrame(body, text="Whole libraries", padding=8)
        lf.pack(fill="x")
        ttk.Label(lf, text="Library:").grid(row=0, column=0, sticky="w")
        self.lib = tk.StringVar(value=ALL)
        ttk.Combobox(lf, textvariable=self.lib, values=[ALL] + libraries(), state="readonly", width=40).grid(
            row=0, column=1, columnspan=3, sticky="w", padx=6)
        steps = (("1  Scan: make plan", self.scan, "Accent.TButton"),
                 ("2  Open plan in Excel", self.open_plan, "TButton"),
                 ("3  Fix: dry run", self.dry_run, "Accent.TButton"),
                 ("4  Fix in the background", self.fix_bg, "Go.TButton"))
        for i, (t, fn, st) in enumerate(steps):
            ttk.Button(lf, text=t, command=fn, style=st, width=24).grid(row=1, column=i, padx=(0, 6), pady=(8, 2))
        ttk.Label(lf, style="Muted.TLabel", wraplength=840, justify="left",
                  text="All buttons work on the library chosen above (or all); each library has its own plan "
                       "file ('fix_audio_timing_plan - <library>.xlsx'). "
                       "1 reads only each file's index (all libraries: about 15 minutes). Clear cases are marked Y. "
                       "2 Review in Excel: Y = fix, blank or n = leave alone; save with Ctrl+S and close Excel. "
                       "4 runs in its own minimised window at lowest priority and pauses while anyone watches Plex; "
                       "close that window to stop - starting again skips films already done."
                  ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        sf = ttk.LabelFrame(body, text="Only if needed: films fixed but not yet in place (e.g. playing at the time)",
                            padding=8)
        sf.pack(fill="x", pady=(8, 0))
        ttk.Button(sf, text="5  Put in place: dry run", command=self.swap_dry, style="Accent.TButton",
                   width=24).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(sf, text="Put in place: DO IT", command=self.swap_go, style="Go.TButton",
                   width=24).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(sf, text="Undo a swap…", command=self.swap_undo, width=16).grid(row=0, column=2, padx=(0, 6))
        ttk.Label(sf, style="Muted.TLabel", wraplength=840, justify="left",
                  text="Fixing already puts each film in place. These buttons catch the rest: films fixed earlier "
                       "or that were in use. 'Undo a swap…' brings the old files back from '(mp4 originals)'. "
                       "After fixing, scan the library in Plex; delete the '(mp4 originals)' folder yourself once "
                       "the films play well."
                  ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

        of = ttk.LabelFrame(body, text="One film", padding=8)
        of.pack(fill="x", pady=(8, 0))
        self.film = tk.StringVar()
        ttk.Entry(of, textvariable=self.film, width=78).grid(row=0, column=0, columnspan=4, sticky="we")
        ttk.Button(of, text="File…", command=self.pick_file).grid(row=0, column=4, padx=(6, 0))
        ttk.Button(of, text="Folder…", command=self.pick_folder).grid(row=0, column=5, padx=(6, 0))
        ttk.Button(of, text="Check", command=self.check_one, style="Accent.TButton", width=14).grid(
            row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(of, text="Fix now", command=self.fix_one, style="Go.TButton", width=14).grid(
            row=1, column=1, sticky="w", padx=6, pady=(8, 0))
        self.force = tk.BooleanVar()
        ttk.Checkbutton(of, text="fix even if it looks fine", variable=self.force).grid(
            row=1, column=2, sticky="w", pady=(8, 0))
        of.columnconfigure(3, weight=1)

        bar = ttk.Frame(self, padding=(10, 0, 10, 8))
        bar.pack(fill="x", side="bottom")
        self.status = ttk.Label(bar, text="Ready.", style="Muted.TLabel")
        self.status.pack(side="left")
        ttk.Button(bar, text="Close", command=self.close).pack(side="right")
        ttk.Button(bar, text="Open reports folder", command=self.open_reports).pack(side="right", padx=6)
        ttk.Button(bar, text="Open newest log", command=self.open_log).pack(side="right")
        self.stop_btn = ttk.Button(bar, text="■ Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="right", padx=6)

        lw = ttk.Frame(self, padding=(10, 0, 10, 6))
        lw.pack(fill="both", expand=True)
        self.out = tk.Text(lw, wrap="none", font=("Consolas", 9), bg=PANEL, relief="flat")
        sb = ttk.Scrollbar(lw, command=self.out.yview)
        self.out.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.out.pack(fill="both", expand=True)

        self.proc, self.lines = None, queue.Queue()  # the reader thread hands lines over here (no Tk calls from it)
        self.after(100, self.poll)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.log("Start with '1  Scan: make plan', or check a single film below.\n")

    def style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        self.configure(bg=BG)
        s.configure(".", background=BG, font=("Segoe UI", 9))
        for name in ("TFrame", "TLabel", "TCheckbutton", "TLabelframe"):
            s.configure(name, background=BG)
        s.configure("Muted.TLabel", foreground=MUTED)
        s.configure("TLabelframe.Label", background=BG, foreground=HEAD, font=("Segoe UI", 9, "bold"))
        s.configure("TButton", padding=(8, 3))
        s.configure("Accent.TButton", background=ACCENT, foreground="white", bordercolor=ACCENT)
        s.map("Accent.TButton", background=[("active", "#24587f"), ("disabled", "#9db4c7")])
        s.configure("Go.TButton", background=GO, foreground="white", bordercolor=GO, font=("Segoe UI", 9, "bold"))
        s.map("Go.TButton", background=[("active", "#1b5e20"), ("disabled", "#9fc3a2")])
        s.configure("Head.TFrame", background=HEAD)
        s.configure("Head.TLabel", background=HEAD, foreground="white", font=("Segoe UI", 15, "bold"))
        s.configure("HeadSub.TLabel", background=HEAD, foreground=HEAD_SUB)

    # ---------------------------------------------------------------- running fix_audio_timing.py
    def log(self, text):
        self.out.insert("end", text)
        self.out.see("end")

    def poll(self):
        try:
            while True:
                item = self.lines.get_nowait()
                if isinstance(item, tuple):   # ("done", return code, on_done)
                    self.finished(*item[1:])
                elif item.startswith("    progress"):   # one progress line, updated in place
                    if self.out.get("end-2l", "end-1l").startswith("    progress"):
                        self.out.delete("end-2l", "end-1l")
                    self.log(item)
                    self.status.config(text="Copying: " + item.strip()[len("progress"):].strip())
                else:
                    self.log(item)
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def run(self, args, what, on_done=None):
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("Busy", "A step is still running.")
            return
        self.log(f"\n> fix_audio_timing {' '.join(args)}\n")
        self.status.config(text=f"Running: {what} …")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", PYTHONNOUSERSITE="1")
        self.proc = subprocess.Popen([python_exe(), str(SCRIPT), *args], cwd=str(pc.PROJECT_ROOT), env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", errors="replace",
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.stop_btn.config(state="normal")

        def read(p=self.proc):
            for line in p.stdout:
                self.lines.put(line)
            p.wait()
            self.lines.put(("done", p.returncode, on_done))

        threading.Thread(target=read, daemon=True).start()

    def finished(self, code, on_done):
        self.stop_btn.config(state="disabled")
        if getattr(self, "stopping", False):
            self.stopping = False
            self.status.config(text="Stopped.")
            self.log("\n(stopped by you)\n")
            return
        self.status.config(text="Finished." if code == 0 else f"Stopped with a problem (code {code}) - see above.")
        if on_done and code == 0:
            on_done()

    def stop(self):
        if self.proc and self.proc.poll() is None and messagebox.askyesno("Stop?", "Stop the running step?"):
            self.stopping = True
            self.kill()

    def kill(self):
        """End fix_audio_timing.py AND its ffmpeg (terminate alone would leave ffmpeg writing its .part copy)."""
        subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def plan(self) -> Path:
        """The plan of the chosen library ('fix_audio_timing_plan - <library>.xlsx'), or of all libraries."""
        return mf.plan_for("" if self.lib.get() == ALL else self.lib.get())

    def plan_closed(self) -> bool:
        for p in {self.plan(), PLAN}:   # fix may fall back on the all-libraries plan
            if p.exists() and (is_locked(p) or (REPORTS / ("~$" + p.name)).exists()):
                messagebox.showwarning("Plan is open", f"{p.name} is open in Excel.\n\n"
                                                       "Save it (Ctrl+S) and close Excel, then click again.")
                return False
        return True

    # ---------------------------------------------------------------- whole libraries
    def scan(self):
        if not self.plan_closed():
            return
        p = self.plan()
        if p.exists() and not messagebox.askyesno(
                "New plan", f"Make a new plan for {self.lib.get().replace(ALL, 'all libraries')}?\n\n"
                            f"{p.name} moves to reports\\old. Y / n you typed in it are carried over."):
            return
        self.run(["scan"] + self.lib_args(), "scan", on_done=self.open_plan)

    def open_plan(self):
        p = self.plan()
        if not p.exists():
            if messagebox.askyesno("No plan yet", f"There is no plan for {self.lib.get().replace(ALL, 'all libraries')}"
                                                  " yet.\n\nScan it now?"):
                self.scan()
            return
        os.startfile(p)

    def lib_args(self) -> list:
        lib = self.lib.get()
        return [] if lib == ALL else ["--library", lib]

    def dry_run(self):
        if self.plan_closed():
            self.run(["fix"] + self.lib_args(), "dry run")

    def fix_bg(self):
        if not self.plan().exists() and not PLAN.exists():
            messagebox.showinfo("No plan yet", "Click '1  Scan: make plan' first.")
            return
        if not self.plan_closed() or not messagebox.askyesno(
                "Fix in the background",
                f"Fix the Y rows of {self.lib.get().replace(ALL, 'all libraries')}?\n\nEach fixed film replaces "
                "its original in the same folder; the old file is kept in '(mp4 originals)'."
                "\n\nIt runs in its own minimised window (\"Fix Audio Location - "
                "running\") at lowest priority and pauses while anyone watches Plex. It keeps running when you "
                "close this window. Close that window to stop; starting again skips films already done.\n\n"
                "Tip: run '3  Fix: dry run' first to see how many films and how long."):
            return
        lib = " ".join(f'"{x}"' for x in self.lib_args())
        cmd = (f'start "Fix Audio Location - running (close to stop)" /low /min cmd /k '
               f'""{python_exe()}" "{SCRIPT}" fix {lib} --go"')
        subprocess.Popen(cmd, shell=True, cwd=str(pc.PROJECT_ROOT),
                         env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1"))
        self.status.config(text="Fixing in the background - see the minimised window 'Fix Audio Location - running'.")
        self.log("\nStarted in the background (minimised window 'Fix Audio Location - running'). "
                 "'Open newest log' shows the films done so far.\n")

    # ---------------------------------------------------------------- swap
    def swap_dry(self):
        self.run(["swap"] + self.lib_args(), "put in place: dry run")

    def swap_go(self):
        if messagebox.askyesno(
                "Put fixed copies in place",
                f"Put the fixed copies of {self.lib.get().replace(ALL, 'all libraries')} in place of the "
                "originals?\n\nEach original moves to '<library folder> (mp4 originals)' (kept, not deleted). "
                "Nobody should be watching Plex.\n\nTip: run '5  Put in place: dry run' first."):
            self.run(["swap"] + self.lib_args() + ["--go"], "putting fixed copies in place")

    def swap_undo(self):
        f = filedialog.askopenfilename(title="Swap log to undo", initialdir=str(REPORTS),
                                       filetypes=[("Swap logs", "fix_audio_timing_swap_*.csv")])
        if f and messagebox.askyesno("Undo", f"Put the originals back for every film in\n{Path(f).name}?"):
            self.run(["swap-undo", f, "--go"], "undo swap")

    # ---------------------------------------------------------------- one film
    def pick_file(self):
        f = filedialog.askopenfilename(title="Film file", filetypes=[("MP4 films", "*.mp4 *.m4v *.mov"),
                                                                      ("All files", "*.*")])
        if f:
            self.film.set(os.path.normpath(f))

    def pick_folder(self):
        d = filedialog.askdirectory(title="Film folder")
        if d:
            self.film.set(os.path.normpath(d))

    def one_args(self, go: bool):
        f = self.film.get().strip().strip('"')
        if not f:
            messagebox.showwarning("Film", "Choose a film file or folder first (File… / Folder…).")
            return None
        return ["one", f] + (["--force"] if self.force.get() else []) + (["--go"] if go else [])

    def check_one(self):
        args = self.one_args(False)
        if args:
            self.run(args, "check")

    def fix_one(self):
        args = self.one_args(True)
        if args and messagebox.askyesno("Fix now", "Fix this film now?\n\nIt takes a few minutes per film. The "
                                                   "fixed film replaces the original in the same folder; the old "
                                                   "file is kept in '(mp4 originals)'. ■ Stop cancels it (the "
                                                   "original then stays as it was)."):
            self.run(args, "fixing one film")

    # ---------------------------------------------------------------- bottom bar
    def open_log(self):
        logs = sorted(REPORTS.glob("fix_audio_timing_log_*.csv"), key=lambda p: p.stat().st_mtime)
        if logs:
            os.startfile(logs[-1])
        else:
            messagebox.showinfo("No log yet", "No film has been fixed yet.")

    def open_reports(self):
        REPORTS.mkdir(exist_ok=True)
        os.startfile(REPORTS)

    def close(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("A step is running", "A step is still running. Stop it and close?"):
                return
            self.kill()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
