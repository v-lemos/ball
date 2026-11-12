import datetime
import json
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent

logger = logging.getLogger("stream_recorder")


def sanitize_slug(title: str) -> str:
    """Sanitizes a match title for safe filesystem filenames."""
    if not title:
        return "match"
    # Replace non-alphanumeric chars with underscore
    slug = re.sub(r"[^\w\s-]", "", title).strip()
    slug = re.sub(r"[-\s]+", "_", slug)
    return slug[:50] or "match"


def get_clips_dir() -> Path:
    """Returns the output/clips directory, creating it if needed."""
    clips_dir = PROJECT_ROOT / "output" / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    return clips_dir


def send_mpv_command(socket_path: str, command: List[Any], timeout: float = 1.5) -> Optional[Dict[str, Any]]:
    """Sends a JSON command to the MPV IPC server and waits for the matching response."""
    if not os.path.exists(socket_path):
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(socket_path)
            req_id = int(time.time() * 1000) % 1000000
            msg = {"command": command, "request_id": req_id}
            s.sendall(json.dumps(msg).encode("utf-8") + b"\n")

            buf = ""
            while True:
                chunk = s.recv(4096).decode("utf-8", errors="ignore")
                if not chunk:
                    break
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("request_id") == req_id:
                            return data
                    except Exception:
                        pass
    except Exception as e:
        logger.debug(f"MPV IPC command {command} error: {e}")
    return None


def send_mpv_notify(socket_path: str, message: str, duration_ms: int = 3000) -> None:
    """Sends an OSD notification to MPV without blocking for response."""
    if not os.path.exists(socket_path):
        return
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(socket_path)
            msg = {"command": ["show-text", message, duration_ms]}
            s.sendall(json.dumps(msg).encode("utf-8") + b"\n")
    except Exception:
        pass


def check_ffmpeg_installed() -> str:
    """Verifies that ffmpeg is installed and returns its path.

    Raises RuntimeError with installation instructions if not found.
    """
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin and os.path.exists("/opt/homebrew/bin/ffmpeg"):
        ffmpeg_bin = "/opt/homebrew/bin/ffmpeg"
    if not ffmpeg_bin:
        raise RuntimeError(
            "❌ 'ffmpeg' is required for clipping and saving replays but was not found in your PATH.\n"
            "Please install it:\n"
            "   • macOS:   brew install ffmpeg\n"
            "   • Ubuntu:  sudo apt install ffmpeg\n"
            "   • Arch:    sudo pacman -S ffmpeg\n"
            "   • Windows: winget install Gyan.FFmpeg"
        )
    return ffmpeg_bin


def remux_to_mp4(input_path: Path, output_path: Path) -> bool:
    """Fast remux of TS/MKV to MP4 without re-encoding using ffmpeg copy."""
    try:
        ffmpeg_bin = check_ffmpeg_installed()
    except RuntimeError as e:
        logger.error(str(e))
        return False

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(input_path),
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
        str(output_path),
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
        return res.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0
    except Exception as e:
        logger.warning(f"FFmpeg remux failed: {e}")
        return False


class StreamRecorder:
    """Manages MPV clip recording (start/stop) and instant goal/replay buffer downloads."""

    def __init__(self, socket_path: str, trigger_file: Path):
        self.socket_path = socket_path
        self.trigger_file = trigger_file
        self.is_recording: bool = False
        self.rec_start_time: Optional[float] = None
        self.rec_start_pts: Optional[float] = None
        self.rec_raw_path: Optional[Path] = None
        self.current_match_title: str = ""

    def check_trigger(self) -> Optional[str]:
        """Checks if MPV Lua script wrote an action trigger to the trigger file."""
        if not self.trigger_file.exists():
            return None
        try:
            content = self.trigger_file.read_text(encoding="utf-8").strip()
            try:
                self.trigger_file.unlink(missing_ok=True)
            except Exception:
                pass
            if content:
                char = content[0].lower()
                if char in ("a", "c", "g"):
                    return char
        except Exception:
            pass
        return None

    def notify_mpv(self, message: str, duration_ms: int = 3000):
        """Sends an on-screen display notification to the MPV player window."""
        send_mpv_notify(self.socket_path, message, duration_ms)

    def get_elapsed_seconds(self) -> float:
        """Returns elapsed recording duration in seconds."""
        if not self.is_recording or self.rec_start_time is None:
            return 0.0
        return max(0.0, time.time() - self.rec_start_time)

    def get_elapsed_str(self) -> str:
        """Returns formatted MM:SS elapsed recording duration."""
        sec = int(self.get_elapsed_seconds())
        mins = sec // 60
        rem_sec = sec % 60
        return f"{mins:02d}:{rem_sec:02d}"

    def start_recording(self, match_title: str) -> Tuple[bool, str]:
        """Starts recording incoming video packets into a raw capture file."""
        if self.is_recording:
            elapsed = int(self.get_elapsed_seconds())
            msg = f"⚠️ Already recording clip ({elapsed}s elapsed). Press 'c' to save."
            self.notify_mpv(msg)
            return False, msg

        clips_dir = get_clips_dir()
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.rec_raw_path = clips_dir / f".raw_rec_{now_str}.mkv"
        self.rec_start_time = time.time()
        self.current_match_title = match_title

        # Query current playback PTS for precise cache-dump fallback
        pts_res = send_mpv_command(self.socket_path, ["get_property", "playback-time"])
        if pts_res and pts_res.get("data") is not None:
            try:
                self.rec_start_pts = float(pts_res["data"])
            except (ValueError, TypeError):
                self.rec_start_pts = None
        else:
            self.rec_start_pts = None

        # Tell MPV to begin writing incoming demuxer packets
        res = send_mpv_command(self.socket_path, ["set_property", "stream-record", str(self.rec_raw_path)])
        if not res or res.get("error") != "success":
            self.rec_raw_path = None
            self.rec_start_time = None
            self.rec_start_pts = None
            msg = "❌ Failed to start recording in MPV."
            self.notify_mpv(msg)
            return False, msg

        self.is_recording = True
        msg = "🔴 Recording started! Press 'c' to stop & save clip."
        self.notify_mpv("🔴 Recording clip started...\nPress 'c' to stop & save", 3000)
        return True, msg

    def _resolve_cache_pts_range(
        self,
        max_duration: Optional[float] = None,
        preferred_start_pts: Optional[float] = None,
        preferred_end_pts: Optional[float] = None,
    ) -> Tuple[Optional[float], Optional[float]]:
        """Resolves valid start and end PTS timestamps from MPV cache state.

        If max_duration is None, resolves the entire available rewind buffer.
        """
        # 1. If preferred PTS range is strictly forward-progressing, use it
        if (
            preferred_start_pts is not None
            and preferred_end_pts is not None
            and preferred_end_pts > preferred_start_pts
        ):
            return preferred_start_pts, preferred_end_pts

        # 2. Query demuxer cache state
        cache_res = send_mpv_command(self.socket_path, ["get_property", "demuxer-cache-state"])
        if not cache_res or cache_res.get("error") != "success":
            return None, None

        cache_data = cache_res.get("data", {})
        ranges = cache_data.get("seekable-ranges", [])

        if ranges:
            latest = ranges[-1]
            r_start = latest.get("start", 0.0)
            r_end = latest.get("end", 0.0)
            if r_end > r_start:
                total_dur = r_end - r_start
                if max_duration is not None and max_duration > 0:
                    clip_dur = min(max_duration, total_dur)
                    return max(r_start, r_end - clip_dur), r_end
                else:
                    # Download EVERYTHING available in the buffer
                    return r_start, r_end

        # 3. Fallback to cache-end or reader-pts
        cache_end = cache_data.get("cache-end")
        reader_pts = cache_data.get("reader-pts")
        cache_dur = cache_data.get("cache-duration", 0.0)

        if cache_end is not None and cache_end > 0:
            end_pts = float(cache_end)
            if max_duration is not None and max_duration > 0:
                clip_dur = min(max_duration, cache_dur if cache_dur > 0 else max_duration)
            else:
                clip_dur = cache_dur if cache_dur > 0 else end_pts
            return max(0.0, end_pts - clip_dur), end_pts
        elif reader_pts is not None and reader_pts > 0:
            end_pts = float(reader_pts)
            if max_duration is not None and max_duration > 0:
                clip_dur = min(max_duration, cache_dur if cache_dur > 0 else max_duration)
            else:
                clip_dur = cache_dur if cache_dur > 0 else end_pts
            return max(0.0, end_pts - clip_dur), end_pts

        return None, None

    def stop_recording(self) -> Tuple[Optional[Path], str]:
        """Stops active recording and remuxes to a standalone MP4 clip."""
        if not self.is_recording or not self.rec_raw_path:
            msg = "⚠️ No active recording to stop. Press 'a' to start recording."
            self.notify_mpv(msg)
            return None, msg

        duration = max(1, round(self.get_elapsed_seconds()))
        raw_path = self.rec_raw_path
        start_pts = self.rec_start_pts
        match_title = self.current_match_title or "match"

        # Query end PTS
        pts_res = send_mpv_command(self.socket_path, ["get_property", "playback-time"])
        end_pts = None
        if pts_res and pts_res.get("data") is not None:
            try:
                end_pts = float(pts_res["data"])
            except (ValueError, TypeError):
                pass

        # 1. Stop MPV stream recording
        send_mpv_command(self.socket_path, ["set_property", "stream-record", ""])
        self.is_recording = False
        self.rec_raw_path = None
        self.rec_start_time = None
        self.rec_start_pts = None
        self.notify_mpv("💾 Saving and finalizing clip...", 2000)

        # 2. Wait for MPV to finish flushing file to disk
        for _ in range(15):
            time.sleep(0.1)
            if raw_path.exists() and raw_path.stat().st_size > 0:
                cur_sz = raw_path.stat().st_size
                time.sleep(0.1)
                if raw_path.stat().st_size == cur_sz:
                    break

        # Fallback to cache-dump if stream-record was empty (e.g. pre-buffered playback)
        if not raw_path.exists() or raw_path.stat().st_size == 0:
            c_start, c_end = self._resolve_cache_pts_range(
                max_duration=float(duration),
                preferred_start_pts=start_pts,
                preferred_end_pts=end_pts,
            )
            if c_start is not None and c_end is not None and c_end > c_start:
                clips_dir = get_clips_dir()
                now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                cache_dump_path = clips_dir / f".raw_clip_dump_{now_str}.ts"
                dump_res = send_mpv_command(self.socket_path, ["dump-cache", c_start, c_end, str(cache_dump_path)])
                if dump_res and dump_res.get("error") == "success":
                    for _ in range(20):
                        time.sleep(0.1)
                        if cache_dump_path.exists() and cache_dump_path.stat().st_size > 0:
                            cur_sz = cache_dump_path.stat().st_size
                            time.sleep(0.1)
                            if cache_dump_path.stat().st_size == cur_sz:
                                break
                    if cache_dump_path.exists() and cache_dump_path.stat().st_size > 0:
                        if raw_path.exists():
                            raw_path.unlink(missing_ok=True)
                        raw_path = cache_dump_path

        if not raw_path.exists() or raw_path.stat().st_size == 0:
            if raw_path.exists():
                try:
                    raw_path.unlink(missing_ok=True)
                except Exception:
                    pass
            msg = "⚠️ Clip was too short or empty. Nothing saved."
            self.notify_mpv(msg)
            return None, msg

        # 3. Destination path
        slug = sanitize_slug(match_title)
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_path = get_clips_dir() / f"clip_{slug}_{now_str}_{duration}s.mp4"

        # 4. Remux to MP4
        success = remux_to_mp4(raw_path, dest_path)
        try:
            raw_path.unlink(missing_ok=True)
        except Exception:
            pass

        if success and dest_path.exists() and dest_path.stat().st_size > 0:
            msg = f"✅ Clip saved: {dest_path.name} ({duration}s)"
            self.notify_mpv(f"✅ Clip saved!\n{dest_path.name} ({duration}s)", 4000)
            return dest_path, msg
        else:
            msg = "❌ Failed to remux clip to MP4."
            self.notify_mpv(msg)
            return None, msg

    def clip_replay_buffer(self, match_title: str, max_duration_sec: Optional[float] = None) -> Tuple[Optional[Path], str]:
        """Dumps everything available from what came before in the MPV RAM cache."""
        self.notify_mpv("⚡ Downloading replay buffer...", 2000)

        # 1. Resolve PTS range from demuxer cache (all available if max_duration_sec is None)
        start_pts, end_pts = self._resolve_cache_pts_range(max_duration=max_duration_sec)
        if start_pts is None or end_pts is None or end_pts <= start_pts:
            msg = "⚠️ Replay buffer not ready yet. Please wait a few seconds."
            self.notify_mpv(msg)
            return None, msg

        duration = max(1, round(end_pts - start_pts))
        clips_dir = get_clips_dir()
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        raw_dump_path = clips_dir / f".raw_replay_{now_str}.ts"

        # 2. Dump cache from MPV
        res = send_mpv_command(self.socket_path, ["dump-cache", start_pts, end_pts, str(raw_dump_path)])
        if not res or res.get("error") != "success":
            msg = "❌ Failed to dump replay buffer from MPV cache."
            self.notify_mpv(msg)
            return None, msg

        # Give MPV a moment to write cache to disk (wait up to 2s for size to stabilize)
        for _ in range(20):
            time.sleep(0.1)
            if raw_dump_path.exists() and raw_dump_path.stat().st_size > 0:
                cur_sz = raw_dump_path.stat().st_size
                time.sleep(0.1)
                if raw_dump_path.stat().st_size == cur_sz:
                    break

        if not raw_dump_path.exists() or raw_dump_path.stat().st_size == 0:
            if raw_dump_path.exists():
                try:
                    raw_dump_path.unlink(missing_ok=True)
                except Exception:
                    pass
            msg = "⚠️ Replay buffer was empty. Try again in a few seconds."
            self.notify_mpv(msg)
            return None, msg

        # 3. Remux to MP4
        slug = sanitize_slug(match_title)
        dest_path = clips_dir / f"replay_{slug}_{now_str}_{duration}s.mp4"
        success = remux_to_mp4(raw_dump_path, dest_path)
        try:
            raw_dump_path.unlink(missing_ok=True)
        except Exception:
            pass

        if success and dest_path.exists() and dest_path.stat().st_size > 0:
            msg = f"⚡ Replay saved: {dest_path.name} ({duration}s)"
            self.notify_mpv(f"⚡ Replay saved!\n{dest_path.name} ({duration}s)", 4000)
            return dest_path, msg
        else:
            msg = "❌ Failed to remux replay buffer to MP4."
            self.notify_mpv(msg)
            return None, msg

    # Backward compatibility alias
    clip_last_minute = clip_replay_buffer

    def cleanup(self) -> None:
        """Cleans up active recordings and temporary trigger files."""
        if self.is_recording and self.rec_raw_path:
            try:
                self.stop_recording()
            except Exception:
                pass
        try:
            self.trigger_file.unlink(missing_ok=True)
        except Exception:
            pass
        if os.path.exists(self.socket_path):
            try:
                os.remove(self.socket_path)
            except Exception:
                pass
