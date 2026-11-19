"""
Shared settings for the Plex Utility scripts.

Plex Media Server runs on this PC; its database is read in place - nothing is
copied or mounted. Every value can be overridden with an environment variable of
the same name, or a line NAME=value in the project's .env file (personal, never
uploaded - see .env.example).

  PLEX_DATA          Plex data folder        E:\\PlexData\\Plex Media Server
  PLEX_SQLITE        Plex's own SQLite CLI    C:\\Program Files\\Plex\\Plex Media Server\\Plex SQLite.exe
  PLEX_URL           Plex web API             http://127.0.0.1:32400
  NAS_HOSTS          NAS name -> IP           diskstation=192.168.1.10;syno2=192.168.1.11
  DRIVE_SHARES       drive -> NAS share       U:=\\\\192.168.1.10\\Share1;V:=\\\\192.168.1.11\\Share2
  DEEPSEEK_API_KEY   the key itself, or
  DEEPSEEK_KEY_FILE  a file with the key on line 1
"""

import ctypes
import os
import re
import sqlite3
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


def env_value(name: str, default: str = "") -> str:
    """Environment variable, else NAME=value from the project's .env file, else default."""
    if os.environ.get(name):
        return os.environ[name]
    try:
        for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            key, sep, val = line.partition("=")
            if sep and key.strip() == name:
                return val.strip().strip('"').strip("'")
    except OSError:
        pass
    return default


PLEX_DATA   = Path(env_value("PLEX_DATA", r"E:\PlexData\Plex Media Server"))
DB_DIR      = PLEX_DATA / "Plug-in Support" / "Databases"
LIBRARY_DB  = DB_DIR / "com.plexapp.plugins.library.db"
BLOBS_DB    = DB_DIR / "com.plexapp.plugins.library.blobs.db"
PREFERENCES = PLEX_DATA / "Preferences.xml"
PLEX_SQLITE = Path(env_value("PLEX_SQLITE", r"C:\Program Files\Plex\Plex Media Server\Plex SQLite.exe"))
PLEX_URL    = env_value("PLEX_URL", "http://127.0.0.1:32400")

def env_pairs(name: str) -> dict:
    """'a=1;b=2' from the environment / .env -> {'a': '1', 'b': '2'} (keys as written)."""
    out = {}
    for item in env_value(name).split(";"):
        key, sep, val = item.partition("=")
        if sep and key.strip():
            out[key.strip()] = val.strip()
    return out


# NAS names -> IP addresses (access by name can be unreliable on a LAN); '<name>.local' is added for each.
NAS_HOSTS = {}
for _name, _ip in env_pairs("NAS_HOSTS").items():
    NAS_HOSTS[_name.casefold()] = NAS_HOSTS[_name.casefold() + ".local"] = _ip
# Fallback if a drive letter is not connected right now (connected drives are asked from Windows).
DRIVE_SHARES = {k.upper().rstrip("\\"): v for k, v in env_pairs("DRIVE_SHARES").items()}


# ─── Database ─────────────────────────────────────────────────────────────────

def connect_ro(db_path=None) -> sqlite3.Connection:
    """Read-only connection to the live Plex database (safe while Plex runs; sees
    Plex's latest changes through its WAL). ICU collations get a dummy stand-in so
    ORDER BY on Plex's collated columns does not fail; writes are impossible."""
    path = Path(db_path or LIBRARY_DB)
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    for name in ("ICU", "icu", "icu_root", "UNICODE", "unicode"):
        try:
            conn.create_collation(name, lambda a, b: (a > b) - (a < b))
        except Exception:
            pass
    conn.row_factory = sqlite3.Row
    return conn


def plex_sqlite(db_path, sql: str, readonly: bool = True) -> str:
    """Run SQL with Plex's own SQLite build (knows Plex's ICU/FTS extensions)."""
    args = [str(PLEX_SQLITE)] + (["-readonly"] if readonly else []) + [str(db_path), sql]
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout + r.stderr).strip()


def plex_running() -> bool:
    """True while Plex Media Server.exe is running on this PC."""
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Plex Media Server.exe", "/NH"],
                       capture_output=True, text=True, errors="replace")
    return "plex media server.exe" in r.stdout.lower()


# ─── Paths: the same file can be stored as V:\..., \\192.168.1.11\..., \\?\V:\... ──

def drive_share(drive: str) -> str:
    r"""'V:' -> '\\192.168.1.11\Syno1517' (the share behind a drive letter, IP form)."""
    drive = drive.upper()
    remote = ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.c_ulong(1024)
        if ctypes.windll.mpr.WNetGetConnectionW(drive, buf, ctypes.byref(n)) == 0:
            remote = buf.value
    except Exception:
        pass
    return ip_form(remote.rstrip("\\")) if remote else DRIVE_SHARES.get(drive, "")


def ip_form(path: str) -> str:
    r"""\\Syno2\x -> \\192.168.1.11\x; anything else unchanged."""
    m = re.match(r"^\\\\([^\\]+)(\\.*)?$", path)
    if m and m.group(1).casefold() in NAS_HOSTS:
        return "\\\\" + NAS_HOSTS[m.group(1).casefold()] + (m.group(2) or "")
    return path


def canonical(path: str) -> str:
    r"""One comparable form for any stored or typed path: NAS address with IP, backslashes.
    'V:\a\b', '\\?\V:\a\b' and '\\Syno2\Syno1517\a\b' all give '\\192.168.1.11\Syno1517\a\b'."""
    p = path.strip().strip('"').strip("'").replace("/", "\\")
    if p.startswith("\\\\?\\UNC\\"):
        p = "\\\\" + p[8:]
    elif p.startswith("\\\\?\\"):
        p = p[4:]
    if len(p) >= 2 and p[1] == ":":
        share = drive_share(p[:2])
        if share:
            p = share + p[2:]
    return ip_form(p)


def share_relative(path: str) -> str:
    r"""Part of the path after the NAS share ('\Japanese\Samurai\x.mkv'), used to find
    a file whatever form Plex stored it in. Paths not on a NAS share come back whole."""
    c = canonical(path)
    m = re.match(r"^\\\\[^\\]+\\[^\\]+(\\.*)?$", c)
    return (m.group(1) or "\\") if m else c


def openable(path: str) -> str:
    r"""A path Windows can open for a stored Plex path (drops the \\?\ prefix, IP form)."""
    return canonical(path)


# ─── Plex web API ─────────────────────────────────────────────────────────────

def plex_token() -> str:
    """The server's Plex token (never printed): Preferences.xml, else the registry."""
    import xml.etree.ElementTree as ET
    try:
        tok = ET.parse(PREFERENCES).getroot().attrib.get("PlexOnlineToken", "")
        if tok:
            return tok
    except (OSError, ET.ParseError):
        pass
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Plex, Inc.\Plex Media Server") as k:
        return winreg.QueryValueEx(k, "PlexOnlineToken")[0]


def plex_request(method: str, path: str, **params):
    """Call the local Plex web API; returns the parsed JSON MediaContainer (or {})."""
    import json
    import urllib.parse
    import urllib.request
    url = PLEX_URL + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, method=method,
                                 headers={"X-Plex-Token": plex_token(), "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
    return json.loads(body).get("MediaContainer", {}) if body else {}


def deepseek_key() -> str | None:
    """DEEPSEEK_API_KEY from the environment / .env, else line 1 of the file DEEPSEEK_KEY_FILE names."""
    key = env_value("DEEPSEEK_API_KEY")
    if key:
        return key
    try:
        f = Path(env_value("DEEPSEEK_KEY_FILE"))
        return f.read_text(encoding="utf-8-sig").splitlines()[0].strip() or None if f.name else None
    except (OSError, IndexError):
        return None
