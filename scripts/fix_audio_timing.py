"""
fix_audio_timing - find MP4 films whose sound is not mixed in with the picture, and make fixed copies.

Why (24 Oct 2026): the Nvidia Shield said "network too slow" on films of only 2 Mbit/s. In those MP4 files all
the picture is stored first and the whole soundtrack after it, so the player jumps between two places in the file
(~7 small requests a second) and starves. A remux with ffmpeg (-c copy, nothing re-encoded) stores picture and
sound mixed together and puts the index at the front.

  python fix_audio_timing.py scan [--library NAME] [--limit N]  -> reports\\fix_audio_timing_plan.xlsx  (read-only, ~15 min)
  python fix_audio_timing.py fix  [PLAN] [--go]                  -> fixed copies of the Y rows  (dry run without --go)
  python fix_audio_timing.py one  FILE_OR_FOLDER [--to DIR] [--force] [--go]   -> check one film and fix it now

The originals are never changed: each copy goes to NewFile, by default '<library folder> (mp4 fixed)\\<same path>',
so Plex does not see it until the owner swaps it in. 'fix --go' is made to run unattended in the background:
lowest CPU and disk priority, pauses while anyone is watching something on Plex, and a rerun skips copies that
are already done (close the window to stop).
"""

import argparse
import csv
import ctypes
import datetime as dt
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plex_config as pc

REPORTS = pc.PROJECT_ROOT / "reports"
PLAN = REPORTS / "fix_audio_timing_plan.xlsx"   # all libraries; one library = plan_for(name) (owner, 24 Oct)


def plan_for(library: str = "") -> Path:
    """The plan file: 'fix_audio_timing_plan.xlsx' for all libraries, 'fix_audio_timing_plan - <library>.xlsx' for one."""
    if not library.strip():
        return PLAN
    safe = "".join("_" if c in '<>:"/\\|?*' else c for c in library.strip())
    return REPORTS / f"fix_audio_timing_plan - {safe}.xlsx"
FIELDS = ["Apply", "Check", "Note", "What to do", "Library", "Folder", "File", "SizeMB", "Gap", "Index",
          "NewFile", "Source"]
WIDTHS = {"Apply": 7, "Check": 9, "Note": 48, "What to do": 52, "Library": 16, "Folder": 45, "File": 55,
          "SizeMB": 9, "Gap": 7, "Index": 7, "NewFile": 70, "Source": 70}
RED, YELLOW = "FFC7CE", "FFEB9C"
CLEAR, MILD = 0.20, 0.05   # picture and sound start this far apart (share of the file): clear case / mild case
SUFFIX = " (mp4 fixed)"
CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}
IDLE_PRIORITY_CLASS, BACKGROUND_BEGIN = 0x40, 0x00100000
WATCH_CHECK = 30           # seconds between "is anyone watching?" checks while copying


# ─── Reading the MP4 index (the moov box only) ────────────────────────────────

def _boxes(f, start, end):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        size, kind = struct.unpack(">I4s", f.read(8))
        hdr = 8
        if size == 1:
            size, hdr = struct.unpack(">Q", f.read(8))[0], 16
        elif size == 0:
            size = end - pos
        if size < hdr:
            return
        yield kind, pos + hdr, pos + size
        pos += size


def _tracks(f, start, end, out, cur=None):
    for kind, a, b in _boxes(f, start, end):
        if kind == b"trak":
            cur = {}
            out.append(cur)
        if kind in CONTAINERS:
            _tracks(f, a, b, out, cur)
        elif kind == b"hdlr" and cur is not None:
            f.seek(a + 8)
            cur["type"] = f.read(4).decode("latin1")
        elif kind in (b"stco", b"co64") and cur is not None and "first" not in cur:
            f.seek(a + 4)
            n = struct.unpack(">I", f.read(4))[0]
            if n:
                w, fmt = (4, ">I") if kind == b"stco" else (8, ">Q")
                cur["first"] = struct.unpack(fmt, f.read(w))[0]


def inspect(path) -> dict:
    """{'state': ok / bad / fragmented / one track / unreadable, 'gap': 0..1, 'index': start/end}."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        top = list(_boxes(f, 0, size))
        kinds = [k for k, _, _ in top]
        moov = next(((a, b) for k, a, b in top if k == b"moov"), None)
        if not moov:
            return dict(state="unreadable", gap=0, index="")
        tr = []
        _tracks(f, moov[0], moov[1], tr)
    index = "start" if b"mdat" not in kinds or kinds.index(b"moov") < kinds.index(b"mdat") else "end"
    if b"moof" in kinds:
        return dict(state="fragmented", gap=0, index=index)   # streamed in pieces, already interleaved
    av = {t["type"]: t for t in tr if t.get("type") in ("vide", "soun") and "first" in t}
    if len(av) < 2:
        return dict(state="one track", gap=0, index=index)
    gap = abs(av["vide"]["first"] - av["soun"]["first"]) / size
    return dict(state="bad" if gap > MILD else "ok", gap=gap, index=index)


# ─── scan -> plan ─────────────────────────────────────────────────────────────

def plex_files(library: str = "") -> list:
    """[(library, library folders, file)] for every MP4/MOV film and episode Plex knows (read-only DB)."""
    conn = pc.connect_ro()
    try:
        roots = {}
        for r in conn.execute("SELECT library_section_id, root_path FROM section_locations"):
            roots.setdefault(r[0], []).append(pc.openable(r[1]))
        rows = conn.execute(
            "SELECT DISTINCT s.id, s.name, mp.file FROM media_parts mp JOIN media_items mi ON mi.id = mp.media_item_id "
            "JOIN library_sections s ON s.id = mi.library_section_id WHERE mi.container IN ('mp4', 'mov') "
            "AND mi.deleted_at IS NULL AND mp.deleted_at IS NULL AND s.section_type IN (1, 2) "
            "ORDER BY s.name, mp.file").fetchall()
    finally:
        conn.close()
    return [(r[1].strip(), roots.get(r[0], []), pc.openable(r[2])) for r in rows
            if lib_match(library, r[1], {x[1].strip() for x in rows})]


def lib_match(wanted: str, name: str, names) -> bool:
    """--library: the library with exactly this name ('War' = only War, not War (China)); if no library has that
    exact name, every library whose name contains it. Empty = all."""
    w, n = wanted.strip().casefold(), name.strip().casefold()
    if not w:
        return True
    if any(x.strip().casefold() == w for x in names):
        return n == w
    return w in n


def new_name(file: str, roots: list) -> str:
    """'<library folder> (mp4 fixed)\\<path inside the library>'; outside any library folder: '<its folder> (mp4
    fixed)\\<name>' (never beside the original, where Plex would see a second version)."""
    for r in sorted(roots, key=len, reverse=True):
        r = r.rstrip("\\/")
        if file.casefold().startswith(r.casefold() + "\\"):
            return r + SUFFIX + file[len(r):]
    p = Path(file)
    return str(p.parent.with_name(p.parent.name + SUFFIX) / p.name)


def row_for(lib, roots, file) -> dict | None:
    p = Path(file)
    base = dict(Library=lib, Folder=p.parent.name, File=p.name, Source=file)
    try:
        r = inspect(file)
        size = os.path.getsize(file)
    except OSError as e:
        return dict(base, Apply="", Check="CHECK", Note=f"could not read the file ({e.strerror or e})",
                    **{"What to do": "Check the NAS is on and the file still exists; scan again. Leave blank."})
    base.update(SizeMB=f"{size / 1048576:.0f}", Index=r["index"])
    if r["state"] == "unreadable":
        return dict(base, Apply="", Check="CHECK", Note="no MP4 index found - the file may be damaged or unfinished",
                    **{"What to do": "Play it in Plex. If it does not play, replace the file. Leave blank."})
    if r["state"] != "bad":
        return None
    gap = f"{r['gap']:.0%}"
    base.update(Gap=gap, NewFile=new_name(file, roots))
    if r["gap"] >= CLEAR:
        return dict(base, Apply="Y", Check="", Note=f"sound stored {gap} of the file away from the picture "
                    "- players jump back and forth and stall", **{"What to do": "Nothing - a fixed copy is made "
                    "(type n to skip)."})
    return dict(base, Apply="", Check="review", Note=f"sound and picture slightly apart ({gap}) - "
                "usually plays, may stall on a slow device", **{"What to do": "Type Y if this film stalls; "
                "otherwise leave blank."})


def is_locked(p: Path) -> bool:
    """True when Excel (or anything) has the file open."""
    try:
        with open(p, "r+b"):
            return False
    except PermissionError:
        return True
    except FileNotFoundError:
        return False


def duration(gb: float) -> str:
    """'45 min' / '3.5 h' at the measured background speed (hours())."""
    h = hours(gb)
    return "-" if gb <= 0 else f"{max(1, round(h * 60))} min" if h < 1 else f"{h:.1f} h"


def library_times(rows: list) -> list:
    """[(library, Y films, GB, review films, review GB)] - biggest job first, total last."""
    acc = {}
    for r in rows:
        a = acc.setdefault(r.get("Library", ""), [0, 0.0, 0, 0.0])
        mb = float(r.get("SizeMB") or 0) / 1024
        if r.get("Apply", "").strip().upper() == "Y":
            a[0] += 1
            a[1] += mb
        elif r.get("Check") == "review":
            a[2] += 1
            a[3] += mb
    out = sorted(((lib, *v) for lib, v in acc.items() if v[0] or v[2]), key=lambda t: -t[2])
    if len(out) > 1:
        out.append(("TOTAL", *[sum(t[i] for t in out) for i in range(1, 5)]))
    return out


def write_plan(rows: list, plan: Path = PLAN, per_lib: list = ()) -> Path:
    from openpyxl import Workbook
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = "Plan"
    ws.append(FIELDS)
    for r in rows:
        ws.append([str(r.get(k, "") or "") for k in FIELDS])
    for c in ws[1]:
        c.font = Font(bold=True)
    n, last = len(rows) + 1, get_column_letter(len(FIELDS))
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:{last}{n}"
    for i, f in enumerate(FIELDS, 1):
        ws.column_dimensions[get_column_letter(i)].width = WIDTHS[f]
    ws.column_dimensions.group("L", "L", hidden=True, outline_level=1)   # Source: the tool needs it, the owner not
    fill = lambda rgb: PatternFill(start_color=rgb, end_color=rgb, fill_type="solid")
    rng = f"A2:{last}{max(n, 2)}"
    ws.conditional_formatting.add(rng, FormulaRule(formula=['AND($A2="",$B2="CHECK")'], fill=fill(RED)))
    ws.conditional_formatting.add(rng, FormulaRule(formula=['AND($A2="",$B2="review")'], fill=fill(YELLOW)))
    if per_lib:
        ts = wb.create_sheet("Time per library")
        ts.append(["Library", "Films to fix (Y)", "GB", "Estimated time", "Mild (review) films", "Extra time if Y"])
        for lib, n, gb, rn, rgb in per_lib:
            ts.append([lib, n, round(gb, 1), duration(gb), rn, duration(rgb) if rn else ""])
        for c in ts[1] + ts[ts.max_row] * (per_lib[-1][0] == "TOTAL"):
            c.font = Font(bold=True)
        for col, w in zip("ABCDEF", (30, 16, 9, 15, 18, 16)):
            ts.column_dimensions[col].width = w
        ts.append([])
        ts.append([f"Estimate at the measured speed ({GB_PER_HOUR} GB per hour, + 20 %), running in the background. "
                   "It pauses while someone watches Plex, so it can take longer."])
    lg = wb.create_sheet("How to review")
    for line in (["Column", "Meaning"],
                 ["Apply", "Y = make a fixed copy. Blank or n = leave the film alone."],
                 ["white / Y", "Clear case: the sound is stored far from the picture. A fixed copy is made."],
                 ["yellow / review", "Mild case: usually plays fine. Type Y only if the film stalls."],
                 ["red / CHECK", "The file could not be read. Read Note."],
                 ["Gap", "How far apart the sound and the picture start inside the file (share of the file)."],
                 ["Index", "Where the file's table of contents is: start (good) or end."],
                 ["NewFile", "Where the fixed copy goes. The original is never changed. You may edit this."],
                 ["", ""],
                 ["How the fix works", "ffmpeg copies picture, sound and subtitles unchanged (no quality loss) "
                                       "and stores them mixed together, with the table of contents at the front."],
                 ["Afterwards", "Play the copy. If it is good, replace the original with it yourself "
                                "(Plex keeps the film when the file name stays the same)."],
                 ["Tip", "Save with Ctrl+S (keep .xlsx) and close Excel before fixing."]):
        lg.append(line)
    lg["A1"].font = lg["B1"].font = Font(bold=True)
    lg.column_dimensions["A"].width, lg.column_dimensions["B"].width = 18, 110
    lg["A4"].fill, lg["A5"].fill = fill(YELLOW), fill(RED)
    REPORTS.mkdir(exist_ok=True)
    if is_locked(plan):
        sys.exit(f"{plan} is open in Excel - save and close it, then scan again.")
    if plan.exists():  # never deleted: the previous plan goes to reports\old
        old = REPORTS / "old"
        old.mkdir(exist_ok=True)
        plan.rename(old / f"{plan.stem}_{dt.datetime.fromtimestamp(plan.stat().st_mtime):%Y-%m-%d_%H%M%S}.xlsx")
        print(r"Previous plan    : moved to reports\old")
    wb.save(plan)
    return plan


def cmd_scan(a):
    files = plex_files(a.library)
    if a.limit:
        files = files[:a.limit]
    print(f"Checking {len(files)} MP4/MOV files known to Plex (reading only the index of each file)...")
    rows, done = [], 0
    with ThreadPoolExecutor(8) as pool:
        for r in pool.map(lambda t: row_for(*t), files):
            done += 1
            if r:
                rows.append(r)
            if done % 250 == 0:
                print(f"  {done}/{len(files)}  found {sum(1 for x in rows if x.get('Gap'))}", flush=True)
    plan = plan_for(a.library)
    if is_locked(plan):
        sys.exit(f"{plan} is open in Excel - save and close it, then scan again.")
    # your Y / n / blank choices carry over from the previous plan of the same files (this plan, else the
    # all-libraries plan) - only for the rows you changed; new findings get the tool's own default
    before = {}
    sources = [plan, PLAN] if a.library else [PLAN, *sorted(REPORTS.glob("fix_audio_timing_plan - *.xlsx"))]
    sources = sorted((p for p in dict.fromkeys(sources) if p.exists()), key=lambda p: p.stat().st_mtime,
                     reverse=True)   # the newest plan's choice wins
    for p in sources:
        if p.exists() and not is_locked(p):
            for r in read_plan(p):
                before.setdefault(pc.canonical(r.get("Source", "")).casefold(), r)
    kept = 0
    for r in rows:
        old = before.get(pc.canonical(r.get("Source", "")).casefold())
        if not old:
            continue
        tool_default = "Y" if not old.get("Check", "").strip() else ""   # clear case = Y, review/CHECK = blank
        if old.get("Apply", "").strip().upper() != tool_default:          # you changed it -> keep yours
            r["Apply"] = old.get("Apply", "").strip()
            kept += 1
    if kept:
        print(f"Your choices     : {kept} rows keep your Y/n from the previous plan")
    order = {"Y": 0, "": 1}
    rows.sort(key=lambda r: (order.get(r["Apply"], 2), r["Check"] == "CHECK", r["Library"], r["Source"]))
    per_lib = library_times(rows)
    out = write_plan(rows, plan, per_lib)
    print("\nTime to fix, per library (Y rows; mild 'review' rows only if you type Y):")
    print(f"  {'Library':<28}{'films':>6}{'GB':>8}{'time':>11}   {'+ review':>9}")
    for lib, n, gb, rn, rgb in per_lib:
        print(f"  {lib[:27]:<28}{n:>6}{gb:>8.1f}{duration(gb):>11}   {f'+{rn} ({duration(rgb)})' if rn else '':>9}")
    y = [r for r in rows if r["Apply"] == "Y"]
    gb = sum(float(r.get("SizeMB") or 0) for r in y) / 1024
    print(f"\nFiles checked    : {len(files)}" + (f"  (library: {', '.join(sorted({l for l, _, _ in files}))})"
                                                  if a.library else "  (all libraries)"))
    print(f"To fix (Y)       : {len(y)}  ({gb:.0f} GB, about {hours(gb):.0f} hours in the background)")
    print(f"Mild (review)    : {sum(1 for r in rows if r['Check'] == 'review')}")
    print(f"Unreadable       : {sum(1 for r in rows if r['Check'] == 'CHECK')}")
    print(f"Plan             : {out}")


# ─── fix (remux) in the background ────────────────────────────────────────────

GB_PER_HOUR = 12.5   # measured 24 Oct: 1.7 GB in 8 min 13 s (NAS -> NAS copy is slower than that test)


def hours(gb: float) -> float:
    return gb / GB_PER_HOUR * 1.2


def read_plan(path: Path) -> list:
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["Plan"]
    it = ws.iter_rows(values_only=True)
    head = [str(h or "").strip() for h in next(it)]
    rows = [{h: ("" if v is None else str(v)) for h, v in zip(head, vals)} for vals in it if any(vals)]
    wb.close()
    return rows


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:  # winget puts ffmpeg on the PATH of new windows only
        import winreg
        for key in ((winreg.HKEY_CURRENT_USER, "Environment"),
                    (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(*key) as k:
                    exe = shutil.which("ffmpeg", path=os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
            except OSError:
                pass
            if exe:
                break
    if not exe:
        sys.exit("ffmpeg not found - install it with: winget install Gyan.FFmpeg.Essentials")
    return exe


def low_priority():
    """This process: lowest CPU priority + Windows background mode (low disk and memory priority)."""
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32")
    k32.GetCurrentProcess.restype = wintypes.HANDLE   # pseudo handle -1: must not be cut to a 32-bit int
    k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    me = k32.GetCurrentProcess()
    k32.SetPriorityClass(me, BACKGROUND_BEGIN)
    k32.SetPriorityClass(me, IDLE_PRIORITY_CLASS)


def watching() -> int:
    """Number of films/episodes playing on Plex right now (0 if Plex can't be asked)."""
    try:
        return int(pc.plex_request("GET", "/status/sessions").get("size", 0))
    except Exception:
        return 0


def _suspend(proc, on: bool):
    from ctypes import wintypes
    nt, k32 = ctypes.WinDLL("ntdll"), ctypes.WinDLL("kernel32")
    k32.OpenProcess.restype = wintypes.HANDLE
    nt.NtSuspendProcess.argtypes = nt.NtResumeProcess.argtypes = k32.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k32.OpenProcess(0x0800, False, proc.pid)  # PROCESS_SUSPEND_RESUME
    if h:
        (nt.NtSuspendProcess if on else nt.NtResumeProcess)(h)
        k32.CloseHandle(h)


PROGRESS_EVERY = 15        # seconds between progress lines while copying


def progress(tmp: Path, total: int, started: float) -> str:
    """'progress 35%  0.7 of 2.0 GB  ~7 min left' from the size of the copy being written."""
    try:
        done = tmp.stat().st_size
    except OSError:
        return ""
    pct = min(done / total, 1) if total else 0
    secs = time.time() - started
    left = f"  ~{max(1, round(secs / pct * (1 - pct) / 60))} min left" if 0.02 < pct < 0.99 else ""
    if pct >= 0.99:   # all written; ffmpeg now moves the index to the front (rewrites the file once more)
        left = "  - finishing: moving the index to the front"
    return f"    progress {pct:4.0%}  {done / 1073741824:.1f} of {total / 1073741824:.1f} GB{left}"


def run_polite(cmd: list, tmp: Path = None, total: int = 0) -> subprocess.CompletedProcess:
    """Run ffmpeg at idle priority; freeze it while someone watches Plex, carry on when they stop.
    With tmp/total, prints how far the copy has got every PROGRESS_EVERY seconds (the window shows one line)."""
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace", creationflags=IDLE_PRIORITY_CLASS)
    err = []   # read stderr alongside, so a chatty ffmpeg can't block on a full pipe
    threading.Thread(target=lambda: err.extend(p.stderr), daemon=True).start()
    paused, started, last_watch = False, time.time(), time.time()
    while True:
        try:
            p.wait(PROGRESS_EVERY if tmp else WATCH_CHECK)
            break
        except subprocess.TimeoutExpired:
            if tmp and not paused:
                line = progress(tmp, total, started)
                if line:
                    print(line, flush=True)
            if tmp and time.time() - last_watch < WATCH_CHECK:
                continue
            last_watch = time.time()
            busy = watching() > 0
            if busy != paused:
                _suspend(p, busy)
                paused = busy
                print(f"    {'paused - someone is watching Plex' if busy else 'resumed'}  {dt.datetime.now():%H:%M}",
                      flush=True)
    time.sleep(0.2)   # let the reader thread take ffmpeg's last lines
    return subprocess.CompletedProcess(cmd, p.returncode, "", "".join(err))


def remux(exe: str, src: str, dst: Path) -> str:
    """Copy every stream unchanged into an interleaved MP4 with the index at the front. '' = fine, else the error."""
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    dst.parent.mkdir(parents=True, exist_ok=True)
    base = [exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", src]
    tail = ["-c", "copy", "-map_metadata", "0", "-movflags", "+faststart", str(tmp)]
    err = ""
    for maps in (["-map", "0", "-ignore_unknown"], ["-map", "0:v", "-map", "0:a?", "-map", "0:s?"]):
        p = run_polite(base + maps + tail, tmp, os.path.getsize(src))
        if p.returncode == 0 and tmp.exists():
            break
        err = (p.stderr.strip().splitlines() or ["ffmpeg failed"])[-1]
    else:
        tmp.unlink(missing_ok=True)   # our own half-written copy, never the original
        return err
    r = inspect(tmp)
    if r["state"] == "bad" or r["index"] != "start":
        tmp.unlink(missing_ok=True)
        return f"copy still not interleaved ({r['gap']:.0%}, index {r['index']})"
    tmp.rename(dst)
    return ""


def cmd_fix(a):
    # the chosen library's own plan; without one, its rows of the all-libraries plan
    plan = Path(a.plan) if a.plan else plan_for(a.library) if plan_for(a.library).exists() else PLAN
    print(f"Plan: {plan.name}")
    if not plan.exists():
        sys.exit(f"No plan yet ({plan}) - run the scan first.")
    if is_locked(plan):
        sys.exit(f"{plan.name} is open in Excel - save and close it first.")
    rows = read_plan(plan)
    names = {r.get("Library", "") for r in rows}
    mine = [r for r in rows if lib_match(a.library, r.get("Library", ""), names)]
    rows = [r for r in mine if r.get("Apply", "").strip().upper() == "Y"]
    # owner (3 Nov): "excel shows 3 videos, but I got this" - 3 yellow rows, Apply blank, so 0 to fix; say why
    blank = [r for r in mine if not r.get("Apply", "").strip()]
    if a.library:
        print(f"Library: {a.library}")
    todo, skip, waiting = [], 0, []
    for r in rows:
        src, dst = r.get("Source", ""), r.get("NewFile", "").strip()
        if not src or not dst:
            print(f"  ! no Source/NewFile: {r.get('File')}")
        elif Path(dst).exists():
            waiting.append((src, dst))   # fixed earlier, not yet in place
        elif backup_name(dst) and Path(backup_name(dst)).exists():
            skip += 1                    # fixed and in place already
        elif pc.canonical(dst).casefold() == pc.canonical(src).casefold():
            print(f"  ! NewFile is the original itself - skipped: {src}")
        else:
            todo.append((src, Path(dst)))
    gb = sum(os.path.getsize(s) for s, _ in todo if os.path.exists(s)) / 1073741824
    print(f"Y rows: {len(rows)}   to fix: {len(todo)} ({gb:.1f} GB, about {hours(gb):.0f} hours)   "
          f"already fixed: {skip}" + (f"   fixed earlier, only to put in place: {len(waiting)}" if waiting else ""))
    for s, _ in todo[:5]:
        print(f"  {s}")
    if len(todo) > 5:
        print(f"  ... and {len(todo) - 5} more")
    if blank:
        kinds = ", ".join(f"{n} {k}" for k, n in sorted(
            {k: sum(1 for r in blank if (r.get("Check", "").strip() or "clear") == k)
             for k in {r.get("Check", "").strip() or "clear" for r in blank}}.items()))
        print(f"Not taken: {len(blank)} row(s) with Apply blank ({kinds}). Only rows with Y in Apply are fixed - "
              f"type Y for the films you want fixed, save (Ctrl+S), close Excel and run this again.")
    if not todo and not waiting:
        print("\nNothing to fix." if a.go else "\nDry run - nothing changed.")
        return
    print("Each fixed film replaces its original in the same folder; the original is kept in "
          "'<library folder> (mp4 originals)'.")
    if not a.go:
        print("\nDry run - nothing changed.")
        return
    low_priority()
    swaps = SwapLog()
    for s, d in waiting:
        swaps.put_in_place(s, d)
    exe = ffmpeg()
    log = REPORTS / f"fix_audio_timing_log_{dt.datetime.now():%Y-%m-%d_%H%M%S}.csv"
    bad, started = 0, time.time()
    with open(log, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "result", "seconds", "source", "new file"])
        for i, (s, d) in enumerate(todo, 1):
            anchor = Path(pc.canonical(str(d))).anchor or d.anchor
            if shutil.disk_usage(anchor).free < os.path.getsize(s) * 2 + 2 * 1073741824:
                print(f"Stopped: not enough free space on {anchor}.")
                break
            while watching():  # don't start a film while someone is watching
                print(f"    waiting - someone is watching Plex  {dt.datetime.now():%H:%M}", flush=True)
                time.sleep(120)
            t = time.time()
            err = remux(exe, s, d)
            bad += bool(err)
            w.writerow([f"{dt.datetime.now():%Y-%m-%d %H:%M}", err or "ok", round(time.time() - t), s, str(d)])
            fh.flush()
            print(f"[{i}/{len(todo)}] {'FAILED ' + err if err else 'fixed'}  ({(time.time() - t) / 60:.0f} min)  "
                  f"{Path(s).name}", flush=True)
            if not err:
                while watching():  # the original may be the film being watched - swap when it stops
                    time.sleep(60)
                swaps.put_in_place(s, d)
    print(f"\nDone in {(time.time() - started) / 3600:.1f} h: {len(todo) - bad} fixed, {bad} failed. Log: {log}")
    print("In Plex: scan the library (files keep their names, so the films stay the same).")


def library_roots() -> list:
    """Every Plex library folder (openable form), for new_name()."""
    conn = pc.connect_ro()
    try:
        return [pc.openable(r[0]) for r in conn.execute("SELECT root_path FROM section_locations")]
    finally:
        conn.close()


def cmd_one(a):
    """One film (a file, or a folder: its MP4s): check it and, with --go, make the fixed copy now."""
    target = Path(pc.openable(a.path.strip().strip('"')))
    if target.is_dir():
        files = sorted(p for p in target.rglob("*") if p.suffix.lower() in (".mp4", ".m4v", ".mov")
                       and ".part." not in p.name)
    elif target.is_file():
        files = [target]
    else:
        sys.exit(f"Not found: {target}")
    if not files:
        sys.exit(f"No MP4 file in {target}")
    roots, todo, waiting = library_roots(), [], []
    for f in files:
        try:
            r = inspect(f)
        except OSError as e:
            print(f"  cannot read ({e.strerror or e}): {f.name}")
            continue
        if r["state"] == "bad":
            verdict = f"NEEDS FIX - sound stored {r['gap']:.0%} of the file away from the picture"
        elif r["state"] == "unreadable":
            verdict = "no MP4 index - damaged or unfinished file (can't be fixed this way)"
        else:
            verdict = f"fine ({r['state']}, index at the {r['index']})"
        print(f"  {f.name}\n    {verdict}")
        if r["state"] == "bad" or (a.force and r["state"] != "unreadable"):
            dst = Path(a.to) / f.name if a.to else Path(new_name(str(f), roots))
            if dst.exists():
                waiting.append((str(f), dst))
                print("    fixed earlier - only needs to be put in place")
            elif a.to:
                todo.append((str(f), dst))
                print(f"    fixed copy -> {dst}  (kept there; the original stays as it is)")
            else:
                todo.append((str(f), dst))
                print("    will be fixed in place (the original is kept in '(mp4 originals)')")
    if not todo and not waiting:
        print("\nNothing to fix." + ("" if a.force else "  ('copy even if it looks fine' fixes it anyway)"))
        return
    gb = sum(os.path.getsize(s) for s, _ in todo) / 1073741824
    if todo:
        print(f"\n{len(todo)} file(s) to fix, {gb:.1f} GB, about {max(1, round(hours(gb) * 60))} min.")
    if not a.go:
        print("Dry run - nothing changed.")
        return
    low_priority()
    swaps = SwapLog()
    for s, d in waiting:
        swaps.put_in_place(s, d)
    if not todo:
        return
    exe = ffmpeg()
    log = REPORTS / f"fix_audio_timing_log_{dt.datetime.now():%Y-%m-%d_%H%M%S}.csv"
    REPORTS.mkdir(exist_ok=True)
    with open(log, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "result", "seconds", "source", "new file"])
        for s, d in todo:
            print(f"Fixing {Path(s).name} ...", flush=True)
            t = time.time()
            err = remux(exe, s, d)
            w.writerow([f"{dt.datetime.now():%Y-%m-%d %H:%M}", err or "ok", round(time.time() - t), s, str(d)])
            print(f"  {'FAILED ' + err if err else 'fixed'}  ({(time.time() - t) / 60:.0f} min)", flush=True)
            if not err and not a.to:
                while watching():
                    print("    waiting - someone is watching Plex", flush=True)
                    time.sleep(60)
                swaps.put_in_place(s, d)
    print(f"Log: {log}")
    if not a.to:
        print("In Plex: scan the library (same file name, so the film stays the same).")


# ─── swap: fixed copy into the original's place ───────────────────────────────
# Owner (24 Oct): "copy the fixed files into the old folder and remove the old file". Nothing is deleted: the
# original MOVES to '<library folder> (mp4 originals)\<same path>' (outside the library, so Plex doesn't see it;
# the owner deletes that folder when happy), the fixed copy moves to the original's path. Both stay on the same NAS
# share, so the moves are instant renames. Same file name = Plex keeps the film, its watch history and poster.

ORIG_SUFFIX = " (mp4 originals)"


def fixed_copies(library: str = "") -> list:
    """[(original, fixed copy)] from every fix log ('ok' rows), newest first, one per original."""
    seen, out = set(), []
    for log in sorted(REPORTS.glob("fix_audio_timing_log_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True):
        with open(log, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                src, dst = r.get("source", ""), r.get("new file", "")
                key = pc.canonical(src).casefold()
                if r.get("result") == "ok" and src and dst and key not in seen:
                    seen.add(key)
                    out.append((src, dst))
    if library:
        known = plex_files()  # library of each original, from Plex
        names = {lib for lib, _, _ in known}
        libs = {pc.canonical(f).casefold(): lib for lib, _, f in known}
        out = [(s, d) for s, d in out if lib_match(library, libs.get(pc.canonical(s).casefold(), ""), names)]
    return out


def backup_name(dst: str) -> str:
    """Where the original goes: the fixed copy's path with '(mp4 fixed)' -> '(mp4 originals)'. '' = no safe place."""
    i = dst.casefold().rfind(SUFFIX.casefold())
    return dst[:i] + ORIG_SUFFIX + dst[i + len(SUFFIX):] if i >= 0 else ""


def swap_check(src: str, dst: str) -> str:
    """'' = safe to swap, else why not."""
    if not os.path.exists(dst):
        return "already swapped" if os.path.exists(backup_name(dst) or "?") else "fixed copy is missing"
    if not os.path.exists(src):
        return "original is missing"
    if not backup_name(dst):
        return "fixed copy is not in a '(mp4 fixed)' folder - swap it by hand"
    if os.path.exists(backup_name(dst)):
        return "a backup of the original already exists"
    r = inspect(dst)
    if r["state"] == "bad" or r["index"] != "start":
        return "fixed copy is not interleaved"
    a, b = os.path.getsize(src), os.path.getsize(dst)
    if abs(a - b) > 0.02 * a:
        return f"sizes differ too much ({a / 1048576:.0f} MB original, {b / 1048576:.0f} MB copy)"
    return ""


def cmd_swap(a):
    pairs = fixed_copies(a.library)
    todo = []
    for s, d in pairs:
        why = swap_check(s, d)
        if why == "already swapped":
            continue
        if why:
            print(f"  skipped ({why}): {Path(s).name}")
        else:
            todo.append((s, d, backup_name(d)))
    print(f"Fixed copies ready to put in place: {len(todo)}" + (f"  (library: {a.library})" if a.library else ""))
    for s, d, b in todo[:8]:
        print(f"  {Path(s).name}\n    original -> {b}\n    fixed copy -> {s}")
    if len(todo) > 8:
        print(f"  ... and {len(todo) - 8} more")
    if not todo or not a.go:
        print("\nDry run - nothing moved." if todo else "\nNothing to do.")
        return
    if watching():
        sys.exit("Someone is watching Plex right now - try again when nothing is playing.")
    swaps = SwapLog()
    ok = sum(swaps.put_in_place(s, d) == "ok" for s, d, _ in todo)
    print(f"\nDone: {ok} put in place, {len(todo) - ok} failed. Originals kept in the '(mp4 originals)' folders."
          f"\nLog: {swaps.path}")
    print("In Plex: scan the library (files keep their names, so the films stay the same).")


class SwapLog:
    """Puts fixed files in place and writes reports\\fix_audio_timing_swap_<ts>.csv (what 'swap-undo' reverses).
    The log file is made on the first swap only."""

    def __init__(self):
        self.path, self.fh = None, None

    def put_in_place(self, s: str, d) -> str:
        """Original -> '(mp4 originals)' (kept), fixed file -> the original's path. 'ok' or why not."""
        d = str(d)
        why = swap_check(s, d)
        if why:
            res = f"not put in place: {why}"
        else:
            b, res = backup_name(d), "ok"
            try:
                Path(b).parent.mkdir(parents=True, exist_ok=True)
                os.rename(s, b)                     # 1) original out of the way (kept)
                try:
                    os.rename(d, s)                 # 2) fixed file into its place
                except OSError:
                    os.rename(b, s)                 # put the original back - nothing half done
                    raise
                for p in Path(d).parents:           # tidy up the now empty '(mp4 fixed)' folders
                    if SUFFIX.casefold() not in str(p).casefold():
                        break
                    try:
                        p.rmdir()
                    except OSError:
                        break
            except OSError as e:
                res = f"not put in place: {e.strerror or e} (file in use? '5 Put in place' retries later)"
            if self.fh is None:
                REPORTS.mkdir(exist_ok=True)
                self.path = REPORTS / f"fix_audio_timing_swap_{dt.datetime.now():%Y-%m-%d_%H%M%S}.csv"
                self.fh = open(self.path, "w", encoding="utf-8-sig", newline="")
                self.w = csv.writer(self.fh)
                self.w.writerow(["time", "result", "original", "original moved to", "fixed copy"])
            self.w.writerow([f"{dt.datetime.now():%Y-%m-%d %H:%M}", res, s, b, d])
            self.fh.flush()
        print(f"  {'in place - original kept in (mp4 originals)' if res == 'ok' else res}  {Path(s).name}",
              flush=True)
        return res


def cmd_swap_undo(a):
    log = Path(a.log)
    with open(log, encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("result") == "ok"]
    todo = [r for r in rows if os.path.exists(r["original moved to"]) and not os.path.exists(r["fixed copy"])]
    print(f"Swaps in {log.name}: {len(rows)}   can be undone: {len(todo)}")
    if not a.go:
        print("Dry run - nothing moved.")
        return
    bad = 0
    for r in todo:
        s, b, d = r["original"], r["original moved to"], r["fixed copy"]
        try:
            Path(d).parent.mkdir(parents=True, exist_ok=True)  # the empty '(mp4 fixed)' folder was tidied away
            os.rename(s, d)      # fixed copy back to '(mp4 fixed)'
            os.rename(b, s)      # original back
            print(f"  ok  {Path(s).name}")
        except OSError as e:
            bad += 1
            print(f"  FAILED {e.strerror or e}  {Path(s).name}")
    print(f"Undo finished, {bad} problems.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help=r"check Plex's MP4/MOV files and write reports\fix_audio_timing_plan.xlsx")
    s.add_argument("--library", default="", help="only libraries whose name contains this")
    s.add_argument("--limit", type=int, default=0)
    s.set_defaults(fn=cmd_scan)
    f = sub.add_parser("fix", help="make fixed copies of the Y rows (dry run without --go)")
    f.add_argument("plan", nargs="?", default="")
    f.add_argument("--library", default="", help="only the Y rows of this library")
    f.add_argument("--go", action="store_true")
    f.set_defaults(fn=cmd_fix)
    o = sub.add_parser("one", help="check one film (file or folder) and fix it now (dry run without --go)")
    o.add_argument("path")
    o.add_argument("--to", default="", help="folder for the fixed copy (default: '<library> (mp4 fixed)\\...')")
    o.add_argument("--force", action="store_true", help="copy even if the file looks fine")
    o.add_argument("--go", action="store_true")
    o.set_defaults(fn=cmd_one)
    w = sub.add_parser("swap", help="put fixed copies in place of the originals (originals kept; dry run without --go)")
    w.add_argument("--library", default="")
    w.add_argument("--go", action="store_true")
    w.set_defaults(fn=cmd_swap)
    u = sub.add_parser("swap-undo", help="put the originals back from a swap log (dry run without --go)")
    u.add_argument("log")
    u.add_argument("--go", action="store_true")
    u.set_defaults(fn=cmd_swap_undo)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    a.fn(a)


if __name__ == "__main__":
    main()
