"""Tidy folder names - replace the dots in folder names with spaces (owner, 1 Nov 2026).

    The.Last.Samurai.2003.1080p   ->  The Last Samurai 2003 1080p
    Mr.Smith.Goes.to.Washington   ->  Mr. Smith Goes to Washington
    S.W.A.T.2003                  ->  S.W.A.T. 2003

Only the folders directly inside the chosen folder are renamed (the film folders of a library); files inside them
are not touched. Dots that belong in a name stay: abbreviations (Mr. Dr. St. Vol. vs. ...), initials (S.W.A.T.,
L.A.), '...' and short numbers (5.1, 2.0).

Renames in place, after a preview. Every run is logged in reports\\tidy_folder_names_<time>.csv; Undo reads that
log and puts the old names back. Skipped: a folder whose new name already exists, a folder in use, Synology/Windows
system folders (@eaDir, #recycle, $RECYCLE.BIN, System Volume Information).

Window: python tidy_folder_names.py  (also from the Plex Utility window). Stdlib only.
"""
import csv
import datetime as dt
import os
import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
LOG_FIELDS = ["time", "parent", "old", "new", "result"]
SYSTEM = {"@eadir", "#recycle", "$recycle.bin", "system volume information", "#snapshot"}
ABBREV = ("Mr|Mrs|Ms|Dr|St|Jr|Sr|Vol|vs|No|Pt|Co|Ltd|Bros|Mt|Ft|Lt|Sgt|Capt|Col|Gen|Prof|Rev|Ave|Inc|Feat|ft|Ep|Op")
KEEP = "\x00"  # a dot that stays

REMINDER = ("After renaming folders that belong to a Plex library:\n"
            "  1. In Plex, scan that library (library menu ⋯ → Scan Library Files).\n"
            "  2. Then refresh it (⋯ → Refresh All Metadata), so Plex finds the subtitles under the new names - "
            "otherwise a film can refuse to play.\n"
            "Posters that are LOCKED in Plex (every poster set by the PlexRename tools is locked) are not changed by "
            "the refresh. Unlocked posters normally stay too, but Plex may pick another one for them.")


def tidy(name: str) -> str:
    """The folder name with its dots turned into spaces, keeping dots that belong in the name."""
    s = name
    s = s.replace("...", KEEP * 3)
    s = re.sub(r"(?<![A-Za-z0-9])(?:[A-Za-z]\.){2,}", lambda m: m.group(0).replace(".", KEEP), s)  # S.W.A.T. L.A.
    s = re.sub(rf"(?<![A-Za-z])({ABBREV})\.(?=.)", lambda m: m.group(1) + KEEP + " ", s, flags=re.I)  # Mr. Vol.
    s = re.sub(r"(?<!\d)(\d)\.(\d)(?!\d)", rf"\1{KEEP}\2", s)  # 5.1  2.0
    s = s.replace(".", " ").replace(KEEP, ".")
    s = re.sub(r"((?<![A-Za-z0-9])(?:[A-Za-z]\.){2,})(?=[A-Za-z0-9])", r"\1 ", s)  # 'S.W.A.T.2003' -> 'S.W.A.T. 2003'
    s = " ".join(s.split())
    return s.rstrip(". ")  # Windows does not allow a name ending in a dot or space


def plan(parent: Path) -> list:
    """[(folder, new name, note)] for every subfolder whose name has a dot. note '' = will be renamed."""
    names = {p.name.casefold() for p in parent.iterdir()}
    out = []
    for d in sorted((p for p in parent.iterdir() if p.is_dir()), key=lambda p: p.name.casefold()):
        if "." not in d.name or d.name.casefold() in SYSTEM:
            continue
        new = tidy(d.name)
        if new == d.name:
            out.append((d, new, "nothing to change (only dots that belong in the name)"))
        elif not new:
            out.append((d, new, "skipped: the name would be empty"))
        elif new.casefold() != d.name.casefold() and new.casefold() in names:
            out.append((d, new, "skipped: a folder with the new name already exists"))
        elif len(str(parent / new)) > 240:
            out.append((d, new, "skipped: the path would be too long"))
        else:
            out.append((d, new, ""))
    return out


def write_log(rows: list) -> Path:
    REPORTS.mkdir(exist_ok=True)
    log = REPORTS / f"tidy_folder_names_{dt.datetime.now():%Y-%m-%d_%H%M%S}.csv"
    with open(log, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return log


def rename_all(parent: Path, items: list, say=print):
    """Rename the items that have no note. Returns (renamed, problems, log path)."""
    rows, ok, bad = [], 0, 0
    for d, new, note in items:
        if note:
            continue
        t = dt.datetime.now().isoformat(timespec="seconds")
        try:
            os.rename(d, parent / new)
            rows.append(dict(time=t, parent=str(parent), old=d.name, new=new, result="ok"))
            ok += 1
            say(f"  renamed: {d.name}  ->  {new}")
        except OSError as e:
            rows.append(dict(time=t, parent=str(parent), old=d.name, new=new, result=f"failed: {e.strerror or e}"))
            bad += 1
            say(f"  FAILED : {d.name}  ({e.strerror or e} - in use?)")
    return ok, bad, write_log(rows) if rows else None


def undo(log: Path, say=print):
    """Put the old names back for every 'ok' row of a log (newest first). Returns (put back, problems)."""
    with open(log, encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["result"] == "ok"]
    ok = bad = 0
    out = []
    for r in reversed(rows):
        parent = Path(r["parent"])
        src, dst = parent / r["new"], parent / r["old"]
        t = dt.datetime.now().isoformat(timespec="seconds")
        if not src.is_dir():
            say(f"  not found (renamed again or moved?): {r['new']}")
            bad += 1
            out.append(dict(time=t, parent=r["parent"], old=r["new"], new=r["old"], result="failed: not found"))
            continue
        if dst.exists() and dst.name.casefold() != src.name.casefold():
            say(f"  old name taken by another folder, left as is: {r['new']}")
            bad += 1
            out.append(dict(time=t, parent=r["parent"], old=r["new"], new=r["old"], result="failed: old name taken"))
            continue
        try:
            os.rename(src, dst)
            ok += 1
            say(f"  put back: {r['new']}  ->  {r['old']}")
            out.append(dict(time=t, parent=r["parent"], old=r["new"], new=r["old"], result="undo ok"))
        except OSError as e:
            bad += 1
            say(f"  FAILED : {r['new']}  ({e.strerror or e})")
            out.append(dict(time=t, parent=r["parent"], old=r["new"], new=r["old"], result=f"failed: {e}"))
    if out:  # an undo is logged too, but never as 'ok' rows - undoing the undo log changes nothing
        write_log(out)
    return ok, bad


# ------------------------------------------------------------------------------------------- window
class TidyWindow(tk.Toplevel):
    def __init__(self, master=None):
        super().__init__(master)
        self.title("Tidy folder names")
        self.geometry("1000x720")
        self.minsize(760, 480)
        bg = "#f3f5f8"
        self.configure(bg=bg)
        s = ttk.Style(self)
        s.configure("Note.TLabel", background="#fff4d6", foreground="#5a4300")
        s.configure("TidyHead.TLabel", background="#1f3b57", foreground="white", font=("Segoe UI", 13, "bold"))
        s.configure("TidyGo.TButton", background="#2e7d32", foreground="white", bordercolor="#2e7d32")
        s.map("TidyGo.TButton", background=[("active", "#1f5d23"), ("disabled", "#9dbf9f")])
        ttk.Label(self, text="  Tidy folder names - dots become spaces", style="TidyHead.TLabel",
                  padding=(10, 8)).pack(fill="x")

        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="Folder:").pack(side="left")
        self.folder = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.folder)
        e.pack(side="left", fill="x", expand=True, padx=6)
        e.bind("<Return>", lambda _e: self.preview())
        ttk.Button(top, text="Browse…", command=self.browse).pack(side="left")
        ttk.Button(top, text="1  Preview", command=self.preview).pack(side="left", padx=(12, 6))
        self.go_btn = ttk.Button(top, text="2  Rename now", style="TidyGo.TButton", command=self.go,
                                 state="disabled")
        self.go_btn.pack(side="left")
        ttk.Button(top, text="Undo a run…", command=self.undo).pack(side="left", padx=(12, 0))
        ttk.Label(self, padding=(10, 0), foreground="#5f6b76", background=bg,
                  text="Renames the folders directly inside the chosen folder (e.g. the film folders of a library). "
                       "Files inside are not touched. Dots that belong in a name stay (Mr., S.W.A.T., 5.1, …)."
                  ).pack(fill="x")

        mid = ttk.Frame(self, padding=(10, 6))
        mid.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(mid, columns=("old", "new", "note"), show="headings")
        for c, t, w in (("old", "Folder now", 360), ("new", "New name", 360), ("note", "Note", 220)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.tag_configure("skip", foreground="#8a5a00")
        self.tree.tag_configure("same", foreground="#8a949e")
        self.tree.tag_configure("done", foreground="#2e7d32")
        sb = ttk.Scrollbar(mid, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        ttk.Label(self, text=REMINDER, style="Note.TLabel", padding=10, wraplength=960, justify="left").pack(
            fill="x", padx=10, pady=(0, 6))
        self.status = ttk.Label(self, text="Pick a folder, then 1 Preview.", padding=(10, 0, 10, 8), background=bg)
        self.status.pack(fill="x")
        self.items, self.parent_dir = [], None
        e.focus_set()

    def browse(self):
        p = filedialog.askdirectory(parent=self, title="Folder whose subfolders should be tidied",
                                    initialdir=self.folder.get() or None)
        if p:
            self.folder.set(os.path.normpath(p))
            self.preview()

    def preview(self):
        p = Path(self.folder.get().strip().strip('"'))
        if not p.is_dir():
            messagebox.showwarning("Folder", f"Not a folder (or the NAS is not reachable):\n{p}", parent=self)
            return
        self.config(cursor="watch")
        self.update()
        try:
            self.items, self.parent_dir = plan(p), p
        finally:
            self.config(cursor="")
        self.tree.delete(*self.tree.get_children())
        todo = [x for x in self.items if not x[2]]
        for d, new, note in self.items:
            tag = "" if not note else ("same" if note.startswith("nothing") else "skip")
            self.tree.insert("", "end", values=(d.name, new if not note.startswith("nothing") else "", note),
                             tags=(tag,))
        skipped = sum(1 for x in self.items if x[2].startswith("skipped"))
        self.status.config(text=f"{len(todo)} folder(s) will be renamed"
                                + (f", {skipped} skipped (see Note)" if skipped else "")
                                + (". Nothing to do." if not todo else ". Check the list, then 2 Rename now."))
        self.go_btn.config(state="normal" if todo else "disabled")

    def go(self):
        todo = [x for x in self.items if not x[2]]
        if not todo or not messagebox.askyesno(
                "Rename folders",
                f"Rename {len(todo)} folder(s) in\n{self.parent_dir}\n\n"
                "Every rename is logged; 'Undo a run…' puts the old names back.", parent=self):
            return
        lines = []
        ok, bad, log = rename_all(self.parent_dir, self.items, lines.append)
        self.preview()
        msg = f"Renamed: {ok}" + (f"   Failed: {bad} (in use? see the log)" if bad else "")
        self.status.config(text=msg + (f"   Log: {log.name}" if log else ""))
        messagebox.showinfo("Done", msg + "\n\n" + REMINDER, parent=self)

    def undo(self):
        log = filedialog.askopenfilename(parent=self, title="Choose the run to undo", initialdir=str(REPORTS),
                                         filetypes=[("Tidy folder names logs", "tidy_folder_names_*.csv")])
        if not log:
            return
        with open(log, encoding="utf-8-sig", newline="") as fh:
            n = sum(1 for r in csv.DictReader(fh) if r["result"] == "ok")
        if not n:
            messagebox.showinfo("Undo", "This log has no renames to undo (it may be an undo log itself).",
                                parent=self)
            return
        if not messagebox.askyesno("Undo", f"Put the old names back for {n} folder(s)?", parent=self):
            return
        ok, bad = undo(Path(log), lambda _s: None)
        messagebox.showinfo("Undo", f"Put back: {ok}" + (f"\nNot possible: {bad}" if bad else "") +
                            "\n\nScan + refresh the library in Plex afterwards.", parent=self)
        if self.parent_dir:
            self.preview()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":  # quick self-check of the name rule
        for a in sys.argv[2:]:
            print(f"{a!r:50} -> {tidy(a)!r}")
        sys.exit(0)
    root = tk.Tk()
    root.withdraw()
    w = TidyWindow(root)
    w.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
