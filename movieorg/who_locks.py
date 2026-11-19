#!/usr/bin/env python3
"""
who_locks.py
============

Find which process(es) are holding a file open (locked) on Windows, using the
Restart Manager API (the same mechanism Windows itself uses in its "file in use"
dialog). No external tools / admin rights required.

Usage
-----
    python who_locks.py "U:\\war (russia)\\Eto_bilo_v_razvedke.avi"
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes
from typing import List, Tuple


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD)]


class _RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wintypes.DWORD),
                ("ProcessStartTime", _FILETIME)]


class _RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [
        ("strAppName", wintypes.WCHAR * 256),
        ("strServiceShortName", wintypes.WCHAR * 64),
        ("ApplicationType", wintypes.DWORD),
        ("AppStatus", wintypes.ULONG),
        ("TSSessionId", wintypes.ULONG),
        ("bRestartable", wintypes.BOOL),
        ("Process", _RM_UNIQUE_PROCESS),
    ]


ERROR_MORE_DATA = 234


def who_locks(path: str) -> List[Tuple[int, str, str]]:
    """Return a list of (pid, rm_app_name, process_exe_or_cmdline) for every
    process that currently holds `path` open. Empty list means: not locked, or
    the API could not determine it.
    """
    if sys.platform != "win32":
        return []

    try:
        dll = ctypes.WinDLL("rstrtmgr")
    except OSError:
        return []

    session = wintypes.DWORD()
    session_key = ctypes.create_unicode_buffer(256)  # CCH_RM_SESSION_KEY
    if dll.RmStartSession(ctypes.byref(session), 0, session_key) != 0:
        return []

    results: List[Tuple[int, str, str]] = []
    try:
        files = (ctypes.c_wchar_p * 1)(path)
        if dll.RmRegisterResources(session, 1, files, 0, None, 0, None) != 0:
            return []

        needed = wintypes.DWORD()
        count = wintypes.DWORD()
        reason = wintypes.DWORD()
        rc = dll.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                           None, ctypes.byref(reason))
        if rc != ERROR_MORE_DATA or count.value == 0:
            return []

        array = (_RM_PROCESS_INFO * count.value)()
        rc = dll.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                           array, ctypes.byref(reason))
        if rc != 0:
            return []

        for i in range(count.value):
            info = array[i]
            pid = info.Process.dwProcessId
            app_name = info.strAppName
            exe = _query_process(pid)
            results.append((pid, app_name, exe))
    finally:
        dll.RmEndSession(session)

    return results


def _query_process(pid: int) -> str:
    """Best-effort human-readable description of a PID (name + command line)."""
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", f"ProcessId={pid}",
             "get", "Name,CommandLine", "/format:list"],
            text=True, stderr=subprocess.DEVNULL, timeout=10,
        )
    except Exception:
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                text=True, stderr=subprocess.DEVNULL, timeout=10,
            )
            return out.strip()
        except Exception:
            return ""
    name = cmdline = ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Name="):
            name = line[5:]
        elif line.startswith("CommandLine="):
            cmdline = line[12:]
    if cmdline:
        return f"{name}  [{cmdline}]"
    return name


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("Usage: python who_locks.py <file>")
        return 2
    path = args[0]
    holders = who_locks(path)
    if not holders:
        print(f"No process appears to be locking:\n  {path}")
        print("(it may have just been released, or the API could not detect it).")
        return 0
    print(f"The following process(es) are locking:\n  {path}\n")
    for pid, app, exe in holders:
        print(f"  PID {pid:>7}  |  {app}")
        if exe:
            print(f"              {exe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
