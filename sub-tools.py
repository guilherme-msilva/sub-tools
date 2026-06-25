"""
Sub-Tools
=========
GUI utility for downloading, cleaning and synchronizing subtitle files.

Features:
  - Download subtitles from OpenSubtitles.com
  - Clean subtitle files (remove ads, formatting tags)
  - Synchronize subtitles using Alass

Requirements:
    python -m pip install opensubtitlescom python-dotenv

Configuration (.env in the same folder):
    MY_API_KEY        — OpenSubtitles API key (required)
    MY_USERNAME       — Account username       (optional)
    MY_PASSWORD       — Account password       (optional)
    MY_LANGUAGE       — Preferred language code
    AD_KEYWORDS_LIST  — Comma-separated ad filter keywords
    SKIP_EXISTING     — 1 = skip videos with existing subtitle
    AUTO_SYNC         — 1 = auto-sync after download with Alass
"""

from __future__ import annotations

import os
import re
import struct
import sys
import time
import logging
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
from pathlib import Path
from datetime import datetime

# ─── External dependencies ────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv, set_key, dotenv_values
except ImportError:
    import tkinter.messagebox as mb
    mb.showerror("Missing dependency",
                 "Run: python -m pip install opensubtitlescom python-dotenv")
    sys.exit(1)

try:
    from opensubtitlescom import OpenSubtitles
    from opensubtitlescom.exceptions import OpenSubtitlesException
except ImportError:
    import tkinter.messagebox as mb
    mb.showerror("Missing dependency",
                 "Run: python -m pip install opensubtitlescom")
    sys.exit(1)

# ─── Constants ────────────────────────────────────────────────────────────────

APP_NAME      = "Sub-Tools v1.4"
APP_TITLE     = "Sub-Tools"
VIDEO_EXTS    = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".ts"}
REQUEST_DELAY = 1.5

# ── Runtime directory resolution ─────────────────────────────────────────────
# With PyInstaller --onefile, Path(__file__).parent points to a hidden temp
# extraction folder (sys._MEIPASS), NOT to the folder where the .exe lives.
# We need sys.executable for everything that must sit beside the executable
# (alass-windows64/, .env, etc.) and sys._MEIPASS only for files that were
# bundled *inside* the package (e.g. the embedded .ico).
def _runtime_dir() -> Path:
    """
    Returns the directory that contains the running executable (frozen mode)
    or the script file (development mode).
    Use this for resolving paths to external files like .env and alass/.
    """
    if getattr(sys, "frozen", False):
        # Compiled with PyInstaller: sys.executable is the .exe path
        return Path(sys.executable).parent
    return Path(__file__).parent

_SCRIPT_DIR = _runtime_dir()

ENV_PATH = _SCRIPT_DIR / ".env"

# Log file backup delimiters for undo-clean feature
_BACKUP_START = "<<ORIGINAL_BACKUP_START>>"
_BACKUP_END   = "<<ORIGINAL_BACKUP_END>>"

# Alass executable search paths — resolved relative to the .exe / script
ALASS_CANDIDATES: list[Path] = [
    _SCRIPT_DIR / "alass-windows64" / "alass.bat",
    _SCRIPT_DIR / "alass-windows64" / "alass.exe",
    _SCRIPT_DIR / "alass-windows64" / "alass",
    _SCRIPT_DIR / "alass" / "alass.bat",
    _SCRIPT_DIR / "alass" / "alass.exe",
    _SCRIPT_DIR / "alass.bat",
    _SCRIPT_DIR / "alass.exe",
]

DEFAULT_AD_KEYWORDS: list[str] = [
    "opensubtitles", "vip", ".com", ".org", "bet", "casino", "cassino",
    "propaganda", "anúncio", "subtitles", "advertise",
]

# Only BCP-47 codes are accepted by the OpenSubtitles v2 REST API.
# 3-letter ISO 639-2 codes (e.g. "pob", "eng", "spa") are NOT supported.
LANGUAGES: dict[str, tuple[str, list[str]]] = {
    "Portuguese Brazilian (pt-br)": ("pt-br", []),
    "Portuguese (pt)":              ("pt-pt", []),
    "English (en)":                 ("en",    []),
    "Spanish (es)":                 ("es",    []),
    "French (fr)":                  ("fr",    []),
    "German (de)":                  ("de",    []),
    "Italian (it)":                 ("it",    []),
    "Japanese (ja)":                ("ja",    []),
    "Korean (ko)":                  ("ko",    []),
    "Chinese Simplified (zh-cn)":   ("zh-cn", ["zh"]),
    "Arabic (ar)":                  ("ar",    []),
    "Russian (ru)":                 ("ru",    []),
    "Dutch (nl)":                   ("nl",    []),
    "Polish (pl)":                  ("pl",    []),
    "Turkish (tr)":                 ("tr",    []),
}

DEFAULT_LANG_LABEL = "Portuguese Brazilian (pt-br)"

# ─── Color palette ────────────────────────────────────────────────────────────

CLR = {
    "bg":        "#0f1117",
    "surface":   "#1a1d27",
    "surface2":  "#22263a",
    "accent":    "#6c63ff",
    "accent_h":  "#8b85ff",
    "accent2":   "#00c9a7",   # teal accent for Sync tab
    "accent2_h": "#00e5bf",
    "success":   "#3dd68c",
    "warning":   "#f5a623",
    "error":     "#ff5f6d",
    "info":      "#79b8ff",
    "text":      "#e8eaf0",
    "text_dim":  "#6b7280",
    "log_bg":    "#0b0d14",
    "input_bg":  "#181b28",
    "card":      "#141720",
}

# ─── Regex patterns ───────────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>", re.IGNORECASE)
_TIMESTAMP_RE = re.compile(
    r"^\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,\.]\d{3}",
    re.MULTILINE,
)

# Release-tag noise to strip when building a clean query for OpenSubtitles
_JUNK_TAGS_RE = re.compile(
    r"\b("
    r"2160p|4k|uhd|1080p|720p|480p|360p"
    r"|x26[45]|h26[45]|hevc|avc|xvid|divx"
    r"|web[.-]?dl|webrip|bluray|bdrip|brrip|dvdrip|hdtv"
    r"|amzn|dsnp|nf|hbo|hulu|atvp|pcok"
    r"|aac|ac3|dd5?\.?1|dts|atmos|dolby|truehd|flac"
    r"|10bit|hdr10?|sdr|hlg|dv"
    r"|extended|theatrical|remastered|proper|repack|unrated|limited"
    r"|dual[.-]?audio|multi[.-]?audio|dubbed|subbed"
    r"|yify|yts|rarbg|ettv|eztv"
    r")\b",
    re.IGNORECASE,
)

# Episode markers: S01E05  s1e5  (also matches S01E05-E06 — takes first episode)
_EPISODE_SE_RE = re.compile(
    r"[Ss](?P<season>\d{1,2})[Ee](?P<episode>\d{1,2})"
)
# Alternative marker: 01x05  1x05
_EPISODE_X_RE = re.compile(
    r"(?:^|[.\-_\s])(?P<season>\d{1,2})[xX](?P<episode>\d{2})(?:[.\-_\s]|$)"
)

# ─── Queue log handler ────────────────────────────────────────────────────────

class QueueHandler(logging.Handler):
    """Forwards log records to a thread-safe queue consumed by the GUI."""

    def __init__(self, log_queue: queue.Queue) -> None:
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        self.log_queue.put(record)


# ─── Confirmation bridge ──────────────────────────────────────────────────────

class ConfirmBridge:
    """
    Thread-safe bridge so a background worker can pause and ask the GUI
    thread for user confirmation before continuing.
    """

    def __init__(self) -> None:
        self._req: queue.Queue = queue.Queue()
        self._res: queue.Queue = queue.Queue()
        self._cancelled = False

    def request_confirm(self, file_name: str, flagged: list[dict]) -> set[str]:
        """Called from worker thread — blocks until GUI responds."""
        if self._cancelled:
            return set()
        self._req.put({"file": file_name, "blocks": flagged})
        return self._res.get()

    def get_pending(self) -> dict | None:
        """Called from GUI polling loop — non-blocking."""
        try:
            return self._req.get_nowait()
        except queue.Empty:
            return None

    def send_response(self, indices: set[str]) -> None:
        self._res.put(indices)

    def cancel(self) -> None:
        self._cancelled = True
        self._res.put(set())


# ─── .env helpers ─────────────────────────────────────────────────────────────

def _ensure_env_file() -> None:
    if not ENV_PATH.exists():
        ENV_PATH.write_text(
            "MY_API_KEY=\nMY_USERNAME=\nMY_PASSWORD=\n"
            "MY_LANGUAGE=\nAD_KEYWORDS_LIST=\nSKIP_EXISTING=1\n",
            encoding="utf-8",
        )


def load_env_values() -> dict[str, str]:
    _ensure_env_file()
    return {k: (v or "") for k, v in dotenv_values(ENV_PATH).items()}


def save_env_value(key: str, value: str) -> None:
    _ensure_env_file()
    set_key(str(ENV_PATH), key, value)


def load_credentials() -> tuple[tuple[str, str, str] | None, list[str]]:
    _ensure_env_file()
    load_dotenv(dotenv_path=ENV_PATH, override=True)
    api_key  = os.getenv("MY_API_KEY",  "").strip()
    username = os.getenv("MY_USERNAME", "").strip()
    password = os.getenv("MY_PASSWORD", "").strip()
    if not api_key:
        return None, ["MY_API_KEY"]
    return (api_key, username, password), []


def load_preferred_language() -> str:
    saved = load_env_values().get("MY_LANGUAGE", "").strip()
    if saved:
        for label, (primary, _) in LANGUAGES.items():
            if primary == saved:
                return label
    return DEFAULT_LANG_LABEL


def load_ad_keywords() -> list[str]:
    raw = load_env_values().get("AD_KEYWORDS_LIST", "").strip()
    if raw:
        return [k.strip() for k in raw.split(",") if k.strip()]
    return list(DEFAULT_AD_KEYWORDS)


def save_ad_keywords(keywords: list[str]) -> None:
    save_env_value("AD_KEYWORDS_LIST", ",".join(keywords))


def load_skip_existing() -> bool:
    return load_env_values().get("SKIP_EXISTING", "1").strip() == "1"


def save_skip_existing(value: bool) -> None:
    save_env_value("SKIP_EXISTING", "1" if value else "0")




# ─── SRT sanitization ─────────────────────────────────────────────────────────

def _remove_tags(text: str) -> str:
    return _TAG_RE.sub("", text)


def _find_matched_keyword(text_lines: list[str], ad_keywords: list[str]) -> str:
    joined = " ".join(text_lines).lower()
    return next((kw for kw in ad_keywords if kw in joined), "")


def _is_ad_block(text_lines: list[str], ad_keywords: list[str]) -> bool:
    joined = " ".join(text_lines).lower()
    return any(kw in joined for kw in ad_keywords)


def _parse_srt_blocks(content: str) -> list[dict]:
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[dict] = []
    for raw in re.split(r"\n{2,}", content.strip()):
        lines = raw.strip().splitlines()
        if not lines:
            continue
        ts_idx = next(
            (i for i, l in enumerate(lines) if _TIMESTAMP_RE.match(l.strip())),
            None,
        )
        if ts_idx is None:
            continue
        text = [l for l in lines[ts_idx + 1:] if l.strip()]
        if not text:
            continue
        blocks.append({
            "index":     lines[0].strip(),
            "timestamp": lines[ts_idx].strip(),
            "text":      text,
        })
    return blocks


def sanitize_srt(
    srt_path: Path,
    ad_keywords: list[str],
    log: logging.Logger,
    stop_event: threading.Event,
    confirm_bridge: ConfirmBridge | None = None,
) -> None:
    """Clean an SRT file in-place: strip tags, filter ads, renumber blocks.
    Writes a .log sidecar with full original backup when blocks are removed."""
    try:
        original_content = srt_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.error("Error     : Cannot read '%s': %s", srt_path.name, exc)
        return

    blocks = _parse_srt_blocks(original_content)
    if not blocks:
        log.warning("Warning   : No valid blocks in '%s'. Skipping.", srt_path.name)
        return

    all_cleaned: list[list[str]] = []
    tags_count = 0
    for block in blocks:
        cleaned = [_remove_tags(line) for line in block["text"]]
        if cleaned != block["text"]:
            tags_count += 1
        all_cleaned.append(cleaned)

    flagged: list[dict] = []
    for i, (block, cleaned) in enumerate(zip(blocks, all_cleaned)):
        if _is_ad_block(cleaned, ad_keywords):
            flagged.append({
                "index":           block["index"],
                "timestamp":       block["timestamp"],
                "text":            block["text"],
                "cleaned_text":    cleaned,
                "matched_keyword": _find_matched_keyword(cleaned, ad_keywords),
            })

    if flagged and confirm_bridge and not stop_event.is_set():
        to_remove: set[str] = confirm_bridge.request_confirm(srt_path.name, flagged)
    elif flagged:
        to_remove = {f["index"] for f in flagged}
    else:
        to_remove = set()

    if stop_event.is_set():
        return

    removed_info: list[dict] = []
    kept: list[dict] = []
    for i, block in enumerate(blocks):
        if block["index"] in to_remove:
            removed_info.append(block)
            continue
        kept.append({**block, "text": all_cleaned[i]})

    output_parts: list[str] = []
    for new_idx, block in enumerate(kept, 1):
        output_parts.append(str(new_idx))
        output_parts.append(block["timestamp"])
        output_parts.extend(block["text"])
        output_parts.append("")

    srt_path.write_text("\n".join(output_parts), encoding="utf-8")

    if removed_info:
        log_path = srt_path.with_suffix(".log")
        with log_path.open("w", encoding="utf-8") as lf:
            lf.write("Sub-Tools — Sanitization Report\n")
            lf.write("=" * 52 + "\n")
            lf.write(f"File      : {srt_path.name}\n")
            lf.write(f"Processed : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            lf.write(f"Original  : {len(blocks)} block(s)\n")
            lf.write(f"Kept      : {len(kept)} block(s)\n")
            lf.write(f"Removed   : {len(removed_info)} block(s)\n")
            lf.write(f"Tags fixed: {tags_count} block(s)\n")
            lf.write("─" * 52 + "\n\n")
            for r in removed_info:
                lf.write(f"[REMOVED] Block #{r['index']}\n")
                lf.write(f"  Timestamp : {r['timestamp']}\n")
                lf.write(f"  Text      : {' | '.join(r['text'])}\n\n")
            lf.write(f"\n{_BACKUP_START}\n")
            lf.write(original_content)
            lf.write(f"\n{_BACKUP_END}\n")
        log.info("Cleaned   : %s  — removed %d ad block(s), fixed %d tag(s)  → .log saved",
                 srt_path.name, len(removed_info), tags_count)
    elif tags_count > 0:
        log.info("Cleaned   : %s  — fixed tags in %d block(s)", srt_path.name, tags_count)
    else:
        log.info("Unchanged : %s  — already clean", srt_path.name)


def undo_clean(log_path: Path, log: logging.Logger) -> bool:
    """Restore .srt from the backup embedded in a .log file. Deletes the .log on success."""
    try:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.error("Error     : Cannot read '%s': %s", log_path.name, exc)
        return False

    start = raw.find(_BACKUP_START)
    end   = raw.find(_BACKUP_END)
    if start == -1 or end == -1:
        log.warning("Warning   : No backup in '%s'. Skipping.", log_path.name)
        return False

    original = raw[start + len(_BACKUP_START):end].strip()
    srt_path = log_path.with_suffix(".srt")
    try:
        srt_path.write_text(original + "\n", encoding="utf-8")
        log_path.unlink()
        log.info("Restored  : '%s' — .log removed.", srt_path.name)
        return True
    except OSError as exc:
        log.error("Error     : Could not restore '%s': %s", srt_path.name, exc)
        return False


def run_clean(
    folder: Path,
    ad_keywords: list[str],
    confirm_bridge: ConfirmBridge,
    log: logging.Logger,
    stop_event: threading.Event,
) -> None:
    srt_files = sorted(p for p in folder.rglob("*.srt") if p.is_file())
    if not srt_files:
        log.info("No .srt files found in '%s'.", folder)
        return
    log.info("Found %d subtitle file(s). Starting cleanup…", len(srt_files))
    log.info("─" * 52)
    for idx, srt_path in enumerate(srt_files, 1):
        if stop_event.is_set():
            log.info("Cancelled by user.")
            return
        log.info("[%d/%d] Cleaning: %s", idx, len(srt_files), srt_path.name)
        try:
            sanitize_srt(srt_path, ad_keywords, log, stop_event, confirm_bridge)
        except Exception as exc:
            log.error("Error     : '%s': %s", srt_path.name, exc)
    log.info("─" * 52)
    log.info("✔ Cleanup complete! Processed %d file(s).", len(srt_files))


def run_undo(
    folder: Path,
    log: logging.Logger,
    stop_event: threading.Event,
) -> None:
    log_files = [p for p in folder.rglob("*.log") if p.is_file()]
    restorable = []
    for lf in sorted(log_files):
        try:
            if _BACKUP_START in lf.read_text(encoding="utf-8", errors="replace"):
                restorable.append(lf)
        except OSError:
            continue
    if not restorable:
        log.info("No restorable .log files found in '%s'.", folder)
        return
    log.info("Found %d restorable file(s). Restoring…", len(restorable))
    log.info("─" * 52)
    for idx, log_path in enumerate(restorable, 1):
        if stop_event.is_set():
            log.info("Cancelled by user.")
            return
        log.info("[%d/%d] Restoring: %s", idx, len(restorable),
                 log_path.with_suffix(".srt").name)
        undo_clean(log_path, log)
    log.info("─" * 52)
    log.info("✔ Restore complete! Processed %d file(s).", len(restorable))


# ─── Alass backend ────────────────────────────────────────────────────────────

def find_alass() -> Path | None:
    """Returns the path to the Alass executable inside the project folder, or None."""
    return next((c for c in ALASS_CANDIDATES if c.exists()), None)


def run_alass_sync(
    alass_bin: Path,
    video: Path,
    srt_path: Path,
    log: logging.Logger,
    stop_event: threading.Event,
    split_penalty: float | None = None,
) -> tuple[bool, str]:
    """
    Synchronizes a subtitle file against a video using Alass.

    Steps:
      1. Rename srt_path  →  <stem>.ori.srt   (preserves original)
      2. Run: alass [--split-penalty N] video.mkv <stem>.ori.srt  video_stem.srt

    Args:
        split_penalty: When provided, passes ``--split-penalty <value>`` to Alass.
                       Lower values (0.05–0.15) suit TV shows with recaps/cuts.
                       Higher values (0.2–0.5) treat the file as one continuous block.

    Returns:
        (True, output_path_str)  on success
        (False, error_message)   on failure
    """
    if stop_event.is_set():
        return False, "Cancelled"

    ori_srt    = srt_path.parent / f"{srt_path.stem}.ori.srt"
    output_srt = video.parent / f"{video.stem}.srt"

    try:
        log.info("Alass     : Renaming '%s' → '%s'", srt_path.name, ori_srt.name)
        srt_path.rename(ori_srt)
    except OSError as exc:
        return False, f"Could not rename subtitle: {exc}"

    cmd = [str(alass_bin)]
    if split_penalty is not None:
        cmd += ["--split-penalty", f"{split_penalty:.2f}"]
    cmd += [str(video), str(ori_srt), str(output_srt)]

    log.info("Alass     : Synchronizing '%s'…", video.name)
    log.info("Alass     : %s", " ".join(f'"{a}"' for a in cmd))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return False, "Alass timed out (>10 min)."
    except OSError as exc:
        return False, f"Could not launch Alass: {exc}"

    if proc.returncode == 0:
        log.info("Synced    : '%s' → %s", video.name, output_srt.name)
        return True, str(output_srt)
    else:
        err = (proc.stderr or proc.stdout or "Unknown error").strip()
        log.error("Alass error for '%s': %s", video.name, err)
        return False, err


# ─── Download backend ─────────────────────────────────────────────────────────

def build_client(api_key: str, username: str, password: str,
                 log: logging.Logger) -> OpenSubtitles:
    client = OpenSubtitles(APP_NAME, api_key)
    if username and password:
        log.info("Authenticating with OpenSubtitles.com…")
        client.login(username, password)
        log.info("Login successful (authenticated mode).")
    else:
        log.info("Using anonymous mode (API key only — 5 downloads/day limit).")
    return client


def find_video_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*")
                  if p.is_file() and p.suffix.lower() in VIDEO_EXTS)


# ─── Smart search helpers ────────────────────────────────────────────────────

def _compute_movie_hash(path: Path) -> str | None:
    """
    Compute the OpenSubtitles MovieHash (64-bit checksum of file size +
    first/last 65536 bytes interpreted as little-endian 64-bit integers).
    Returns a 16-character hex string, or None if the file is too small.
    """
    try:
        file_size = path.stat().st_size
        chunk_size = 65536
        if file_size < chunk_size * 2:
            return None
        hash_val = file_size
        with path.open("rb") as f:
            for chunk in (f.read(chunk_size),):
                for i in range(0, len(chunk), 8):
                    (val,) = struct.unpack_from("<q", chunk, i)
                    hash_val = (hash_val + val) & 0xFFFFFFFFFFFFFFFF
            f.seek(-chunk_size, 2)
            for chunk in (f.read(chunk_size),):
                for i in range(0, len(chunk), 8):
                    (val,) = struct.unpack_from("<q", chunk, i)
                    hash_val = (hash_val + val) & 0xFFFFFFFFFFFFFFFF
        return f"{hash_val:016x}"
    except (OSError, struct.error):
        return None


def _clean_video_name(stem: str) -> str:
    """Strip release tags and normalize separators for a plain text query."""
    name = stem
    # Drop episode marker and everything after it so 'Show.S01E01.1080p' → 'Show'
    m = _EPISODE_SE_RE.search(name)
    if not m:
        m = _EPISODE_X_RE.search(name)
    if m:
        name = name[:m.start()]
    name = _JUNK_TAGS_RE.sub(" ", name)
    name = re.sub(r"[.\-_]+", " ", name)
    name = re.sub(r"\s{2,}", " ", name).strip()
    return name


def _parse_episode_info(stem: str) -> tuple[str, int, int] | None:
    """Extract (show_name, season, episode) from a filename stem, or None."""
    m = _EPISODE_SE_RE.search(stem)
    if m:
        show_part = stem[:m.start()]
        season    = int(m.group("season"))
        episode   = int(m.group("episode"))
    else:
        m = _EPISODE_X_RE.search(stem)
        if not m:
            return None
        show_part = stem[:m.start()]
        season    = int(m.group("season"))
        episode   = int(m.group("episode"))

    show = re.sub(r"[.\-_]+", " ", show_part)
    show = _JUNK_TAGS_RE.sub(" ", show)
    show = re.sub(r"\s{2,}", " ", show).strip()
    return (show, season, episode) if show else None


def search_subtitle_with_fallback(
    client: OpenSubtitles,
    video: Path,
    language: str,
    log: logging.Logger,
    stop_event: threading.Event,
) -> tuple[object | None, str]:
    """
    3-step subtitle search with automatic fallback.

    Step 1 — MovieHash  : exact fingerprint match (most accurate).
    Step 2 — Clean name : filename stripped of release tags.
    Step 3 — Episode    : structured season/episode query (TV series).

    Returns (result_object, method_label) or (None, "").
    """

    def _safe_search(**kwargs) -> object | None:
        """Wrapper with 429 rate-limit retry and quota-exhaustion detection."""
        for attempt in range(2):
            try:
                resp = client.search(**kwargs)
                return resp.data[0] if (resp and resp.data) else None
            except OpenSubtitlesException as exc:
                err = str(exc).lower()
                if "429" in err or "too many" in err:
                    if attempt == 0:
                        log.warning("Rate-limit hit. Waiting 60 s…")
                        for _ in range(60):
                            if stop_event.is_set():
                                return None
                            time.sleep(1)
                        continue  # retry once
                    log.error("Error     : Persistent rate-limit. Skipping search.")
                    return None
                elif "406" in err or "quota" in err:
                    log.error("Error     : Download quota exhausted. Stopping.")
                    stop_event.set()
                    return None
                else:
                    raise
        return None

    # ── Step 1: Hash ────────────────────────────────────────────────────
    log.info("Search    : [1/3] Hash — '%s'…", video.name)
    movie_hash = _compute_movie_hash(video)
    if movie_hash:
        result = _safe_search(moviehash=movie_hash, languages=language)
        if result:
            return result, "hash match"
        if stop_event.is_set():
            return None, ""
    else:
        log.info("Search    : [1/3] File too small for hash — skipping.")

    time.sleep(REQUEST_DELAY)

    # ── Step 2: Clean name query ─────────────────────────────────────────
    clean = _clean_video_name(video.stem)
    log.info("Search    : [2/3] Clean-name — '%s'…", clean)
    result = _safe_search(query=clean, languages=language)
    if result:
        return result, f"clean name ('{clean}')"
    if stop_event.is_set():
        return None, ""

    time.sleep(REQUEST_DELAY)

    # ── Step 3: Structured episode query ─────────────────────────────────
    parsed = _parse_episode_info(video.stem)
    if parsed:
        show, season, episode = parsed
        log.info("Search    : [3/3] Episode — '%s' S%02dE%02d…",
                 show, season, episode)
        result = _safe_search(
            query=show,
            season_number=season,
            episode_number=episode,
            languages=language,
        )
        if result:
            return result, f"episode ('{show}' S{season:02d}E{episode:02d})"
    else:
        log.info("Search    : [3/3] No episode pattern detected — skipping.")

    return None, ""


def download_subtitle(client: OpenSubtitles, result, dest_path: Path) -> None:
    raw_bytes: bytes = client.download(result)
    dest_path.write_text(client.bytes_to_str(raw_bytes), encoding="utf-8")


def process_video(
    client: OpenSubtitles,
    video: Path,
    lang_codes: list[str],
    skip_existing: bool,
    log: logging.Logger,
    stop_event: threading.Event,
) -> None:
    """Download subtitle for one video."""
    if stop_event.is_set():
        return

    base_srt = video.with_suffix(".srt")
    if base_srt.exists() and skip_existing:
        log.info("Skipped   : Subtitle already exists → %s", base_srt.name)
        return

    # ── Search (3-step smart fallback) ──────────────────────────────────────
    result       = None
    used_language = None

    for language in lang_codes:
        if stop_event.is_set():
            return
        try:
            result, method = search_subtitle_with_fallback(
                client, video, language, log, stop_event
            )
        except OpenSubtitlesException as exc:
            log.warning("Warning   : Search error for '%s' (%s): %s",
                        video.name, language, exc)
            continue
        except Exception as exc:
            log.warning("Warning   : Unexpected search error for '%s': %s",
                        video.name, exc)
            continue

        if result:
            used_language = language
            log.info("Found     : Subtitle located via %s.", method)
            break

        if stop_event.is_set():
            return

    if not result:
        log.warning("Not found : No subtitle found for '%s' (tried hash, clean name and episode query).",
                    video.name)
        return

    # ── Destination path ─────────────────────────────────────────────────────
    if base_srt.exists():
        srt_path = video.parent / f"{video.stem}.{used_language}.srt"
        if srt_path.exists():
            log.info("Skipped   : '%s' already exists.", srt_path.name)
            return
        log.info("Info      : Base .srt exists — saving as '%s'.", srt_path.name)
    else:
        srt_path = base_srt

    # ── Download ─────────────────────────────────────────────────────────────
    try:
        download_subtitle(client, result, srt_path)
        log.info("Success   : '%s' [%s] → %s", video.name, used_language, srt_path.name)
    except OpenSubtitlesException as exc:
        err = str(exc).lower()
        if "429" in err or "too many" in err:
            log.warning("Rate-limit on download. Waiting 60s…")
            for _ in range(60):
                if stop_event.is_set():
                    return
                time.sleep(1)
            try:
                download_subtitle(client, result, srt_path)
                log.info("Success   : '%s' → %s", video.name, srt_path.name)
            except Exception as e:
                log.error("Error     : Download failed for '%s': %s", video.name, e)
                return
        else:
            log.error("Error     : API error for '%s': %s", video.name, exc)
            return
    except Exception as exc:
        log.error("Error     : Unexpected error for '%s': %s", video.name, exc)
        return


def run_download(
    folder: Path,
    lang_codes: list[str],
    skip_existing: bool,
    log: logging.Logger,
    stop_event: threading.Event,
) -> None:
    try:
        credentials, missing = load_credentials()
        if missing:
            log.error("Missing credentials: %s\nConfigure via Settings (⚙).", ", ".join(missing))
            return

        api_key, username, password = credentials
        client = build_client(api_key, username, password, log)

        videos = find_video_files(folder)
        if not videos:
            log.info("No video files found in '%s'.", folder)
            return

        log.info("Found %d video file(s). Starting search…", len(videos))
        log.info("─" * 52)

        for idx, video in enumerate(videos, 1):
            if stop_event.is_set():
                log.info("Cancelled by user.")
                return
            log.info("[%d/%d] Processing: %s", idx, len(videos), video.name)
            try:
                process_video(client, video, lang_codes, skip_existing, log, stop_event)
            except Exception as exc:
                log.error("Error     : '%s': %s", video.name, exc)
            if idx < len(videos) and not stop_event.is_set():
                time.sleep(REQUEST_DELAY)

        log.info("─" * 52)
        log.info("✔ Done! Processed %d video(s).", len(videos))

    except OpenSubtitlesException as exc:
        log.error("API error: %s", exc)
    except Exception as exc:
        log.error("Unexpected error: %s", exc)


# ─── Settings Dialog ──────────────────────────────────────────────────────────

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk) -> None:
        super().__init__(parent)
        self.title("Settings — Credentials")
        self.configure(bg=CLR["bg"])
        self.resizable(False, False)
        self.grab_set()
        self.update_idletasks()
        w, h = 500, 400
        px = parent.winfo_x() + (parent.winfo_width()  - w) // 2
        py = parent.winfo_y() + (parent.winfo_height() - h) // 2
        self.geometry(f"{w}x{h}+{px}+{py}")
        self._show_api = tk.BooleanVar(value=False)
        self._build()
        self._load()

    def _build(self) -> None:
        hdr = tk.Frame(self, bg=CLR["surface"], height=52)
        hdr.pack(fill="x"); hdr.pack_propagate(False)
        tk.Label(hdr, text="⚙  Settings", font=("Segoe UI", 14, "bold"),
                 fg=CLR["accent"], bg=CLR["surface"]).pack(side="left", padx=16, pady=10)
        tk.Label(hdr, text="Username & Password are optional",
                 font=("Segoe UI", 8), fg=CLR["text_dim"],
                 bg=CLR["surface"]).pack(side="left", padx=4)

        body = tk.Frame(self, bg=CLR["bg"])
        body.pack(fill="both", expand=True, padx=24, pady=8)

        tk.Label(body, text="USERNAME  (optional — raises download limit)",
                 font=("Segoe UI", 8, "bold"), fg=CLR["text_dim"],
                 bg=CLR["bg"]).pack(anchor="w", pady=(10, 2))
        self._user = self._entry(body)

        tk.Label(body, text="PASSWORD  (optional)",
                 font=("Segoe UI", 8, "bold"), fg=CLR["text_dim"],
                 bg=CLR["bg"]).pack(anchor="w", pady=(10, 2))
        self._pass = self._entry(body, show="●")

        tk.Frame(body, bg=CLR["surface2"], height=1).pack(fill="x", pady=(14, 0))

        tk.Checkbutton(body, text="Change API Key  (for advanced users)",
                       variable=self._show_api, command=self._toggle_api,
                       font=("Segoe UI", 9), fg=CLR["text_dim"], bg=CLR["bg"],
                       activebackground=CLR["bg"], selectcolor=CLR["surface2"],
                       relief="flat", bd=0, cursor="hand2").pack(anchor="w", pady=(8, 0))

        self._api_frame = tk.Frame(body, bg=CLR["bg"])
        self._api_frame.pack(fill="x")
        tk.Label(self._api_frame, text="API KEY  (required)",
                 font=("Segoe UI", 8, "bold"), fg=CLR["text_dim"],
                 bg=CLR["bg"]).pack(anchor="w", pady=(8, 2))
        self._api = self._entry(self._api_frame, show="●")
        self._api.pack_forget()

        btn_row = tk.Frame(self, bg=CLR["bg"])
        btn_row.pack(fill="x", padx=24, pady=(0, 16))
        self._btn(btn_row, "Save", self._save, CLR["accent"], CLR["accent_h"]).pack(side="right")
        self._btn(btn_row, "Cancel", self.destroy,
                  CLR["bg"], CLR["surface2"], CLR["text_dim"]).pack(side="right", padx=(0, 8))

    def _entry(self, p, show="") -> tk.Entry:
        e = tk.Entry(p, font=("Segoe UI", 10), bg=CLR["input_bg"], fg=CLR["text"],
                     insertbackground=CLR["text"], show=show, relief="flat", bd=0,
                     highlightthickness=1, highlightbackground=CLR["surface2"],
                     highlightcolor=CLR["accent"])
        e.pack(fill="x", ipady=8, ipadx=10)
        return e

    def _btn(self, p, text, cmd, bg, bgh, fg=None) -> tk.Button:
        fg = fg or CLR["bg"]
        b = tk.Button(p, text=text, command=cmd, font=("Segoe UI", 10, "bold"),
                      fg=fg, bg=bg, activeforeground=fg, activebackground=bgh,
                      relief="flat", bd=0, padx=16, pady=8, cursor="hand2")
        b.bind("<Enter>", lambda e: b.config(bg=bgh))
        b.bind("<Leave>", lambda e: b.config(bg=bg))
        return b

    def _load(self) -> None:
        v = load_env_values()
        self._user.insert(0, v.get("MY_USERNAME", ""))
        self._pass.insert(0, v.get("MY_PASSWORD", ""))
        self._api.insert(0,  v.get("MY_API_KEY",  ""))

    def _toggle_api(self) -> None:
        if self._show_api.get():
            self._api.pack(fill="x", ipady=8, ipadx=10)
        else:
            self._api.pack_forget()

    def _save(self) -> None:
        u, p = self._user.get().strip(), self._pass.get().strip()
        if bool(u) != bool(p):
            messagebox.showwarning("Incomplete",
                "Fill both Username and Password, or leave both empty.", parent=self)
            return
        save_env_value("MY_USERNAME", u); save_env_value("MY_PASSWORD", p)
        if self._show_api.get():
            k = self._api.get().strip()
            if not k:
                messagebox.showwarning("Incomplete", "API Key cannot be empty.", parent=self)
                return
            save_env_value("MY_API_KEY", k)
        messagebox.showinfo("Saved", "Settings saved successfully.", parent=self)
        self.destroy()


# ─── Keywords Editor Dialog ───────────────────────────────────────────────────

class KeywordsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, keywords: list[str], on_save) -> None:
        super().__init__(parent)
        self.title("Ad Filter Keywords")
        self.configure(bg=CLR["bg"])
        self.resizable(False, False)
        self.grab_set()
        self.update_idletasks()
        w, h = 440, 460
        px = parent.winfo_x() + (parent.winfo_width()  - w) // 2
        py = parent.winfo_y() + (parent.winfo_height() - h) // 2
        self.geometry(f"{w}x{h}+{px}+{py}")
        self._keywords = list(keywords)
        self._on_save  = on_save
        self._build()

    def _build(self) -> None:
        hdr = tk.Frame(self, bg=CLR["surface"], height=52)
        hdr.pack(fill="x"); hdr.pack_propagate(False)
        tk.Label(hdr, text="🔤  Ad Filter Keywords",
                 font=("Segoe UI", 13, "bold"),
                 fg=CLR["accent"], bg=CLR["surface"]).pack(side="left", padx=16, pady=12)

        body = tk.Frame(self, bg=CLR["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=12)
        tk.Label(body,
                 text="Subtitle blocks containing any of these keywords will be flagged as ads.",
                 font=("Segoe UI", 9), fg=CLR["text_dim"], bg=CLR["bg"],
                 wraplength=390, justify="left").pack(anchor="w")

        lb_outer = tk.Frame(body, bg=CLR["accent"])
        lb_outer.pack(fill="both", expand=True, pady=(10, 0))
        lb_inner = tk.Frame(lb_outer, bg=CLR["log_bg"])
        lb_inner.pack(fill="both", expand=True, padx=1, pady=1)
        sb = tk.Scrollbar(lb_inner, bg=CLR["surface2"], troughcolor=CLR["log_bg"],
                          relief="flat", bd=0, width=8)
        sb.pack(side="right", fill="y")
        self._lb = tk.Listbox(lb_inner, font=("Consolas", 10),
                              bg=CLR["log_bg"], fg=CLR["text"],
                              selectbackground=CLR["accent"], selectforeground=CLR["bg"],
                              activestyle="none", relief="flat", bd=0,
                              yscrollcommand=sb.set)
        self._lb.pack(side="left", fill="both", expand=True, padx=8, pady=6)
        sb.config(command=self._lb.yview)
        self._refresh()

        add_row = tk.Frame(body, bg=CLR["bg"])
        add_row.pack(fill="x", pady=(10, 0))
        self._new = tk.Entry(add_row, font=("Segoe UI", 10),
                             bg=CLR["input_bg"], fg=CLR["text"],
                             insertbackground=CLR["text"],
                             relief="flat", bd=0,
                             highlightthickness=1, highlightbackground=CLR["surface2"],
                             highlightcolor=CLR["accent"])
        self._new.pack(side="left", fill="x", expand=True, ipady=7, ipadx=8)
        self._new.bind("<Return>", lambda e: self._add())
        self._mkbtn(add_row, "Add", self._add, CLR["accent"], CLR["accent_h"]).pack(side="left", padx=(8, 0))
        self._mkbtn(add_row, "Remove Selected", self._remove, "#3d1a1f", "#5a2228", CLR["error"]).pack(side="left", padx=(6, 0))

        footer = tk.Frame(self, bg=CLR["bg"])
        footer.pack(fill="x", padx=20, pady=(0, 14))
        self._mkbtn(footer, "Reset to Defaults", self._reset,
                    CLR["bg"], CLR["surface2"], CLR["text_dim"]).pack(side="left")
        self._mkbtn(footer, "Save", self._save, CLR["accent"], CLR["accent_h"]).pack(side="right")
        self._mkbtn(footer, "Cancel", self.destroy,
                    CLR["bg"], CLR["surface2"], CLR["text_dim"]).pack(side="right", padx=(0, 8))

    def _mkbtn(self, p, text, cmd, bg, bgh, fg=None) -> tk.Button:
        fg = fg or CLR["bg"]
        b = tk.Button(p, text=text, command=cmd, font=("Segoe UI", 10),
                      fg=fg, bg=bg, activeforeground=fg, activebackground=bgh,
                      relief="flat", bd=0, padx=12, pady=7, cursor="hand2")
        b.bind("<Enter>", lambda e: b.config(bg=bgh))
        b.bind("<Leave>", lambda e: b.config(bg=bg))
        return b

    def _refresh(self) -> None:
        self._lb.delete(0, "end")
        for kw in sorted(self._keywords):
            self._lb.insert("end", kw)

    def _add(self) -> None:
        kw = self._new.get().strip().lower()
        if not kw:
            return
        if kw in self._keywords:
            messagebox.showinfo("Duplicate", f"'{kw}' is already in the list.", parent=self)
            return
        self._keywords.append(kw)
        self._refresh()
        self._new.delete(0, "end")

    def _remove(self) -> None:
        sel = list(self._lb.curselection())
        if not sel:
            messagebox.showinfo("Nothing selected", "Select keyword(s) to remove.", parent=self)
            return
        sorted_kws = sorted(self._keywords)
        to_del = {sorted_kws[i] for i in sel}
        self._keywords = [k for k in self._keywords if k not in to_del]
        self._refresh()

    def _reset(self) -> None:
        if messagebox.askyesno("Reset", "Reset to built-in defaults?", parent=self):
            self._keywords = list(DEFAULT_AD_KEYWORDS)
            self._refresh()

    def _save(self) -> None:
        save_ad_keywords(self._keywords)
        self._on_save(list(self._keywords))
        messagebox.showinfo("Saved", "Keywords saved successfully.", parent=self)
        self.destroy()


# ─── Confirmation Dialog ──────────────────────────────────────────────────────

class ConfirmationDialog(tk.Toplevel):
    """Shows flagged subtitle blocks so the user can approve which to remove."""

    def __init__(self, parent: tk.Tk, file_name: str, flagged: list[dict]) -> None:
        super().__init__(parent)
        self.title("Review Flagged Blocks")
        self.configure(bg=CLR["bg"])
        self.resizable(True, True)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._keep_all)
        self.update_idletasks()
        w, h = 620, 500
        px = parent.winfo_x() + (parent.winfo_width()  - w) // 2
        py = parent.winfo_y() + (parent.winfo_height() - h) // 2
        self.geometry(f"{w}x{h}+{px}+{py}")
        self.minsize(500, 380)
        self.result: set[str] | None = None
        self._flagged  = flagged
        self._chk_vars: dict[str, tk.BooleanVar] = {}
        self._build(file_name, flagged)

    def _build(self, file_name: str, flagged: list[dict]) -> None:
        hdr = tk.Frame(self, bg=CLR["surface"], height=58)
        hdr.pack(fill="x"); hdr.pack_propagate(False)
        tk.Label(hdr, text="⚠  Review Flagged Blocks",
                 font=("Segoe UI", 13, "bold"),
                 fg=CLR["warning"], bg=CLR["surface"]).pack(side="left", padx=16, pady=10)

        sub = tk.Frame(self, bg=CLR["bg"])
        sub.pack(fill="x", padx=16, pady=(10, 0))
        tk.Label(sub, text=f"File: {file_name}",
                 font=("Segoe UI", 9, "bold"), fg=CLR["text"], bg=CLR["bg"]).pack(anchor="w")
        tk.Label(sub,
                 text=f"{len(flagged)} block(s) flagged. Uncheck any false positives before removing.",
                 font=("Segoe UI", 9), fg=CLR["text_dim"], bg=CLR["bg"],
                 wraplength=560, justify="left").pack(anchor="w", pady=(2, 0))

        # Scrollable block list
        cf = tk.Frame(self, bg=CLR["bg"])
        cf.pack(fill="both", expand=True, padx=16, pady=10)

        canvas = tk.Canvas(cf, bg=CLR["log_bg"], highlightthickness=1,
                           highlightbackground=CLR["surface2"])
        sb = tk.Scrollbar(cf, orient="vertical", bg=CLR["surface2"],
                          troughcolor=CLR["log_bg"], relief="flat", bd=0, width=10,
                          command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        sf = tk.Frame(canvas, bg=CLR["log_bg"])
        cw = canvas.create_window((0, 0), window=sf, anchor="nw")

        canvas.bind("<Configure>", lambda e: canvas.itemconfig(cw, width=e.width))
        sf.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        def _wheel(event):
            try:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except tk.TclError:
                pass
        canvas.bind_all("<MouseWheel>", _wheel)
        self.bind("<Destroy>", lambda e:
                  canvas.unbind_all("<MouseWheel>") if e.widget is self else None)

        for block in flagged:
            var = tk.BooleanVar(value=True)
            self._chk_vars[block["index"]] = var
            card = tk.Frame(sf, bg=CLR["card"], highlightthickness=1,
                            highlightbackground=CLR["surface2"])
            card.pack(fill="x", padx=8, pady=5, ipadx=4, ipady=4)
            top = tk.Frame(card, bg=CLR["card"])
            top.pack(fill="x", padx=6, pady=(4, 2))
            tk.Checkbutton(top, variable=var,
                           font=("Segoe UI", 9, "bold"), text=f"Block #{block['index']}",
                           fg=CLR["text"], bg=CLR["card"], activebackground=CLR["card"],
                           selectcolor=CLR["surface2"], relief="flat", bd=0,
                           cursor="hand2").pack(side="left")
            tk.Label(top, text=f"  {block['timestamp']}",
                     font=("Consolas", 8), fg=CLR["text_dim"], bg=CLR["card"]).pack(side="left")
            tk.Label(top, text=f"⚠ {block['matched_keyword']}",
                     font=("Segoe UI", 8), fg=CLR["warning"], bg=CLR["card"]).pack(side="right")
            preview = " / ".join(block["text"][:3])
            if len(preview) > 80:
                preview = preview[:77] + "…"
            tk.Label(card, text=preview, font=("Consolas", 9), fg=CLR["info"],
                     bg=CLR["card"], anchor="w", wraplength=540,
                     justify="left").pack(fill="x", padx=10, pady=(0, 4))

        # Select all toggle
        sel_all = tk.BooleanVar(value=True)
        sel_row = tk.Frame(self, bg=CLR["bg"])
        sel_row.pack(fill="x", padx=16)
        tk.Checkbutton(sel_row, text="Select / Deselect All", variable=sel_all,
                       command=lambda: [v.set(sel_all.get()) for v in self._chk_vars.values()],
                       font=("Segoe UI", 9), fg=CLR["text_dim"], bg=CLR["bg"],
                       activebackground=CLR["bg"], selectcolor=CLR["surface2"],
                       relief="flat", bd=0, cursor="hand2").pack(side="left")

        # Footer
        foot = tk.Frame(self, bg=CLR["surface"], height=52)
        foot.pack(fill="x", side="bottom"); foot.pack_propagate(False)

        self._rem_btn = tk.Button(foot, text=f"Remove Checked ({len(flagged)})",
                                  command=self._remove_checked,
                                  font=("Segoe UI", 10, "bold"),
                                  fg=CLR["error"], bg="#3d1a1f",
                                  activeforeground=CLR["error"], activebackground="#5a2228",
                                  relief="flat", bd=0, padx=14, pady=6, cursor="hand2")
        self._rem_btn.pack(side="right", padx=12, pady=8)

        tk.Button(foot, text="Keep All (no changes)", command=self._keep_all,
                  font=("Segoe UI", 10), fg=CLR["text_dim"], bg=CLR["surface"],
                  activeforeground=CLR["text"], activebackground=CLR["surface2"],
                  relief="flat", bd=0, padx=14, pady=6, cursor="hand2").pack(side="right", pady=8)

        def _upd(*_):
            n = sum(1 for v in self._chk_vars.values() if v.get())
            self._rem_btn.config(text=f"Remove Checked ({n})")
        for v in self._chk_vars.values():
            v.trace_add("write", _upd)

    def _remove_checked(self) -> None:
        self.result = {idx for idx, v in self._chk_vars.items() if v.get()}
        self.destroy()

    def _keep_all(self) -> None:
        self.result = set()
        self.destroy()


# ─── Main Application Window ──────────────────────────────────────────────────

class App(tk.Tk):
    """Main application window — two-tab layout (Download & Clean / Subtitle Syncing)."""

    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.configure(bg=CLR["bg"])
        self.resizable(True, True)
        self.minsize(780, 720)

        self.update_idletasks()
        w, h = 900, 830
        x = (self.winfo_screenwidth()  - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        # Set window icon — works both from source and from a PyInstaller bundle
        try:
            # When compiled with PyInstaller, data files land in sys._MEIPASS
            base = Path(getattr(sys, "_MEIPASS", _SCRIPT_DIR))
            ico  = base / "sub-tools.ico"
            if ico.exists():
                self.iconbitmap(str(ico))
        except Exception:
            pass  # icon is cosmetic — never crash on failure

        # Shared state
        self._folder_var      = tk.StringVar(value="")
        self._status_var      = tk.StringVar(value="Ready")
        self._lang_var        = tk.StringVar(value=load_preferred_language())
        self._skip_var        = tk.BooleanVar(value=load_skip_existing())
        self._ad_keywords     = load_ad_keywords()
        self._alass_bin       = find_alass()

        # Sync tab state
        self._sync_video_var  = tk.StringVar(value="")
        self._sync_srt_var    = tk.StringVar(value="")
        self._adv_sync_var    = tk.BooleanVar(value=False)
        self._split_penalty_var = tk.DoubleVar(value=0.1)

        self._log_queue: queue.Queue[logging.LogRecord] = queue.Queue()
        self._stop_event      = threading.Event()
        self._confirm_bridge: ConfirmBridge | None = None
        self._worker: threading.Thread | None = None
        self._running         = False

        self._logger = logging.getLogger("SubTools.GUI")
        self._logger.setLevel(logging.DEBUG)
        self._logger.handlers.clear()
        self._logger.propagate = False
        self._logger.addHandler(QueueHandler(self._log_queue))

        self._setup_styles()
        self._build_ui()
        self._poll_log_queue()

    # ── Styles ────────────────────────────────────────────────────────────────

    def _setup_styles(self) -> None:
        s = ttk.Style(self)
        s.theme_use("default")

        # Notebook
        s.configure("Sub.TNotebook",
                    background=CLR["bg"], borderwidth=0, tabmargins=[0, 0, 0, 0])
        s.configure("Sub.TNotebook.Tab",
                    background=CLR["surface2"], foreground=CLR["text_dim"],
                    padding=[20, 10], font=("Segoe UI", 10), borderwidth=0)
        s.map("Sub.TNotebook.Tab",
              background=[("selected", CLR["surface"]), ("active", CLR["surface2"])],
              foreground=[("selected", CLR["accent"]),  ("active", CLR["text"])])

        # Combobox
        s.configure("Dark.TCombobox",
                    fieldbackground=CLR["surface2"], background=CLR["surface2"],
                    foreground=CLR["text"], selectbackground=CLR["accent"],
                    selectforeground=CLR["bg"], arrowcolor=CLR["text_dim"], borderwidth=0)
        s.map("Dark.TCombobox",
              fieldbackground=[("readonly", CLR["surface2"])],
              foreground=[("readonly", CLR["text"])],
              background=[("readonly", CLR["surface2"])])

        # Progress bars
        s.configure("OSD.Horizontal.TProgressbar",
                    troughcolor=CLR["surface2"], background=CLR["accent"],
                    borderwidth=0, thickness=4)
        s.configure("Sync.Horizontal.TProgressbar",
                    troughcolor=CLR["surface2"], background=CLR["accent2"],
                    borderwidth=0, thickness=4)

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # ── Header ────────────────────────────────────────────────────────────
        header = tk.Frame(self, bg=CLR["surface"], height=64)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(header, text="⬡  Sub-Tools",
                 font=("Segoe UI", 16, "bold"),
                 fg=CLR["accent"], bg=CLR["surface"]).pack(side="left", padx=20, pady=12)
        tk.Label(header, text="Subtitle Download  •  Clean  •  Sync",
                 font=("Segoe UI", 9), fg=CLR["text_dim"],
                 bg=CLR["surface"]).pack(side="left", padx=4)

        hdr_btns = tk.Frame(header, bg=CLR["surface"])
        hdr_btns.pack(side="right", padx=12, pady=12)
        self._mkbtn(hdr_btns, "⚙  Settings", self._open_settings, "ghost").pack(side="left", padx=4)
        self._mkbtn(hdr_btns, "🔤  Keywords", self._open_keywords, "ghost").pack(side="left", padx=4)

        # ── Notebook ──────────────────────────────────────────────────────────
        self._notebook = ttk.Notebook(self, style="Sub.TNotebook")
        self._notebook.pack(fill="x", padx=0, pady=0)

        self._tab_dl   = tk.Frame(self._notebook, bg=CLR["bg"])
        self._tab_sync = tk.Frame(self._notebook, bg=CLR["bg"])
        self._notebook.add(self._tab_dl,   text="⬇  Download & Clean")
        self._notebook.add(self._tab_sync, text="🔄  Subtitle Syncing")

        self._build_tab_download(self._tab_dl)
        self._build_tab_sync(self._tab_sync)

        # Fix notebook height so it never grows beyond the tallest tab,
        # leaving the log area a stable, generous space below.
        self._notebook.configure(height=420)

        # ── Shared log frame ──────────────────────────────────────────────────
        self._build_log_frame()

        # ── Footer ────────────────────────────────────────────────────────────
        footer = tk.Frame(self, bg=CLR["surface"], height=28)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)
        tk.Label(footer, text="OpenSubtitles.com  •  Alass  •  Sub-Tools v1.4",
                 font=("Segoe UI", 8), fg=CLR["text_dim"],
                 bg=CLR["surface"]).pack(side="right", padx=16, pady=6)

    def _build_tab_download(self, tab: tk.Frame) -> None:
        """Builds the Download & Clean tab content."""
        pad = {"padx": 20, "pady": (14, 0)}

        # Folder selection
        f1 = tk.Frame(tab, bg=CLR["bg"])
        f1.pack(fill="x", **pad)
        tk.Label(f1, text="VIDEO FOLDER", font=("Segoe UI", 8, "bold"),
                 fg=CLR["text_dim"], bg=CLR["bg"]).pack(anchor="w")

        f1r = tk.Frame(f1, bg=CLR["bg"])
        f1r.pack(fill="x", pady=(6, 0))
        self._folder_entry = tk.Entry(
            f1r, textvariable=self._folder_var, font=("Segoe UI", 10),
            bg=CLR["surface2"], fg=CLR["text"], insertbackground=CLR["text"],
            relief="flat", bd=0,
            highlightthickness=1, highlightbackground=CLR["surface2"],
            highlightcolor=CLR["accent"])
        self._folder_entry.pack(side="left", fill="x", expand=True, ipady=8, ipadx=10)
        self._btn_browse = self._mkbtn(f1r, "📂  Browse", self._browse_folder, "secondary")
        self._btn_browse.pack(side="left", padx=(8, 0))

        # Language + options
        f2 = tk.Frame(tab, bg=CLR["bg"])
        f2.pack(fill="x", **pad)
        tk.Label(f2, text="SUBTITLE LANGUAGE", font=("Segoe UI", 8, "bold"),
                 fg=CLR["text_dim"], bg=CLR["bg"]).pack(anchor="w")

        f2r = tk.Frame(f2, bg=CLR["bg"])
        f2r.pack(fill="x", pady=(6, 0))
        self._lang_combo = ttk.Combobox(
            f2r, textvariable=self._lang_var, values=list(LANGUAGES.keys()),
            state="readonly", font=("Segoe UI", 10), style="Dark.TCombobox", width=30)
        self._lang_combo.pack(side="left", ipady=6)
        self._lang_combo.bind("<<ComboboxSelected>>", self._on_language_changed)

        # Options: skip existing
        opts = tk.Frame(f2r, bg=CLR["bg"])
        opts.pack(side="left", padx=(16, 0))

        self._chk_skip = tk.Checkbutton(
            opts, text="Skip videos with existing subtitle",
            variable=self._skip_var, command=self._on_skip_changed,
            font=("Segoe UI", 9), fg=CLR["text_dim"], bg=CLR["bg"],
            activebackground=CLR["bg"], selectcolor=CLR["surface2"],
            relief="flat", bd=0, cursor="hand2")
        self._chk_skip.pack(anchor="w")

        # Action buttons
        act = tk.Frame(tab, bg=CLR["bg"])
        act.pack(fill="x", padx=20, pady=14)

        self._btn_download = self._mkbtn(act, "⬇  Download Subtitles",
                                         self._start_download, "primary")
        self._btn_download.pack(side="left")
        self._btn_clean = self._mkbtn(act, "✦  Clean Subtitles",
                                      self._start_clean, "secondary")
        self._btn_clean.pack(side="left", padx=(10, 0))
        self._btn_undo = self._mkbtn(act, "↩  Undo Clean",
                                     self._start_undo, "secondary")
        self._btn_undo.pack(side="left", padx=(10, 0))
        self._btn_stop = self._mkbtn(act, "■  Cancel", self._stop, "danger")
        self._btn_stop.pack(side="left", padx=(10, 0))
        self._btn_stop.config(state="disabled")

        self._progress_dl = ttk.Progressbar(act, style="OSD.Horizontal.TProgressbar",
                                            mode="indeterminate", length=120)
        self._progress_dl.pack(side="left", padx=(14, 0), pady=4)

        tk.Label(act, textvariable=self._status_var,
                 font=("Segoe UI", 9), fg=CLR["text_dim"],
                 bg=CLR["bg"]).pack(side="right")

    def _build_tab_sync(self, tab: tk.Frame) -> None:
        """Builds the standalone Subtitle Syncing tab."""
        # Alass status banner
        alass_ok  = self._alass_bin is not None
        ban_bg    = "#0d2218" if alass_ok else "#2a0d10"
        ban_fg    = CLR["success"] if alass_ok else CLR["error"]
        ban_text  = (f"✓  Alass found:  {self._alass_bin}" if alass_ok
                     else "✗  Alass executable not found.  "
                          "Place it in:  alass-windows64/alass.bat  (or alass.exe)")
        ban = tk.Frame(tab, bg=ban_bg)
        ban.pack(fill="x", padx=20, pady=(14, 0))
        tk.Label(ban, text=ban_text, font=("Segoe UI", 9), fg=ban_fg,
                 bg=ban_bg, wraplength=800, justify="left",
                 padx=12, pady=8).pack(anchor="w")

        pad = {"padx": 20, "pady": (14, 0)}

        # Video file
        f1 = tk.Frame(tab, bg=CLR["bg"])
        f1.pack(fill="x", **pad)
        tk.Label(f1, text="VIDEO FILE", font=("Segoe UI", 8, "bold"),
                 fg=CLR["text_dim"], bg=CLR["bg"]).pack(anchor="w")
        f1r = tk.Frame(f1, bg=CLR["bg"])
        f1r.pack(fill="x", pady=(6, 0))
        self._sync_video_entry = tk.Entry(
            f1r, textvariable=self._sync_video_var, font=("Segoe UI", 10),
            bg=CLR["surface2"], fg=CLR["text"], insertbackground=CLR["text"],
            relief="flat", bd=0,
            highlightthickness=1, highlightbackground=CLR["surface2"],
            highlightcolor=CLR["accent2"])
        self._sync_video_entry.pack(side="left", fill="x", expand=True, ipady=8, ipadx=10)
        self._btn_browse_video = self._mkbtn(f1r, "📂  Browse", self._browse_video, "sync")
        self._btn_browse_video.pack(side="left", padx=(8, 0))

        # Subtitle file
        f2 = tk.Frame(tab, bg=CLR["bg"])
        f2.pack(fill="x", **pad)
        tk.Label(f2, text="SUBTITLE FILE  (out of sync)",
                 font=("Segoe UI", 8, "bold"), fg=CLR["text_dim"],
                 bg=CLR["bg"]).pack(anchor="w")
        f2r = tk.Frame(f2, bg=CLR["bg"])
        f2r.pack(fill="x", pady=(6, 0))
        self._sync_srt_entry = tk.Entry(
            f2r, textvariable=self._sync_srt_var, font=("Segoe UI", 10),
            bg=CLR["surface2"], fg=CLR["text"], insertbackground=CLR["text"],
            relief="flat", bd=0,
            highlightthickness=1, highlightbackground=CLR["surface2"],
            highlightcolor=CLR["accent2"])
        self._sync_srt_entry.pack(side="left", fill="x", expand=True, ipady=8, ipadx=10)
        self._btn_browse_srt = self._mkbtn(f2r, "📂  Browse", self._browse_srt, "sync")
        self._btn_browse_srt.pack(side="left", padx=(8, 0))

        # Rename preview label
        self._rename_preview = tk.Label(
            tab, text="", font=("Segoe UI", 8, "italic"),
            fg=CLR["text_dim"], bg=CLR["bg"])
        self._rename_preview.pack(anchor="w", padx=20, pady=(6, 0))
        self._sync_video_var.trace_add("write", self._update_rename_preview)
        self._sync_srt_var.trace_add("write",   self._update_rename_preview)

        # ── Advanced sync options (Split Penalty) ─────────────────────────────
        adv_frame = tk.Frame(tab, bg=CLR["surface"], relief="flat")
        adv_frame.pack(fill="x", padx=20, pady=(14, 0))

        # Card inner padding
        adv_inner = tk.Frame(adv_frame, bg=CLR["surface"])
        adv_inner.pack(fill="x", padx=14, pady=10)

        # Checkbox
        self._chk_adv_sync = tk.Checkbutton(
            adv_inner,
            text="Enable advanced sync for Series  (Recaps / Scene Cuts)",
            variable=self._adv_sync_var,
            command=self._on_adv_sync_toggled,
            font=("Segoe UI", 9, "bold"), fg=CLR["accent2"], bg=CLR["surface"],
            activebackground=CLR["surface"], selectcolor=CLR["surface2"],
            relief="flat", bd=0, cursor="hand2")
        self._chk_adv_sync.pack(anchor="w")

        # Scale row
        scale_row = tk.Frame(adv_inner, bg=CLR["surface"])
        scale_row.pack(fill="x", pady=(8, 0))

        tk.Label(scale_row, text="Split Penalty:",
                 font=("Segoe UI", 9), fg=CLR["text_dim"],
                 bg=CLR["surface"]).pack(side="left")

        self._split_val_label = tk.Label(
            scale_row, textvariable=self._split_penalty_var,
            font=("Segoe UI", 9, "bold"), fg=CLR["accent2"],
            bg=CLR["surface"], width=4)
        self._split_val_label.pack(side="left", padx=(4, 0))

        self._split_scale = tk.Scale(
            scale_row,
            variable=self._split_penalty_var,
            from_=0.05, to=0.5, resolution=0.05,
            orient="horizontal", length=340,
            bg=CLR["surface"], fg=CLR["text_dim"],
            activebackground=CLR["accent2"],
            highlightthickness=0, sliderrelief="flat",
            troughcolor=CLR["surface2"],
            showvalue=False,
            state="disabled",
        )
        self._split_scale.pack(side="left", padx=(10, 0))

        # Guidance text
        self._split_guidance = tk.Label(
            adv_inner,
            text=(
                "Guidance:  Lower values (0.05 – 0.15) are ideal for TV shows with "
                "\"Previously on\" recaps or commercial-break cuts.  "
                "Higher values (0.2 – 0.5) treat the subtitle as one continuous block, "
                "best for regular movies."
            ),
            font=("Segoe UI", 8), fg=CLR["text_dim"],
            bg=CLR["surface"], justify="left", wraplength=700,
            state="disabled",
        )
        self._split_guidance.pack(anchor="w", pady=(8, 0))

        # Sync button row
        sync_act = tk.Frame(tab, bg=CLR["bg"])
        sync_act.pack(fill="x", padx=20, pady=14)

        self._btn_sync = self._mkbtn(sync_act, "🔄  Sync Subtitle",
                                     self._start_sync, "sync")
        self._btn_sync.pack(side="left")
        if not alass_ok:
            self._btn_sync.config(state="disabled")

        self._btn_stop_sync = self._mkbtn(sync_act, "■  Cancel",
                                          self._stop, "danger")
        self._btn_stop_sync.pack(side="left", padx=(10, 0))
        self._btn_stop_sync.config(state="disabled")

        self._progress_sync = ttk.Progressbar(sync_act, style="Sync.Horizontal.TProgressbar",
                                              mode="indeterminate", length=140)
        self._progress_sync.pack(side="left", padx=(14, 0), pady=4)

        tk.Label(sync_act, textvariable=self._status_var,
                 font=("Segoe UI", 9), fg=CLR["text_dim"],
                 bg=CLR["bg"]).pack(side="right")

        # Naming convention note
        note = tk.Frame(tab, bg=CLR["surface2"])
        note.pack(fill="x", padx=20, pady=(0, 4))
        tk.Label(note,
                 text="📋  Naming convention:  subtitle.srt → subtitle.ori.srt  (backup)  |  "
                      "Output: video_name.srt",
                 font=("Segoe UI", 8), fg=CLR["text_dim"],
                 bg=CLR["surface2"], padx=10, pady=6).pack(anchor="w")

    def _build_log_frame(self) -> None:
        """Shared log area — visible regardless of active tab."""
        log_sec = tk.Frame(self, bg=CLR["bg"])
        log_sec.pack(fill="both", expand=True, padx=20, pady=(4, 8))

        log_hdr = tk.Frame(log_sec, bg=CLR["bg"])
        log_hdr.pack(fill="x")
        tk.Label(log_hdr, text="EXECUTION LOG", font=("Segoe UI", 8, "bold"),
                 fg=CLR["text_dim"], bg=CLR["bg"]).pack(side="left")
        self._mkbtn(log_hdr, "Clear", self._clear_log, "ghost").pack(side="right")

        log_outer = tk.Frame(log_sec, bg=CLR["accent"])
        log_outer.pack(fill="both", expand=True, pady=(6, 0))
        log_inner = tk.Frame(log_outer, bg=CLR["log_bg"])
        log_inner.pack(fill="both", expand=True, padx=1, pady=1)

        sb = tk.Scrollbar(log_inner, bg=CLR["surface2"], troughcolor=CLR["log_bg"],
                          activebackground=CLR["accent"], relief="flat", bd=0, width=10)
        sb.pack(side="right", fill="y")

        mono = ("Cascadia Code", 9) if self._font_exists("Cascadia Code") else ("Consolas", 9)
        self._log_text = tk.Text(
            log_inner, font=mono, bg=CLR["log_bg"], fg=CLR["text"],
            insertbackground=CLR["text"], relief="flat", bd=0, wrap="word",
            state="disabled", yscrollcommand=sb.set, padx=12, pady=8, cursor="arrow")
        self._log_text.pack(side="left", fill="both", expand=True)
        sb.config(command=self._log_text.yview)

        for tag, fg in [("INFO",      CLR["text"]),     ("DEBUG",   CLR["text_dim"]),
                         ("WARNING",   CLR["warning"]),  ("ERROR",   CLR["error"]),
                         ("SUCCESS",   CLR["success"]),  ("TIME",    CLR["text_dim"]),
                         ("ACCENT",    CLR["accent"]),   ("ACCENT2", CLR["accent2"]),
                         ("UNCHANGED", CLR["text_dim"])]:
            self._log_text.tag_config(tag, foreground=fg)

    # ── Widget helpers ────────────────────────────────────────────────────────

    def _mkbtn(self, parent, text, cmd, style="primary") -> tk.Button:
        palettes = {
            "primary":   (CLR["accent"],   CLR["accent_h"],  CLR["bg"],       "bold"),
            "secondary": (CLR["surface2"], CLR["surface"],   CLR["text"],     "normal"),
            "danger":    ("#3d1a1f",       "#5a2228",        CLR["error"],    "normal"),
            "ghost":     (CLR["bg"],       CLR["surface2"],  CLR["text_dim"], "normal"),
            "sync":      (CLR["accent2"],  CLR["accent2_h"], CLR["bg"],       "bold"),
        }
        bg, bgh, fg, w = palettes[style]
        b = tk.Button(parent, text=text, command=cmd,
                      font=("Segoe UI", 10, w), fg=fg, bg=bg,
                      activeforeground=fg, activebackground=bgh,
                      relief="flat", bd=0, padx=13, pady=8, cursor="hand2")
        b.bind("<Enter>", lambda e: b.config(bg=bgh))
        b.bind("<Leave>", lambda e: b.config(bg=bg))
        return b

    @staticmethod
    def _font_exists(name: str) -> bool:
        try:
            import tkinter.font as tkfont
            return name in tkfont.families()
        except Exception:
            return False

    # ── Sync tab helpers ──────────────────────────────────────────────────────

    def _update_rename_preview(self, *_) -> None:
        v = self._sync_video_var.get().strip()
        s = self._sync_srt_var.get().strip()
        if not v or not s:
            self._rename_preview.config(text="")
            return
        vp, sp = Path(v), Path(s)
        ori_name = f"{sp.stem}.ori.srt"
        out_name = f"{vp.stem}.srt"
        self._rename_preview.config(
            text=f"Will rename:  {sp.name}  →  {ori_name}   |   Output:  {out_name}")

    def _browse_video(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))
        path = filedialog.askopenfilename(
            title="Select video file",
            filetypes=[("Video files", exts), ("All files", "*.*")])
        if path:
            self._sync_video_var.set(path)

    def _browse_srt(self) -> None:
        path = filedialog.askopenfilename(
            title="Select subtitle file (out of sync)",
            filetypes=[("Subtitle files", "*.srt"), ("All files", "*.*")])
        if path:
            self._sync_srt_var.set(path)

    # ── General actions ───────────────────────────────────────────────────────

    def _browse_folder(self) -> None:
        path = filedialog.askdirectory(title="Select the folder containing video files")
        if path:
            self._folder_var.set(path)

    def _open_settings(self) -> None:
        SettingsDialog(self)

    def _open_keywords(self) -> None:
        KeywordsDialog(self, self._ad_keywords, self._on_keywords_saved)

    def _on_keywords_saved(self, kws: list[str]) -> None:
        self._ad_keywords = kws
        self._append_log("INFO", f"Ad keywords updated ({len(kws)} active).")

    def _on_language_changed(self, _=None) -> None:
        label = self._lang_var.get()
        primary, _ = LANGUAGES.get(label, ("pt-br", []))
        save_env_value("MY_LANGUAGE", primary)

    def _on_skip_changed(self) -> None:
        save_skip_existing(self._skip_var.get())

    def _on_adv_sync_toggled(self) -> None:
        """Enable or disable the split-penalty scale based on the checkbox state."""
        enabled = self._adv_sync_var.get()
        state = "normal" if enabled else "disabled"
        self._split_scale.config(state=state)
        self._split_guidance.config(
            fg=CLR["text"] if enabled else CLR["text_dim"]
        )

    def _get_folder(self) -> Path | None:
        s = self._folder_var.get().strip()
        if not s:
            self._append_log("ERROR", "Please select a folder before starting.")
            return None
        p = Path(s)
        if not p.is_dir():
            self._append_log("ERROR", f"Invalid folder: {p}")
            return None
        return p

    # ── Download & Clean tab actions ──────────────────────────────────────────

    def _start_download(self) -> None:
        folder = self._get_folder()
        if folder is None:
            return
        label = self._lang_var.get()
        primary, fallbacks = LANGUAGES.get(label, ("pt-br", ["pt"]))
        lang_codes    = [primary] + fallbacks
        skip_existing = self._skip_var.get()

        self._confirm_bridge = None
        self._stop_event.clear()
        self._set_running(True)

        self._append_log("ACCENT", "═" * 52)
        self._append_log("ACCENT", f"  Download Subtitles  —  {datetime.now().strftime('%m/%d/%Y %H:%M:%S')}")
        self._append_log("ACCENT", f"  Folder      : {folder}")
        self._append_log("ACCENT", f"  Language    : {label}")
        self._append_log("ACCENT", f"  Skip exist  : {'Yes' if skip_existing else 'No (add lang suffix)'}")
        self._append_log("ACCENT", "═" * 52)

        self._worker = threading.Thread(
            target=self._run_worker,
            args=(run_download, folder, lang_codes, skip_existing),
            daemon=True,
        )
        self._worker.start()

    def _start_clean(self) -> None:
        folder = self._get_folder()
        if folder is None:
            return
        self._confirm_bridge = ConfirmBridge()
        self._stop_event.clear()
        self._set_running(True)
        kws = list(self._ad_keywords)
        self._append_log("ACCENT", "═" * 52)
        self._append_log("ACCENT", f"  Clean Subtitles  —  {datetime.now().strftime('%m/%d/%Y %H:%M:%S')}")
        self._append_log("ACCENT", f"  Folder   : {folder}")
        self._append_log("ACCENT", f"  Keywords : {', '.join(kws)}")
        self._append_log("ACCENT", "═" * 52)
        self._worker = threading.Thread(
            target=self._run_worker,
            args=(run_clean, folder, kws, self._confirm_bridge),
            daemon=True,
        )
        self._worker.start()

    def _start_undo(self) -> None:
        folder = self._get_folder()
        if folder is None:
            return
        if not messagebox.askyesno("Undo Clean",
                                   f"Restore all cleaned subtitles in:\n{folder}\n\nContinue?"):
            return
        self._confirm_bridge = None
        self._stop_event.clear()
        self._set_running(True)
        self._append_log("ACCENT", "═" * 52)
        self._append_log("ACCENT", f"  Undo Clean  —  {datetime.now().strftime('%m/%d/%Y %H:%M:%S')}")
        self._append_log("ACCENT", f"  Folder : {folder}")
        self._append_log("ACCENT", "═" * 52)
        self._worker = threading.Thread(
            target=self._run_worker, args=(run_undo, folder), daemon=True)
        self._worker.start()

    # ── Sync tab actions ──────────────────────────────────────────────────────

    def _start_sync(self) -> None:
        video_str = self._sync_video_var.get().strip()
        srt_str   = self._sync_srt_var.get().strip()

        if not video_str:
            self._append_log("ERROR", "Please select a video file.")
            return
        if not srt_str:
            self._append_log("ERROR", "Please select a subtitle file.")
            return

        video = Path(video_str)
        srt   = Path(srt_str)

        if not video.is_file():
            self._append_log("ERROR", f"Video file not found: {video}")
            return
        if not srt.is_file():
            self._append_log("ERROR", f"Subtitle file not found: {srt}")
            return
        if not self._alass_bin:
            messagebox.showerror("Alass Not Found",
                                 "Alass executable was not found.\n"
                                 "Place it in the alass-windows64 folder.")
            return

        adv_sync     = self._adv_sync_var.get()
        split_penalty = round(self._split_penalty_var.get(), 2) if adv_sync else None

        self._confirm_bridge = None
        self._stop_event.clear()
        self._set_running(True)

        ori_name = f"{srt.stem}.ori.srt"
        out_name = f"{video.stem}.srt"

        self._append_log("ACCENT2", "═" * 52)
        self._append_log("ACCENT2", f"  Subtitle Syncing  —  {datetime.now().strftime('%m/%d/%Y %H:%M:%S')}")
        self._append_log("ACCENT2", f"  Video    : {video.name}")
        self._append_log("ACCENT2", f"  Subtitle : {srt.name}  →  {ori_name}")
        self._append_log("ACCENT2", f"  Output   : {out_name}")
        if split_penalty is not None:
            self._append_log("ACCENT2", f"  Mode     : Advanced Series Sync  (split-penalty={split_penalty:.2f})")
        else:
            self._append_log("ACCENT2",  "  Mode     : Standard Sync")
        self._append_log("ACCENT2", "═" * 52)

        alass_bin = self._alass_bin
        self._worker = threading.Thread(
            target=self._sync_thread,
            args=(video, srt, alass_bin, split_penalty),
            daemon=True,
        )
        self._worker.start()

    def _sync_thread(self, video: Path, srt: Path, alass_bin: Path,
                     split_penalty: float | None) -> None:
        success, detail = run_alass_sync(alass_bin, video, srt,
                                         self._logger, self._stop_event,
                                         split_penalty=split_penalty)
        self.after(0, lambda: self._on_sync_done(success, video, detail))
        self.after(0, self._on_done)

    def _on_sync_done(self, success: bool, video: Path, detail: str) -> None:
        if self._stop_event.is_set():
            return
        if success:
            out = video.parent / f"{video.stem}.srt"
            messagebox.showinfo(
                "Subtitle Synced ✓",
                f"Subtitle synchronized successfully!\n\nSaved as:\n{out.name}",
            )
        else:
            messagebox.showerror(
                "Sync Failed",
                f"Alass could not synchronize the subtitle.\n\nDetails:\n{detail}",
            )

    # ── Generic background worker ─────────────────────────────────────────────

    def _run_worker(self, fn, first_arg, *extra) -> None:
        """Calls fn(first_arg, *extra, logger, stop_event) in the current thread."""
        try:
            fn(first_arg, *extra, self._logger, self._stop_event)
        finally:
            self.after(0, self._on_done)

    def _on_done(self) -> None:
        self._set_running(False)
        self._confirm_bridge = None

    def _stop(self) -> None:
        self._stop_event.set()
        if self._confirm_bridge:
            self._confirm_bridge.cancel()
        self._status_var.set("Cancelling…")
        self._append_log("WARNING", "Cancellation requested. Finishing current operation…")

    def _clear_log(self) -> None:
        self._log_text.config(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.config(state="disabled")

    def _set_running(self, running: bool) -> None:
        self._running = running
        dl_btns   = [self._btn_download, self._btn_clean,
                     self._btn_undo, self._btn_browse]
        sync_btns = [self._btn_sync, self._btn_browse_video, self._btn_browse_srt]
        all_btns  = dl_btns + sync_btns

        if running:
            for b in all_btns:
                b.config(state="disabled")
            self._btn_stop.config(state="normal")
            self._btn_stop_sync.config(state="normal")
            self._lang_combo.config(state="disabled")
            self._chk_skip.config(state="disabled")
            self._progress_dl.start(12)
            self._progress_sync.start(12)
            self._status_var.set("Processing…")
        else:
            for b in all_btns:
                b.config(state="normal")
            # Re-disable sync button if alass not found
            if not self._alass_bin:
                self._btn_sync.config(state="disabled")
            self._btn_stop.config(state="disabled")
            self._btn_stop_sync.config(state="disabled")
            self._lang_combo.config(state="readonly")
            self._chk_skip.config(state="normal")
            self._progress_dl.stop()
            self._progress_sync.stop()
            self._status_var.set("Done" if not self._stop_event.is_set() else "Cancelled")

    # ── Log display ───────────────────────────────────────────────────────────

    def _poll_log_queue(self) -> None:
        try:
            while True:
                self._display_record(self._log_queue.get_nowait())
        except queue.Empty:
            pass

        if self._confirm_bridge and self._running:
            req = self._confirm_bridge.get_pending()
            if req:
                self._show_confirmation(req)

        self.after(80, self._poll_log_queue)

    def _show_confirmation(self, request: dict) -> None:
        dlg = ConfirmationDialog(self, request["file"], request["blocks"])
        self.wait_window(dlg)
        self._confirm_bridge.send_response(
            dlg.result if dlg.result is not None else set()
        )

    def _display_record(self, record: logging.LogRecord) -> None:
        ts  = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        msg = record.getMessage()
        if record.levelno == logging.INFO and "success" in msg.lower():
            tag = "SUCCESS"
        elif record.levelno == logging.INFO and "unchanged" in msg.lower():
            tag = "UNCHANGED"
        elif record.levelno == logging.INFO and ("═" in msg or "─" in msg or "✔" in msg):
            tag = "ACCENT"
        elif record.levelno == logging.INFO and ("alass" in msg.lower() or "sync" in msg.lower()):
            tag = "ACCENT2"
        else:
            tag = record.levelname
        self._append_log(tag, msg, ts)

    def _append_log(self, tag: str, message: str, ts: str | None = None) -> None:
        if ts is None:
            ts = datetime.now().strftime("%H:%M:%S")
        self._log_text.config(state="normal")
        self._log_text.insert("end", f"{ts}  ", "TIME")
        self._log_text.insert("end", f"{message}\n", tag)
        self._log_text.see("end")
        self._log_text.config(state="disabled")


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
