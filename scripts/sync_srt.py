#!/usr/bin/env python3
"""
sync_srt.py — Auto-sync a foreign-language SRT file to match a video's audio track.

Detects speech timing with faster-whisper (same engine as vdo2srt), then finds
the global offset — and, if present, linear drift (e.g. PAL 25fps vs 23.976fps
speedup) — that best aligns the SRT cues to the detected speech.

Alignment is language-agnostic: cue *timing* is correlated with detected
speech timing, so the SRT may be in any language (e.g. English subs over a
Russian soundtrack).

Before the full run, an AI content check (DeepSeek) verifies the subtitles
actually belong to this video: the first 5 minutes of audio are transcribed
and DeepSeek compares transcript vs subtitle text semantically — this catches
wrong-episode/wrong-film subtitles even when the SRT is a translation.
Requires DEEPSEEK_API_KEY (env var, or DEEPSEEK_API_KEY=... in the project's .env);
skipped silently when no key is set.

Usage:
    python sync_srt.py                            # prompts for video file
    python sync_srt.py myvideo.mkv                # SRT found next to video
    python sync_srt.py myvideo.mkv subs.en.srt    # explicit subtitle file
    python sync_srt.py myvideo.mkv subs.srt --check   # AI match check only, no sync
    python sync_srt.py myvideo.mkv --model base

Batch matching (which subtitle belongs to which episode?):
    python sync_srt.py --match "V:\Series\Season 1"            # report only
    python sync_srt.py --match "V:\Series\Season 1" --rename   # + interactive renames

    Transcribes the first 5 minutes of every video (cached in
    %LOCALAPPDATA%\sync_srt\transcripts), asks DeepSeek to pair each
    subtitle file with its video, writes "SRT Match Report.txt" to the
    folder, and with --rename offers to fix mislabeled subtitle names.

Output: <video stem>.synced.srt next to the video.

Non-SRT subtitle formats (.ass/.ssa/.vtt/.sub/.smi) and unreadable encodings
are converted via the Subtitle Edit CLI when Subtitle Edit is installed.

Requires:
    pip install faster-whisper      (already installed for vdo2srt)

ffmpeg must also be installed and on PATH.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

# ============================================================================
# Configuration & Constants
# ============================================================================

# Suppress OpenMP duplicate-library warning (faster-whisper + numpy conflict)
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# Ensure UTF-8 I/O on Windows
if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Whisper Model Configuration
DEFAULT_MODEL = "small"
COMPUTE_TYPES = {"cuda": "int8_float16", "cpu": "int8"}

# SRT Parsing Configuration
SRT_ENCODINGS = ["utf-8-sig", "utf-8", "cp1251", "latin-1"]
SUBTITLE_EXTS = (".srt", ".ass", ".ssa", ".vtt", ".sub", ".smi")
TIMECODE_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2},\d{3})"
)

# Subtitle Edit Configuration
SUBTITLE_EDIT_PATHS = (
    r"C:\Program Files\Subtitle Edit\SubtitleEdit.exe",
    r"C:\Program Files (x86)\Subtitle Edit\SubtitleEdit.exe",
)

# AI Content Check Configuration (DeepSeek)
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
VERIFY_CHUNK_S = 300         # audio transcribed for the check (5 minutes)
VERIFY_SRT_WINDOW_S = 900    # subtitle window sent for comparison (allows large offsets)
VERIFY_MAX_LINES = 120       # cap on lines sent per side

# Batch Matching Configuration
TRANSCRIPT_CACHE_DIR = (
    Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "sync_srt" / "transcripts"
)
MATCH_SUB_LINES = 40         # subtitle cue lines sent per matching call
MATCH_DIGEST_LINES = 15      # transcript lines sent per video digest
MATCH_BATCH_VIDEOS = 25      # max video digests per DeepSeek call
MATCH_REPORT_NAME = "SRT Match Report.txt"
LANG_SUFFIX_RE = re.compile(r"\.[a-z]{2,3}$", re.IGNORECASE)

# Alignment Algorithm Configuration
OFFSET_STEP_S = 0.5
OFFSET_RANGE_S = 600
FRAMERATE_RATIOS = (
    1.0,
    25 / 23.976, 23.976 / 25,
    25 / 24, 24 / 25,
    24 / 23.976, 23.976 / 24,
)
OFFSET_SEARCH_SAMPLE = 100        # cues sampled during the (scale, offset) search
REFINE_TOLERANCES_S = (3.0, 2.0, 1.5)   # match tolerance per polish iteration
MIN_MATCHED_PAIRS = 10
SCALE_FIT_TOLERANCE = 0.01        # polished scale must stay near the chosen ratio

# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class Cue:
    """One subtitle cue. Times in float seconds."""
    start: float
    end: float
    text: str


# ============================================================================
# SRT Parsing & Writing Functions
# ============================================================================

def parse_timecode(tc: str) -> float:
    """Convert SRT timecode HH:MM:SS,mmm to float seconds."""
    h, m, rest = tc.strip().split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def format_timecode(seconds: float) -> str:
    """Convert float seconds to SRT timecode HH:MM:SS,mmm."""
    ms = round(max(seconds, 0.0) * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def read_srt_text(path: Path) -> str:
    """Read an SRT file, trying common encodings in order."""
    for enc in SRT_ENCODINGS:
        try:
            text = path.read_text(encoding=enc)
            print(f"  Decoded {path.name} using encoding: {enc}")
            return text
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError(f"Could not decode {path} with any known encoding.")


def parse_srt(path: Path) -> list[Cue]:
    """Parse an SRT file into a list of Cues."""
    raw = read_srt_text(path)
    cues = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        try:
            int(lines[0].strip())
        except ValueError:
            continue
        tc_match = TIMECODE_RE.match(lines[1])
        if not tc_match:
            continue
        cues.append(Cue(
            start=parse_timecode(tc_match.group(1)),
            end=parse_timecode(tc_match.group(2)),
            text="\n".join(lines[2:]),
        ))
    return cues


def write_srt(cues: list[Cue], path: Path) -> None:
    """Write cues as UTF-8 SRT, renumbering from 1."""
    lines = []
    for i, cue in enumerate(cues, 1):
        lines.append(str(i))
        lines.append(f"{format_timecode(cue.start)} --> {format_timecode(cue.end)}")
        lines.append(cue.text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ Synced SRT written to: {path}")


# ============================================================================
# Subtitle Loading & Conversion Functions
# ============================================================================

def find_subtitle_edit() -> str | None:
    """Locate Subtitle Edit executable in standard installation paths."""
    for p in SUBTITLE_EDIT_PATHS:
        if Path(p).exists():
            return p
    return None


def convert_to_srt(sub_path: Path, out_dir: Path) -> Path:
    """Convert any subtitle format/encoding to UTF-8 SRT via Subtitle Edit CLI."""
    exe = find_subtitle_edit()
    if not exe:
        raise RuntimeError(
            f"Cannot read {sub_path.name} directly and Subtitle Edit was not found.\n"
            "Install Subtitle Edit (https://www.nikse.dk/subtitleedit) or supply a plain .srt file."
        )
    print(f"  Converting {sub_path.name} to UTF-8 SRT with Subtitle Edit...")
    result = subprocess.run(
        [exe, "/convert", str(sub_path), "SubRip",
         f"/outputfolder:{out_dir}", "/encoding:utf-8"],
        capture_output=True, text=True
    )
    out = out_dir / (sub_path.stem + ".srt")
    if not out.exists():
        raise RuntimeError(
            f"Subtitle Edit failed to convert {sub_path.name}:\n"
            f"{result.stdout or result.stderr}"
        )
    return out


def load_cues(sub_path: Path, tmpdir: Path) -> list[Cue]:
    """
    Parse a subtitle file into Cues. Plain SRT is parsed directly;
    other formats — or SRT files we cannot decode/parse — go through
    Subtitle Edit conversion first.
    """
    if sub_path.suffix.lower() == ".srt":
        try:
            cues = parse_srt(sub_path)
            if cues:
                return cues
        except ValueError:
            pass
        print(f"  Could not parse {sub_path.name} directly — trying Subtitle Edit...")
    return parse_srt(convert_to_srt(sub_path, tmpdir))


# ============================================================================
# Audio Extraction Functions
# ============================================================================

def extract_audio(video_path: Path, out_wav: Path, duration: float | None = None) -> None:
    """Extract mono 16kHz WAV from video using ffmpeg (required by Whisper)."""
    if duration is not None:
        print(f"  Running ffmpeg: first {duration:.0f}s of audio → mono 16 kHz WAV...")
    else:
        print("  Running ffmpeg: full audio track → mono 16 kHz WAV...")
    t0 = time.time()
    cmd = ["ffmpeg", "-y"]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += [
        "-i", str(video_path),
        "-ac", "1", "-ar", "16000",
        "-vn", str(out_wav),
        "-loglevel", "error"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed to extract audio:\n{result.stderr}"
        )
    size_mb = out_wav.stat().st_size / 1_000_000
    print(f"  Audio ready: {size_mb:.1f} MB WAV in {time.time() - t0:.1f}s")


def get_video_duration(video_path: Path) -> float:
    """Use ffprobe to get video duration in seconds, or 0.0 if unavailable."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


# ============================================================================
# Whisper Transcription Functions
# ============================================================================

def resolve_device() -> str:
    """Use CUDA if the CTranslate2 backend supports it, else CPU."""
    try:
        import ctranslate2
        if "float16" in ctranslate2.get_supported_compute_types("cuda"):
            return "cuda"
    except Exception:
        pass
    return "cpu"


def load_whisper_model(model_name: str):
    """Load a faster-whisper model once; reused for the check and the full run."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ImportError("faster-whisper not installed. Run: pip install faster-whisper")

    device = resolve_device()
    compute = COMPUTE_TYPES[device]
    print(f"  Loading Whisper model '{model_name}' on {device.upper()} ({compute})...")
    t0 = time.time()
    model = WhisperModel(model_name, device=device, compute_type=compute)
    print(f"  Model loaded in {time.time() - t0:.1f}s")
    return model


def whisper_transcribe(model, wav_path: Path, duration: float) -> list[dict]:
    """
    Detect speech segments with faster-whisper. Returns a list of
    {start, end, text} dicts. Language auto-detected.
    """
    t0 = time.time()
    segments_gen, info = model.transcribe(
        str(wav_path),
        beam_size=5,
        vad_filter=True,                      # skip non-speech, prevents hallucinations
        condition_on_previous_text=False,     # stops hallucinations from propagating
    )
    print(f"  Language detected: {info.language} ({info.language_probability:.0%} confidence)")
    whisper_transcribe.last_language = info.language

    segments = []
    for seg in segments_gen:
        segments.append({"start": seg.start, "end": seg.end, "text": seg.text})
        if duration > 0:
            print(f"\r  Transcribing... {min(seg.end, duration):5.0f}s / {duration:.0f}s "
                  f"({min(seg.end / duration, 1.0):4.0%})", end="", flush=True)
    print()
    took = time.time() - t0
    speed = f" ({duration / took:.1f}× real-time)" if took > 0 and duration > 0 else ""
    print(f"  Speech detection finished in {took:.1f}s{speed}")
    return segments


# ============================================================================
# AI Content Check Functions (DeepSeek)
# ============================================================================

def get_deepseek_key() -> str | None:
    """DEEPSEEK_API_KEY from the environment, or from <repo root>\\.env."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key.strip()
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def timestamped_lines(items: list[tuple[float, str]], max_lines: int) -> str:
    """Render (seconds, text) pairs as '[mm:ss] text' lines for the LLM prompt."""
    lines = []
    for start, text in items[:max_lines]:
        mm, ss = divmod(int(start), 60)
        lines.append(f"[{mm:03d}:{ss:02d}] {' '.join(text.split())}")
    return "\n".join(lines)


def deepseek_chat(api_key: str, system: str, user: str) -> dict | None:
    """
    One JSON-mode chat call to DeepSeek. Returns the parsed JSON reply,
    or None on any failure (network, HTTP, malformed reply).
    """
    import urllib.request

    body = json.dumps({
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }).encode("utf-8")

    t0 = time.time()
    req = urllib.request.Request(
        DEEPSEEK_API_URL, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        print(f"  DeepSeek responded in {time.time() - t0:.1f}s")
        content = data["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`").lstrip("json").strip()
        return json.loads(content)
    except Exception as e:
        print(f"  ⚠️  DeepSeek call failed ({e})")
        return None


def deepseek_verify(api_key: str, video_name: str, audio_language: str,
                    cues: list[Cue], chunk_segments: list[dict]) -> dict | None:
    """
    Ask DeepSeek whether the subtitles match the transcribed audio chunk.
    The subtitle may be a translation — the comparison is semantic.
    Returns {"match": "yes"|"no"|"uncertain", "estimated_offset_seconds": float|None,
    "reason": str}, or None if the API call fails.
    """
    transcript = timestamped_lines(
        [(s["start"], s["text"]) for s in chunk_segments], VERIFY_MAX_LINES)
    subs = timestamped_lines(
        [(c.start, c.text) for c in cues if c.start <= VERIFY_SRT_WINDOW_S],
        VERIFY_MAX_LINES)

    prompt = f"""A video file "{video_name}" had its first {VERIFY_CHUNK_S // 60} minutes of audio transcribed by Whisper (detected language: {audio_language}).

TRANSCRIPT of the audio (timestamps [mmm:ss] are accurate):
{transcript}

SUBTITLE FILE content for its first {VERIFY_SRT_WINDOW_S // 60} minutes (timestamps may be shifted — that is the problem we are fixing):
{subs}

Question: is this subtitle file for THIS video (same film/episode)? The subtitle may be a TRANSLATION into another language — compare meaning, names, and the flow of dialogue, not exact wording. A constant time shift between the two is normal and does NOT mean a mismatch.

Respond with JSON only:
{{"match": "yes" | "no" | "uncertain",
 "estimated_offset_seconds": <number to ADD to subtitle times so they match the audio, or null if no match or cannot tell>,
 "reason": "<one or two sentences>"}}"""

    print(f"  Sending {len(chunk_segments)} transcript lines + "
          f"{min(len(cues), VERIFY_MAX_LINES)} subtitle lines to {DEEPSEEK_MODEL}...")
    verdict = deepseek_chat(
        api_key,
        "You compare a subtitle file against an audio transcript and answer strictly in JSON.",
        prompt)
    if verdict and verdict.get("match") in ("yes", "no", "uncertain"):
        return verdict
    return None


# ============================================================================
# Batch Matching Functions
# ============================================================================

def find_media(folder: Path) -> tuple[list[Path], list[Path]]:
    """All video and subtitle files directly inside folder (our .synced.srt outputs excluded)."""
    try:
        from vdo_util import VIDEO_EXTENSIONS as video_exts
    except ImportError:
        video_exts = (".mkv", ".mp4", ".m4v", ".mov", ".ts", ".avi",
                      ".wmv", ".webm", ".mpg", ".mpeg")
    videos = sorted(p for p in folder.iterdir()
                    if p.is_file() and p.suffix.lower() in video_exts)
    subs = sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in SUBTITLE_EXTS
                  and not p.name.lower().endswith(".synced.srt"))
    return videos, subs


def transcript_cache_path(video_path: Path, model_name: str) -> Path:
    """Cache file keyed on resolved path, size, mtime, model and chunk length."""
    st = video_path.stat()
    key = f"{video_path.resolve()}|{st.st_size}|{st.st_mtime_ns}|{model_name}|{VERIFY_CHUNK_S}"
    return TRANSCRIPT_CACHE_DIR / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")


def get_chunk_transcript(get_model: Callable, video_path: Path, model_name: str, tmpdir: Path) -> dict:
    """
    {language, segments} for the first VERIFY_CHUNK_S seconds of video_path.
    Served from %LOCALAPPDATA%\\sync_srt\\transcripts when the video is
    unchanged; otherwise transcribed (get_model() is called lazily so the
    Whisper model only loads when at least one video is uncached).
    """
    cache = transcript_cache_path(video_path, model_name)
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    wav = tmpdir / "chunk.wav"
    extract_audio(video_path, wav, duration=VERIFY_CHUNK_S)
    segments = whisper_transcribe(get_model(), wav, VERIFY_CHUNK_S)
    data = {"language": getattr(whisper_transcribe, "last_language", "unknown"),
            "segments": segments}
    TRANSCRIPT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def split_lang_suffix(stem: str) -> tuple[str, str]:
    """'Episode 5.en' → ('Episode 5', '.en');  'Episode 5' → ('Episode 5', '')."""
    m = LANG_SUFFIX_RE.search(stem)
    if m:
        return stem[:m.start()], m.group(0)
    return stem, ""


def deepseek_match(api_key: str, sub_name: str, sub_lines: str,
                   video_digests: list[tuple[str, str]]) -> dict | None:
    """
    Ask DeepSeek which video a subtitle file belongs to. video_digests is
    a list of (video filename, transcript digest). Large folders are split
    into batches of MATCH_BATCH_VIDEOS; the highest-confidence answer wins.
    Returns {"best_match": str|None, "confidence": "high|medium|low", "reason": str}.
    """
    rank = {"high": 3, "medium": 2, "low": 1}
    best: dict | None = None

    for i in range(0, len(video_digests), MATCH_BATCH_VIDEOS):
        batch = video_digests[i:i + MATCH_BATCH_VIDEOS]
        blocks = "\n\n".join(f'VIDEO "{name}":\n{digest or "(no speech detected)"}'
                             for name, digest in batch)
        prompt = f"""A subtitle file "{sub_name}" needs to be matched to the correct video in a folder.

SUBTITLE FILE content (opening lines; timestamps may be shifted — that is normal):
{sub_lines}

Below are Whisper transcripts of the FIRST {VERIFY_CHUNK_S // 60} MINUTES of each candidate video. The subtitle may be a TRANSLATION into another language — compare meaning, names, characters and the flow of dialogue, not exact wording.

{blocks}

Which video does this subtitle file belong to? If none of them fit, say so. Respond with JSON only:
{{"best_match": "<exact video filename from the list>" | null,
 "confidence": "high" | "medium" | "low",
 "reason": "<one or two sentences>"}}"""

        verdict = deepseek_chat(
            api_key,
            "You match subtitle files to video transcripts and answer strictly in JSON.",
            prompt)
        if not verdict or "best_match" not in verdict:
            continue
        # Guard against hallucinated filenames
        if verdict["best_match"] is not None:
            names = {name.lower(): name for name, _ in batch}
            resolved = names.get(str(verdict["best_match"]).lower())
            if resolved is None:
                verdict["best_match"] = None
                verdict["confidence"] = "low"
            else:
                verdict["best_match"] = resolved
        if best is None:
            best = verdict
        elif (verdict["best_match"] is not None) != (best["best_match"] is not None):
            best = verdict if verdict["best_match"] is not None else best
        elif rank.get(verdict.get("confidence"), 0) > rank.get(best.get("confidence"), 0):
            best = verdict
    return best


def build_match_report(folder: Path, videos: list[Path],
                       results: list[tuple[Path, str | None, str, str]]) -> str:
    """results: (subtitle, matched video name or None, confidence, reason)."""
    lines = ["=" * 70,
             f"SRT MATCH REPORT — {folder}",
             f"Generated {time.strftime('%Y-%m-%d %H:%M:%S')}",
             "=" * 70]

    lines.append("\n── Matches " + "─" * 40)
    matched_videos = set()
    any_match = False
    for sub, video_name, confidence, reason in results:
        if video_name is None:
            continue
        any_match = True
        matched_videos.add(video_name)
        base, _lang = split_lang_suffix(sub.stem)
        flag = "  ⚠ MISLABELED" if base != Path(video_name).stem else ""
        lines.append(f"\n  {sub.name}\n    → {video_name}   [{confidence}]{flag}\n    {reason}")
    if not any_match:
        lines.append("  (none)")

    lines.append("\n── Unmatched subtitles " + "─" * 28)
    unmatched = [(s, c, r) for s, v, c, r in results if v is None]
    for sub, confidence, reason in unmatched:
        lines.append(f"  {sub.name} — {reason}")
    if not unmatched:
        lines.append("  (none)")

    lines.append("\n── Videos with no subtitle file " + "─" * 19)
    matched_stems = {Path(v).stem for v in matched_videos}
    # A same-named subtitle only counts if it wasn't matched to a DIFFERENT video
    unmatched_sub_stems = {split_lang_suffix(s.stem)[0]
                           for s, v, c, r in results if v is None}
    bare = [v.name for v in videos
            if v.stem not in matched_stems and v.stem not in unmatched_sub_stems]
    if bare:
        lines.extend(f"  {name}" for name in bare)
    else:
        lines.append("  (none)")

    lines.append("\n── Suggested sync commands " + "─" * 24)
    for sub, video_name, confidence, reason in results:
        if video_name is not None:
            lines.append(f'  python "{Path(__file__).resolve()}" "{folder / video_name}" "{sub}"')
    lines.append("")
    return "\n".join(lines)


def apply_renames(candidates: list[tuple[Path, Path]]) -> None:
    """Interactively rename subtitles: y = rename, n = skip, a = all remaining."""
    all_yes = False
    for sub, target in candidates:
        if target.exists():
            print(f"  SKIP {sub.name} — target already exists: {target.name}")
            continue
        if not all_yes:
            ans = input(f"  Rename \"{sub.name}\" → \"{target.name}\"? [y/n/a]: ").strip().lower()
            if ans == "a":
                all_yes = True
            elif ans != "y":
                print("    skipped")
                continue
        sub.rename(target)
        print(f"    renamed → {target.name}")


def run_match(args) -> None:
    """Batch mode: match every subtitle in FOLDER to its video and write a report."""
    folder = Path(args.match)
    if not folder.is_dir():
        print(f"❌ Not a folder: {folder}")
        sys.exit(1)
    api_key = get_deepseek_key()
    if not api_key:
        print("❌ --match needs DEEPSEEK_API_KEY (env var or DEEPSEEK_API_KEY=... in the project's .env).")
        sys.exit(1)

    videos, subs = find_media(folder)
    print(f"\nFolder: {folder}")
    print(f"Found : {len(videos)} video(s), {len(subs)} subtitle file(s)")
    print(f"Model : Whisper '{args.model}' on {resolve_device().upper()}  |  cache: {TRANSCRIPT_CACHE_DIR}")
    if not videos or not subs:
        print("❌ Need at least one video and one subtitle file in the folder.")
        sys.exit(1)

    # Lazy Whisper loading — skipped entirely when every chunk is cached
    _model = []
    def get_model():
        if not _model:
            _model.append(load_whisper_model(args.model))
        return _model[0]

    print(f"\n[1/3] Transcribing first {VERIFY_CHUNK_S // 60} minutes of each video (cached)...")
    digests: list[tuple[str, str]] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for i, video in enumerate(videos, 1):
            cached = transcript_cache_path(video, args.model).exists()
            print(f"  [{i}/{len(videos)}] {video.name}" + ("  (cached)" if cached else ""))
            data = get_chunk_transcript(get_model, video, args.model, Path(tmpdir))
            digest = timestamped_lines(
                [(s["start"], s["text"]) for s in data["segments"]], MATCH_DIGEST_LINES)
            digests.append((video.name, digest))
            if not data["segments"]:
                print("      ⚠ no speech detected in the opening chunk")

    print(f"\n[2/3] Matching {len(subs)} subtitle file(s) with DeepSeek...")
    results: list[tuple[Path, str | None, str, str]] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for i, sub in enumerate(subs, 1):
            print(f"\n  [{i}/{len(subs)}] {sub.name}")
            try:
                cues = load_cues(sub, Path(tmpdir))
            except (RuntimeError, ValueError, OSError) as e:
                results.append((sub, None, "-", f"could not read/parse: {e}"))
                print(f"      skipped — {e}")
                continue
            sub_lines = timestamped_lines(
                [(c.start, c.text) for c in cues if c.start <= VERIFY_SRT_WINDOW_S],
                MATCH_SUB_LINES)
            verdict = deepseek_match(api_key, sub.name, sub_lines, digests)
            if verdict is None:
                results.append((sub, None, "-", "unmatched (API error)"))
                print("      unmatched (API error)")
                continue
            video_name = verdict.get("best_match")
            confidence = verdict.get("confidence", "low")
            reason = str(verdict.get("reason", "")).strip()
            results.append((sub, video_name, confidence, reason))
            if video_name:
                print(f"      → {video_name}   [{confidence}]")
            else:
                print(f"      no match   [{confidence}]")
            print(f"      {reason}")

    print("\n[3/3] Report...")
    report = build_match_report(folder, videos, results)
    print("\n" + report)
    report_path = folder / MATCH_REPORT_NAME
    report_path.write_text(report, encoding="utf-8")
    print(f"Report written to: {report_path}")

    if args.rename:
        candidates = []
        for sub, video_name, confidence, _reason in results:
            if video_name is None or confidence != "high":
                continue
            base, lang = split_lang_suffix(sub.stem)
            video_stem = Path(video_name).stem
            if base == video_stem:
                continue
            candidates.append((sub, sub.with_name(video_stem + lang + sub.suffix)))
        if candidates:
            print(f"\nRename proposals ({len(candidates)} mislabeled, high-confidence only):")
            apply_renames(candidates)
        else:
            print("\nNo high-confidence mislabeled subtitles to rename.")


# ============================================================================
# Alignment Algorithm Functions
# ============================================================================

def nearest_value(sorted_times: list[float], t: float) -> float:
    """Nearest neighbour to t in an ascending-sorted list."""
    i = bisect.bisect_left(sorted_times, t)
    if i == 0:
        return sorted_times[0]
    if i == len(sorted_times):
        return sorted_times[-1]
    before, after = sorted_times[i - 1], sorted_times[i]
    return before if t - before <= after - t else after


def find_global_offset(s_starts: list[float], w_starts: list[float]) -> tuple[float, float]:
    """
    Cross-correlate: try offsets in fixed steps over the search range and
    return (best_offset, avg_nearest_distance) minimising the total
    nearest-neighbour distance.
    """
    best_offset = 0.0
    best_score = float("inf")
    n_steps = int(OFFSET_RANGE_S / OFFSET_STEP_S)

    for trial_steps in range(-n_steps, n_steps + 1):
        trial = trial_steps * OFFSET_STEP_S
        score = sum(abs(nearest_value(w_starts, t + trial) - (t + trial))
                    for t in s_starts)
        if score < best_score:
            best_score = score
            best_offset = trial
    return best_offset, best_score / max(len(s_starts), 1)


def fit_linear_drift(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    """Least-squares fit w = a*s + b over (srt_time, whisper_time) pairs."""
    n = len(pairs)
    sum_s = sum(s for s, _ in pairs)
    sum_w = sum(w for _, w in pairs)
    sum_ss = sum(s * s for s, _ in pairs)
    sum_sw = sum(s * w for s, w in pairs)
    denom = n * sum_ss - sum_s * sum_s
    if denom == 0:
        return 1.0, 0.0
    a = (n * sum_sw - sum_s * sum_w) / denom
    b = (sum_w - a * sum_s) / n
    return a, b


def align_cues(cues: list[Cue], whisper_segments: list[dict]) -> tuple[list[Cue], str]:
    """
    Align SRT cues to detected speech. Tries each known framerate ratio as a
    candidate time scale, finds the best global offset under each, and keeps
    the best-scoring (scale, offset). Then polishes with a least-squares fit
    on matched cue↔speech pairs. Returns (adjusted_cues, diagnosis_string).
    """
    if not whisper_segments:
        return cues, "Whisper found no speech in audio — cannot compute offset."

    w_starts = sorted(s["start"] for s in whisper_segments)
    s_starts = [c.start for c in cues]

    print(f"  Matching {len(s_starts)} subtitle cues against {len(w_starts)} detected speech starts.")

    # Search on a sample of cues — enough signal, much faster
    stride = max(1, len(s_starts) // OFFSET_SEARCH_SAMPLE)
    sample = s_starts[::stride]

    print(f"  Searching offsets ±{OFFSET_RANGE_S}s (step {OFFSET_STEP_S}s) "
          f"under {len(FRAMERATE_RATIOS)} framerate-ratio candidates:")
    best = (float("inf"), 1.0, 0.0)   # (score, scale, offset)
    score_no_drift = float("inf")
    offset_no_drift = 0.0
    for scale in FRAMERATE_RATIOS:
        offset, score = find_global_offset([t * scale for t in sample], w_starts)
        print(f"    scale ×{scale:.4f} → best offset {offset:+8.2f}s, avg error {score:6.2f}s")
        if scale == 1.0:
            score_no_drift, offset_no_drift = score, offset
        if score < best[0]:
            best = (score, scale, offset)
    _, scale, offset = best

    # Prefer "no drift" unless a framerate ratio wins by a clear margin —
    # on short material the near-1.0 ratios are indistinguishable from 1.0
    if scale != 1.0 and best[0] > score_no_drift * 0.9 - 0.05:
        print(f"  Scale ×{scale:.4f} not clearly better than no drift — keeping scale ×1.0000")
        scale, offset = 1.0, offset_no_drift
    print(f"  Selected: scale ×{scale:.4f}, offset {offset:+.2f}s")

    def transform(a: float, b: float) -> list[Cue]:
        return [replace(c,
                        start=max(0.0, a * c.start + b),
                        end=max(0.0, a * c.end + b))
                for c in cues]

    # Polish: least-squares fit on cues that land close to detected speech
    print("  Polishing with least-squares fit on matched cue↔speech pairs:")
    a, b = scale, offset
    pairs: list[tuple[float, float]] = []
    for tol in REFINE_TOLERANCES_S:
        new_pairs = [(s, w) for s in s_starts
                     if abs((w := nearest_value(w_starts, a * s + b)) - (a * s + b)) <= tol]
        if len(new_pairs) < MIN_MATCHED_PAIRS:
            print(f"    tolerance ±{tol}s: only {len(new_pairs)} pairs — stopping polish")
            break
        a2, b2 = fit_linear_drift(new_pairs)
        if abs(a2 - scale) > SCALE_FIT_TOLERANCE:
            print(f"    tolerance ±{tol}s: fit drifted off the selected scale — stopping polish")
            break
        a, b, pairs = a2, b2, new_pairs
        print(f"    tolerance ±{tol}s: {len(pairs)} pairs → scale ×{a:.4f}, offset {b:+.2f}s")

    if pairs:
        residual = sum(abs(a * s + b - w) for s, w in pairs) / len(pairs)
        matched = f"({len(pairs)}/{len(cues)} cues matched)"
        if scale != 1.0:
            diagnosis = (
                f"Framerate drift corrected: scale ×{a:.4f}, offset {b:+.2f}s  |  "
                f"Average residual error: {residual:.2f}s {matched}"
            )
        else:
            diagnosis = (
                f"Global offset applied: {b:+.2f}s  |  "
                f"Average residual error: {residual:.2f}s {matched}"
            )
        return transform(a, b), diagnosis

    # Polish never got enough matched pairs — apply the raw search result
    residual = sum(abs(nearest_value(w_starts, t * scale + offset) - (t * scale + offset))
                   for t in s_starts) / len(s_starts)
    scale_note = f"scale ×{scale:.4f}, " if scale != 1.0 else ""
    diagnosis = (
        f"Global offset applied: {scale_note}{offset:+.2f}s  |  "
        f"Average residual error: {residual:.2f}s"
    )
    return transform(scale, offset), diagnosis


# ============================================================================
# Mismatch Diagnostics Functions
# ============================================================================

def diagnose_mismatch(cues: list[Cue], whisper_segments: list[dict],
                      video_duration: float) -> str | None:
    """
    Return a human-readable explanation if the SRT appears to be
    a bad match for this video, otherwise return None.
    """
    problems = []

    if not whisper_segments:
        return "❌ Whisper detected NO speech in the audio. The video may be silent, muted, or ffmpeg failed to extract audio correctly."

    srt_start = cues[0].start if cues else 0
    srt_end = cues[-1].end if cues else 0

    # 1. SRT covers a completely different duration
    srt_duration = srt_end - srt_start
    if video_duration > 0:
        ratio = srt_duration / video_duration
        if ratio < 0.3 or ratio > 3.0:
            problems.append(
                f"SRT duration ({srt_duration/60:.1f} min) is very different "
                f"from video duration ({video_duration/60:.1f} min) — "
                f"the subtitle file may be for a different episode or cut."
            )

    # 2. SRT ends well after the video
    if srt_end > video_duration * 1.2 and video_duration > 0:
        problems.append(
            f"SRT has entries up to {format_timecode(srt_end)} but "
            f"video is only {format_timecode(video_duration)} long — "
            f"SRT may cover multiple episodes."
        )

    # 3. Large constant offset (likely wrong episode start point)
    w_first = whisper_segments[0]["start"]
    gap = abs(w_first - srt_start)
    if gap > 120:
        problems.append(
            f"First subtitle cue ({format_timecode(srt_start)}) is {gap/60:.1f} min "
            f"away from first detected speech ({format_timecode(w_first)}) — "
            f"may need manual offset or wrong episode."
        )

    return "\n".join(f"⚠️  {p}" for p in problems) if problems else None


# ============================================================================
# Main Execution Functions
# ============================================================================

def resolve_video_path(arg: str | None) -> Path:
    """Get the video path from the CLI argument or an interactive prompt."""
    if arg:
        video_path = Path(arg)
    else:
        raw = input("Enter path to video file: ").strip().strip('"').strip("'")
        video_path = Path(raw)

    if not video_path.exists():
        print(f"❌ Video file not found: {video_path}")
        sys.exit(1)
    return video_path


def find_subtitles_for(video_path: Path) -> Path:
    """Locate a subtitle file next to the video, prompting if there are several."""
    for ext in SUBTITLE_EXTS:
        candidate = video_path.with_suffix(ext)
        if candidate.exists():
            return candidate

    candidates = sorted(
        p for p in video_path.parent.iterdir()
        if p.suffix.lower() in SUBTITLE_EXTS
    )
    if len(candidates) == 1:
        print(f"  No exact subtitle match found; using: {candidates[0].name}")
        return candidates[0]
    if not candidates:
        print(f"❌ No subtitle file found alongside: {video_path}")
        sys.exit(1)

    print("Multiple subtitle files found in folder:")
    for i, c in enumerate(candidates):
        print(f"  [{i}] {c.name}")
    while True:
        choice = input("Enter number to use: ").strip()
        if choice.isdigit() and int(choice) < len(candidates):
            return candidates[int(choice)]
        print(f"  Please enter a number between 0 and {len(candidates) - 1}.")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    p = argparse.ArgumentParser(
        description="Auto-sync a subtitle file to a video's audio track using faster-whisper. "
                    "Works with subtitles in any language (timing-based alignment)."
    )
    p.add_argument("video", nargs="?", help="path to video file (prompted if omitted)")
    p.add_argument("srt", nargs="?",
                   help="subtitle file to sync (default: found next to the video)")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"Whisper model size (default: {DEFAULT_MODEL})")
    p.add_argument("--check", action="store_true",
                   help="AI content check only (first 5 min + DeepSeek) — no sync; "
                        "requires DEEPSEEK_API_KEY")
    p.add_argument("--match", metavar="FOLDER",
                   help="batch mode: match every subtitle in FOLDER to its video "
                        "and write a report; requires DEEPSEEK_API_KEY")
    p.add_argument("--rename", action="store_true",
                   help="with --match: interactively rename mislabeled subtitles "
                        "(high-confidence matches only)")
    return p.parse_args()


def main():
    """Main execution function."""
    t_start = time.time()
    args = parse_args()

    if args.match:
        if args.video or args.srt:
            print("❌ --match takes a folder; do not also pass video/subtitle arguments.")
            sys.exit(1)
        run_match(args)
        print(f"\nTotal time: {time.time() - t_start:.1f}s")
        return

    video_path = resolve_video_path(args.video)
    if args.srt:
        srt_path = Path(args.srt)
        if not srt_path.exists():
            print(f"❌ Subtitle file not found: {srt_path}")
            sys.exit(1)
    else:
        srt_path = find_subtitles_for(video_path)

    api_key = get_deepseek_key()

    print(f"\nVideo : {video_path.name}")
    print(f"SRT   : {srt_path.name}")
    print(f"Model : Whisper '{args.model}' via faster-whisper on {resolve_device().upper()}")
    print(f"Tools : Subtitle Edit {'found' if find_subtitle_edit() else 'not installed'}"
          f"  |  DeepSeek key {'found' if api_key else 'not set'}")
    print(f"Output: {video_path.stem}.synced.srt")

    if args.check and not api_key:
        print("❌ --check needs DEEPSEEK_API_KEY (env var or DEEPSEEK_API_KEY=... in the project's .env).")
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmpdir:
        print("\n[1/5] Parsing subtitles...")
        try:
            cues = load_cues(srt_path, Path(tmpdir))
        except (RuntimeError, ValueError) as e:
            print(f"❌ {e}")
            sys.exit(1)
        if not cues:
            print("❌ Subtitle file parsed but contains no valid entries.")
            sys.exit(1)
        print(f"  Found {len(cues)} subtitle entries.")
        print(f"  SRT spans {format_timecode(cues[0].start)} → {format_timecode(cues[-1].end)}")

        video_duration = get_video_duration(video_path)
        if video_duration:
            print(f"  Video duration: {format_timecode(video_duration)}")

        try:
            model = load_whisper_model(args.model)
        except ImportError as e:
            print(f"❌ {e}")
            sys.exit(1)

        # ── AI content check: transcribe first 5 min, ask DeepSeek if it matches ──
        verified = False
        print("\n[2/5] AI content check (first 5 minutes)...")
        if api_key:
            chunk_len = min(VERIFY_CHUNK_S, video_duration) if video_duration else VERIFY_CHUNK_S
            chunk_wav = Path(tmpdir) / "chunk.wav"
            try:
                extract_audio(video_path, chunk_wav, duration=chunk_len)
            except RuntimeError as e:
                print(f"❌ {e}")
                sys.exit(1)
            chunk_segs = whisper_transcribe(model, chunk_wav, chunk_len)
            print(f"  Whisper found {len(chunk_segs)} speech segments in the chunk.")
            print("  Asking DeepSeek whether subtitles match this audio...")
            lang = getattr(whisper_transcribe, "last_language", "unknown")
            verdict = deepseek_verify(api_key, video_path.name, lang, cues, chunk_segs)
            if verdict:
                match = verdict["match"]
                est = verdict.get("estimated_offset_seconds")
                est_str = f"  (estimated offset: {est:+.1f}s)" if isinstance(est, (int, float)) else ""
                print(f"  DeepSeek verdict: {match.upper()}{est_str}")
                print(f"  Reason: {verdict.get('reason', '')}")
                if match == "yes":
                    verified = True
                elif match == "no":
                    if args.check:
                        sys.exit(2)
                    proceed = input("\nSubtitles do NOT appear to match this video. Proceed anyway? [y/N]: ").strip().lower()
                    if proceed != "y":
                        print("Aborted.")
                        sys.exit(2)
            if args.check:
                sys.exit(0 if verified else 1)
        else:
            print("  DEEPSEEK_API_KEY not set — skipping AI check (heuristics still apply).")

        print("\n[3/5] Extracting full audio...")
        wav_path = Path(tmpdir) / "audio.wav"
        try:
            extract_audio(video_path, wav_path)
        except RuntimeError as e:
            print(f"❌ {e}")
            sys.exit(1)

        print("\n[4/5] Detecting speech with Whisper...")
        whisper_segs = whisper_transcribe(model, wav_path, video_duration)
        print(f"  Whisper found {len(whisper_segs)} speech segments.")

    print("\n[5/5] Aligning...")
    mismatch_msg = diagnose_mismatch(cues, whisper_segs, video_duration)
    if mismatch_msg:
        print("\n" + "═" * 60)
        print("MISMATCH DETECTED — alignment may produce poor results:")
        print(mismatch_msg)
        print("═" * 60)
        if verified:
            print("(DeepSeek confirmed the content matches — continuing despite warnings.)")
        else:
            proceed = input("\nProceed anyway? [y/N]: ").strip().lower()
            if proceed != "y":
                print("Aborted.")
                sys.exit(0)

    synced, diagnosis = align_cues(cues, whisper_segs)
    print(f"  {diagnosis}")

    print("  Example adjustments (cue start times):")
    mid = len(cues) // 2
    for label, old, new in (("first cue ", cues[0], synced[0]),
                            ("middle cue", cues[mid], synced[mid]),
                            ("last cue  ", cues[-1], synced[-1])):
        print(f"    {label}: {format_timecode(old.start)} → {format_timecode(new.start)}")

    output_path = video_path.with_name(video_path.stem + ".synced.srt")
    write_srt(synced, output_path)

    if mismatch_msg and not verified:
        print("\n⚠️  Note: mismatch warnings were raised above — review the output carefully.")
    print(f"\nTotal time: {time.time() - t_start:.1f}s")


if __name__ == "__main__":
    main()
