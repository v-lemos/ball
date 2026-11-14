import asyncio
import http.server
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import urllib.request
import zlib

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

logger = logging.getLogger("player_launcher")


def check_mpv_installed() -> str:
    """Verifies that mpv is installed and returns its path.

    Raises RuntimeError with installation instructions if not found.
    """
    mpv_bin = shutil.which("mpv")
    if not mpv_bin:
        raise RuntimeError(
            "❌ 'mpv' media player is required but was not found in your PATH.\n"
            "Please install it:\n"
            "   • macOS:   brew install mpv\n"
            "   • Ubuntu:  sudo apt install mpv\n"
            "   • Arch:    sudo pacman -S mpv\n"
            "   • Windows: winget install mpv.mpv"
        )
    return mpv_bin


def find_free_port(start_port: int = 9999) -> int:
    """Finds an available TCP port starting from start_port."""
    for port in range(start_port, start_port + 100):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    # Let OS assign an ephemeral port if all checked are busy
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ReusableTCPServer(socketserver.TCPServer):
    """TCPServer that enables SO_REUSEADDR before binding to prevent Errno 48."""
    allow_reuse_address = True
    daemon_threads = True


def unpack_png_stream_segment(data: bytes) -> bytes:
    """Detects and unpacks MPEG-TS video chunks obfuscated inside PNG images.

    Modern restream hosts hide raw MPEG-TS inside a PNG IDAT payload. This unpacks the
    zlib-compressed scanlines and strips the leading 0x00 filter byte from each row,
    restoring standard 188-byte synchronized MPEG-TS packets.
    """
    if not data or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data

    try:
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        idat_pos = data.find(b"IDAT")
        if idat_pos == -1:
            return data
        idat_len = int.from_bytes(data[idat_pos - 4 : idat_pos], "big")
        compressed_payload = data[idat_pos + 4 : idat_pos + 4 + idat_len]
        decompressed = zlib.decompress(compressed_payload)

        row_len = width * 3  # RGB 8-bit
        scanline_len = 1 + row_len  # 1 filter byte + row bytes
        output_ts = bytearray()
        for r in range(height):
            start = r * scanline_len + 1  # Skip the 0x00 PNG filter byte
            output_ts.extend(decompressed[start : start + row_len])
        return bytes(output_ts)
    except Exception as e:
        logger.debug(f"Segment PNG unpack error: {e}")
        return data


class HlsRelayHandler(http.server.BaseHTTPRequestHandler):
    """Serves the latest intercepted M3U8 playlist and proxies/unpacks video segments."""

    server_instance: "HlsRelayServer"

    def log_message(self, format, *args):
        # Suppress noisy HTTP request logging
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path in ("/live.m3u8", "/"):
            content = self.server_instance.latest_playlist
            if not content:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"Playlist not ready yet.")
                return

            # Rewrite segment URLs so mpv downloads them through our local unmasking relay
            rewritten_lines = []
            relay_base = f"http://127.0.0.1:{self.server_instance.port}/chunk?url="
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("http://") or stripped.startswith("https://"):
                    encoded_url = urllib.parse.quote(stripped, safe="")
                    rewritten_lines.append(f"{relay_base}{encoded_url}")
                else:
                    rewritten_lines.append(line)

            data = ("\n".join(rewritten_lines) + "\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        elif parsed.path == "/chunk":
            query = urllib.parse.parse_qs(parsed.query)
            target_url = query.get("url", [None])[0]
            if not target_url:
                self.send_response(400)
                self.end_headers()
                return

            try:
                headers = {
                    "Referer": "https://hls.st/",
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    ),
                    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"macOS"',
                }
                req = urllib.request.Request(target_url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    raw_chunk = resp.read()

                # Unpack PNG disguised video chunks to pristine MPEG-TS
                clean_ts = unpack_png_stream_segment(raw_chunk)

                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(clean_ts)))
                self.end_headers()
                self.wfile.write(clean_ts)
            except Exception as e:
                logger.debug(f"Error proxying chunk: {e}")
                self.send_response(502)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


class HlsRelayServer:
    """Lightweight in-memory HTTP server that relays HLS playlists to mpv."""

    def __init__(self, port: Optional[int] = None):
        self.port = port or find_free_port()
        self.latest_playlist = ""
        self._server: Optional[ReusableTCPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._playlist_ready_event = threading.Event()

    def update_playlist(self, content: str):
        if not content or "#EXTM3U" not in content:
            return
        # Ignore placeholder/empty endlist manifests without media segments
        if "#EXT-X-ENDLIST" in content and "#EXTINF" not in content:
            return
        # Accept valid manifests with actual media chunks or sub-stream playlists
        if "#EXTINF" in content or "#EXT-X-STREAM-INF" in content:
            self.latest_playlist = content
            self._playlist_ready_event.set()

    def wait_for_playlist(self, timeout: float = 15.0) -> bool:
        return self._playlist_ready_event.wait(timeout=timeout)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/live.m3u8"

    def start(self):
        handler_cls = type(
            "BoundHlsRelayHandler",
            (HlsRelayHandler,),
            {"server_instance": self},
        )
        for attempt in range(10):
            try:
                self._server = ReusableTCPServer(("127.0.0.1", self.port), handler_cls)
                break
            except OSError:
                self.port = find_free_port(self.port + 1)

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info(f"HLS Relay Server running on {self.url}")

    def stop(self):
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None


def launch_mpv_process(
    stream_url: str,
    title: str = "Soccer Streams - Live",
    referer: Optional[str] = None,
    mute: bool = False,
    controls: bool = False,
    ipc_socket: Optional[str] = None,
    lua_script: Optional[str] = None,
    trigger_file: Optional[str] = None,
) -> subprocess.Popen:
    """Launches mpv with clean terminal output pointing to the relay URL."""
    mpv_bin = check_mpv_installed()
    chrome_ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    cmd = [
        mpv_bin,
        f"--title=⚽ {title}",
        "--really-quiet",
        f"--user-agent={chrome_ua}",
        # Force mpv to select the top bitrate and avoid throttled low-quality renditions
        "--hls-bitrate=max",
        # Software decode prevents macOS VideoToolbox macroblock smearing on stream packet loss
        "--hwdec=no",
        # Buffering settings to enable deep rewind cache for instant goal/replay clipping
        "--demuxer-max-bytes=500M",
        "--demuxer-max-back-bytes=400M",
        "--demuxer-seekable-cache=yes",
    ]
    if referer:
        cmd.append(f"--referrer={referer}")
    if mute:
        cmd.append("--mute=yes")
    if controls:
        cmd.append("--script-opts-add=osc-visibility=always")
    if ipc_socket:
        cmd.append(f"--input-ipc-server={ipc_socket}")
    if lua_script:
        cmd.append(f"--script={lua_script}")
    if trigger_file:
        cmd.append(f"--script-opts-add=trigger_file={trigger_file}")
    cmd.append(stream_url)
    log_path = PROJECT_ROOT / "output" / "mpv.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8")
    return subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )


async def switch_stream(
    context,
    relay: HlsRelayServer,
    stream: Dict[str, str],
    match_url: str,
    match_title: str,
    current_page: Optional[Any],
    current_proc: Optional[subprocess.Popen],
    timeout_sec: float = 12.0,
    mute: bool = False,
    controls: bool = False,
    ipc_socket: Optional[str] = None,
    lua_script: Optional[str] = None,
    trigger_file: Optional[str] = None,
) -> Tuple[Optional[Any], Optional[subprocess.Popen], bool]:
    """Closes current playback and loads the new stream into MPV via HLS relay."""
    from src.play_stream import auto_play_jwplayer

    # 1. Terminate current MPV process
    if current_proc and current_proc.poll() is None:
        try:
            current_proc.terminate()
            current_proc.wait(timeout=1.0)
        except Exception:
            try:
                current_proc.kill()
            except Exception:
                pass
        current_proc = None

    # 2. Close current browser page
    if current_page:
        try:
            await current_page.close()
        except Exception:
            pass
        current_page = None

    # 3. Reset relay playlist buffer
    relay.latest_playlist = ""
    relay._playlist_ready_event.clear()

    # 4. Open fresh page in existing context
    try:
        page = await context.new_page()

        async def handle_response(resp):
            if ".m3u8" in resp.url and "jwpltx" not in resp.url and resp.status == 200:
                try:
                    text = await resp.text()
                    relay.update_playlist(text)
                except Exception:
                    pass

        page.on("response", handle_response)

        # 5. Navigate to stream URL with Referer
        await page.goto(stream["url"], referer=match_url, wait_until="domcontentloaded")

        # 6. Auto-click JW Player
        await auto_play_jwplayer(page, timeout_ms=10000)

        # 7. Wait for initial valid playlist manifest
        got_playlist = await asyncio.to_thread(relay.wait_for_playlist, timeout_sec)
        if not got_playlist:
            return page, None, False

        # 8. Launch MPV
        stream_title = f"{match_title} - {stream.get('name', 'Stream')}"
        proc = launch_mpv_process(
            relay.url,
            title=stream_title,
            referer=stream.get("url") or match_url,
            mute=mute,
            controls=controls,
            ipc_socket=ipc_socket,
            lua_script=lua_script,
            trigger_file=trigger_file,
        )

        # 9. Verify MPV stays alive for at least 1 second
        await asyncio.sleep(1.0)
        if proc.poll() is not None:
            logger.warning(f"MPV process exited immediately with code {proc.poll()}")
            return page, None, False

        return page, proc, True

    except Exception as e:
        logger.warning(f"Error switching to stream {stream.get('name')}: {e}")
        return None, None, False


async def play_interactive_stream_session(
    streams: List[Dict[str, str]],
    match_url: str,
    match_title: str = "SoccerStreams Live",
    initial_index: int = 0,
    use_adblock: bool = True,
    headless: bool = True,
    mute: bool = False,
    controls: bool = False,
):
    """Maintains an interactive terminal stream switcher while playing in MPV."""
    check_mpv_installed()
    import time
    from playwright.async_api import async_playwright
    from src.playground import launch_browser_context
    from src.stream_recorder import StreamRecorder
    from src.terminal_menu import TerminalRawMode, get_key_nonblocking, render_stream_menu

    if not streams:
        print("❌ No streams available to play.")
        return

    cursor_idx = max(0, min(initial_index, len(streams) - 1))
    active_idx: Optional[int] = None
    player_proc: Optional[subprocess.Popen] = None
    page: Optional[Any] = None

    # MPV IPC & recording trigger configuration
    session_id = os.getpid()
    ipc_socket = f"/tmp/mpv_soccer_{session_id}.sock"
    trigger_file = Path(f"/tmp/mpv_trigger_{session_id}.txt")
    lua_script = str(SRC_DIR / "mpv_recorder.lua")
    recorder = StreamRecorder(socket_path=ipc_socket, trigger_file=trigger_file)

    relay = HlsRelayServer()
    relay.start()

    try:
        async with async_playwright() as p:
            context, initial_page = await launch_browser_context(
                p=p,
                use_adblock=use_adblock,
                headless=headless,
            )
            try:
                await initial_page.close()
            except Exception:
                pass

            with TerminalRawMode():
                status_msg = f"⏳ Connecting to '{streams[cursor_idx]['name']}'..."
                render_stream_menu(match_title, streams, cursor_idx, active_idx, status_msg, is_playing=False)

                page, player_proc, ok = await switch_stream(
                    context=context,
                    relay=relay,
                    stream=streams[cursor_idx],
                    match_url=match_url,
                    match_title=match_title,
                    current_page=page,
                    current_proc=player_proc,
                    mute=mute,
                    controls=controls,
                    ipc_socket=ipc_socket,
                    lua_script=lua_script,
                    trigger_file=str(trigger_file),
                )

                if ok:
                    active_idx = cursor_idx
                    status_msg = f"🟢 Playing '{streams[active_idx]['name']}' in MPV"
                    render_stream_menu(match_title, streams, cursor_idx, active_idx, status_msg, is_playing=True)
                else:
                    status_msg = f"⚠️ Could not load '{streams[cursor_idx]['name']}'. Select another stream."
                    render_stream_menu(match_title, streams, cursor_idx, active_idx, status_msg, is_playing=False)

                last_rec_render_time = time.time()

                while True:
                    key = get_key_nonblocking(timeout=0.1)
                    trigger_action = recorder.check_trigger()
                    action = trigger_action or key

                    is_currently_playing = (player_proc is not None and player_proc.poll() is None)

                    # Detect if MPV was closed outside terminal
                    if player_proc and player_proc.poll() is not None:
                        if recorder.is_recording:
                            await asyncio.to_thread(recorder.stop_recording)
                        player_proc = None
                        status_msg = (
                            f"⏸️ MPV closed. Press ENTER to watch '{streams[cursor_idx]['name']}', or 'q' to exit."
                        )
                        render_stream_menu(
                            match_title,
                            streams,
                            cursor_idx,
                            active_idx,
                            status_msg,
                            is_playing=False,
                            is_recording=False,
                        )

                    if not action:
                        # Update recording elapsed timer every second
                        if recorder.is_recording and time.time() - last_rec_render_time >= 1.0:
                            last_rec_render_time = time.time()
                            render_stream_menu(
                                match_title,
                                streams,
                                cursor_idx,
                                active_idx,
                                status_msg,
                                is_playing=is_currently_playing,
                                is_recording=True,
                                elapsed_rec=recorder.get_elapsed_str(),
                            )
                        await asyncio.sleep(0.02)
                        continue

                    if action == "UP":
                        if cursor_idx > 0:
                            cursor_idx -= 1
                            render_stream_menu(
                                match_title,
                                streams,
                                cursor_idx,
                                active_idx,
                                status_msg,
                                is_playing=is_currently_playing,
                                is_recording=recorder.is_recording,
                                elapsed_rec=recorder.get_elapsed_str(),
                            )
                    elif action == "DOWN":
                        if cursor_idx < len(streams) - 1:
                            cursor_idx += 1
                            render_stream_menu(
                                match_title,
                                streams,
                                cursor_idx,
                                active_idx,
                                status_msg,
                                is_playing=is_currently_playing,
                                is_recording=recorder.is_recording,
                                elapsed_rec=recorder.get_elapsed_str(),
                            )
                    elif action == "ENTER":
                        if cursor_idx == active_idx and player_proc and player_proc.poll() is None:
                            status_msg = f"ℹ️ '{streams[cursor_idx]['name']}' is already playing in MPV"
                            render_stream_menu(
                                match_title,
                                streams,
                                cursor_idx,
                                active_idx,
                                status_msg,
                                is_playing=True,
                                is_recording=recorder.is_recording,
                                elapsed_rec=recorder.get_elapsed_str(),
                            )
                        else:
                            if recorder.is_recording:
                                status_msg = "💾 Finalizing active recording before stream switch..."
                                render_stream_menu(
                                    match_title,
                                    streams,
                                    cursor_idx,
                                    active_idx,
                                    status_msg,
                                    is_playing=is_currently_playing,
                                    is_recording=True,
                                    elapsed_rec=recorder.get_elapsed_str(),
                                )
                                await asyncio.to_thread(recorder.stop_recording)

                            status_msg = f"⏳ Switching to '{streams[cursor_idx]['name']}'..."
                            render_stream_menu(
                                match_title,
                                streams,
                                cursor_idx,
                                active_idx,
                                status_msg,
                                is_playing=False,
                                is_recording=False,
                            )

                            page, player_proc, ok = await switch_stream(
                                context=context,
                                relay=relay,
                                stream=streams[cursor_idx],
                                match_url=match_url,
                                match_title=match_title,
                                current_page=page,
                                current_proc=player_proc,
                                mute=mute,
                                controls=controls,
                                ipc_socket=ipc_socket,
                                lua_script=lua_script,
                                trigger_file=str(trigger_file),
                            )

                            if ok:
                                active_idx = cursor_idx
                                status_msg = f"🟢 Playing '{streams[active_idx]['name']}' in MPV"
                                render_stream_menu(
                                    match_title,
                                    streams,
                                    cursor_idx,
                                    active_idx,
                                    status_msg,
                                    is_playing=True,
                                    is_recording=False,
                                )
                            else:
                                status_msg = f"❌ Failed to load '{streams[cursor_idx]['name']}'. Select another stream."
                                render_stream_menu(
                                    match_title,
                                    streams,
                                    cursor_idx,
                                    active_idx,
                                    status_msg,
                                    is_playing=False,
                                    is_recording=False,
                                )

                    elif action in ("START_RECORD", "a"):
                        if not is_currently_playing:
                            status_msg = "⚠️ Playback is not active. Select a stream and press ENTER."
                        else:
                            stream_name = streams[active_idx].get("name", "Stream") if active_idx is not None else "Stream"
                            full_title = f"{match_title} - {stream_name}"
                            ok, status_msg = recorder.start_recording(full_title)
                        render_stream_menu(
                            match_title,
                            streams,
                            cursor_idx,
                            active_idx,
                            status_msg,
                            is_playing=is_currently_playing,
                            is_recording=recorder.is_recording,
                            elapsed_rec=recorder.get_elapsed_str(),
                        )

                    elif action in ("STOP_RECORD", "c"):
                        if not recorder.is_recording:
                            status_msg = "⚠️ No active recording. Press 'a' to start recording."
                        else:
                            status_msg = "💾 Finalizing and saving clip..."
                            render_stream_menu(
                                match_title,
                                streams,
                                cursor_idx,
                                active_idx,
                                status_msg,
                                is_playing=is_currently_playing,
                                is_recording=False,
                            )
                            dest_path, status_msg = await asyncio.to_thread(recorder.stop_recording)
                        render_stream_menu(
                            match_title,
                            streams,
                            cursor_idx,
                            active_idx,
                            status_msg,
                            is_playing=is_currently_playing,
                            is_recording=recorder.is_recording,
                            elapsed_rec=recorder.get_elapsed_str(),
                        )

                    elif action in ("CLIP_LAST_MINUTE", "g"):
                        if not is_currently_playing:
                            status_msg = "⚠️ Playback is not active. Select a stream and press ENTER."
                        else:
                            status_msg = "⚡ Downloading all available replay buffer..."
                            render_stream_menu(
                                match_title,
                                streams,
                                cursor_idx,
                                active_idx,
                                status_msg,
                                is_playing=is_currently_playing,
                                is_recording=recorder.is_recording,
                                elapsed_rec=recorder.get_elapsed_str(),
                            )
                            stream_name = streams[active_idx].get("name", "Stream") if active_idx is not None else "Stream"
                            full_title = f"{match_title} - {stream_name}"
                            dest_path, status_msg = await asyncio.to_thread(recorder.clip_replay_buffer, full_title)
                        render_stream_menu(
                            match_title,
                            streams,
                            cursor_idx,
                            active_idx,
                            status_msg,
                            is_playing=is_currently_playing,
                            is_recording=recorder.is_recording,
                            elapsed_rec=recorder.get_elapsed_str(),
                        )

                    elif action == "QUIT":
                        break
                    elif action == "ESC":
                        status_msg = "ℹ️ Press 'q' or Ctrl+C to exit the stream session."
                        render_stream_menu(
                            match_title,
                            streams,
                            cursor_idx,
                            active_idx,
                            status_msg,
                            is_playing=is_currently_playing,
                            is_recording=recorder.is_recording,
                            elapsed_rec=recorder.get_elapsed_str(),
                        )

            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            await context.close()

    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        recorder.cleanup()
        if player_proc and player_proc.poll() is None:
            player_proc.terminate()
            try:
                player_proc.wait(timeout=1.0)
            except Exception:
                player_proc.kill()
        relay.stop()
        sys.stdout.write("\033[2J\033[H\033[?25h")
        sys.stdout.write("📺 Stream session ended. Goodbye!\n")
        sys.stdout.flush()


async def play_stream_with_mpv(
    stream_url: str,
    match_url: str,
    use_adblock: bool = True,
    headless: bool = True,
    match_title: str = "SoccerStreams Live",
    mute: bool = False,
    controls: bool = False,
    **kwargs,
):
    """Maintains the authenticated DDoS-Guard session via Chromium (default headless),

    relays the live M3U8 manifest via local HTTP server, and plays in mpv.
    """
    streams = [{"name": "Stream 1", "url": stream_url}]
    await play_interactive_stream_session(
        streams=streams,
        match_url=match_url,
        match_title=match_title,
        initial_index=0,
        use_adblock=use_adblock,
        headless=headless,
        mute=mute,
        controls=controls,
    )


# Backward compatibility alias
play_stream_with_relay = play_stream_with_mpv
