import asyncio
import http.server
import logging
from pathlib import Path
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
from typing import Optional

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


class HlsRelayHandler(http.server.BaseHTTPRequestHandler):
    """Serves the latest intercepted M3U8 playlist to local media players."""

    server_instance: "HlsRelayServer"

    def log_message(self, format, *args):
        # Suppress noisy HTTP request logging
        return

    def do_GET(self):
        if self.path in ("/live.m3u8", "/"):
            content = self.server_instance.latest_playlist
            if not content:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"Playlist not ready yet.")
                return

            data = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
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
        if "#EXTM3U" in content:
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


def launch_mpv_process(stream_url: str, title: str = "Soccer Streams - Live") -> subprocess.Popen:
    """Launches mpv with clean terminal output pointing to the relay URL."""
    mpv_bin = check_mpv_installed()
    cmd = [
        mpv_bin,
        f"--title=⚽ {title}",
        "--really-quiet",
        stream_url,
    ]
    print(f"\n🚀 Launching MPV: {' '.join(cmd)}")
    return subprocess.Popen(cmd)


async def play_stream_with_mpv(
    stream_url: str,
    match_url: str,
    use_adblock: bool = True,
    headless: bool = True,
    match_title: str = "SoccerStreams Live",
    **kwargs,
):
    """Maintains the authenticated DDoS-Guard session via Chromium (default headless),

    relays the live M3U8 manifest via local HTTP server, and plays in mpv.
    """
    check_mpv_installed()

    from playwright.async_api import async_playwright
    from src.playground import launch_browser_context
    from src.play_stream import auto_play_jwplayer

    relay = HlsRelayServer()
    relay.start()

    print("\n" + "=" * 65)
    print(" 🎬 Live Stream Player (MPV)")
    print(f" Relay URL:  {relay.url}")
    print(f" Stream:     {stream_url}")
    print(f" Headless:   {headless}")
    print("=" * 65)

    player_proc: Optional[subprocess.Popen] = None

    try:
        async with async_playwright() as p:
            print(f"\n⏳ Initializing stream session (headless={headless})...")
            context, page = await launch_browser_context(
                p=p,
                use_adblock=use_adblock,
                headless=headless,
            )

            async def handle_response(resp):
                if ".m3u8" in resp.url and "jwpltx" not in resp.url and resp.status == 200:
                    try:
                        text = await resp.text()
                        if "#EXTM3U" in text:
                            relay.update_playlist(text)
                    except Exception:
                        pass

            page.on("response", handle_response)

            logger.info(f"Navigating to stream page: {stream_url}...")
            await page.goto(stream_url, referer=match_url, wait_until="domcontentloaded")

            # Activate JW Player
            logger.info("Triggering video player initialization...")
            await auto_play_jwplayer(page, timeout_ms=10000)

            # Wait for initial playlist
            print("⏳ Waiting for initial HLS stream manifest...")
            got_playlist = await asyncio.to_thread(relay.wait_for_playlist, 12.0)

            if not got_playlist:
                print("❌ Failed to capture HLS stream manifest within timeout.")
                await context.close()
                return

            print(f"✅ Stream manifest captured! Starting MPV...")

            # Launch mpv
            player_proc = launch_mpv_process(relay.url, title=match_title)

            print("-" * 65)
            print(" 🟢 Now playing in MPV!")
            print(f"    Relay: {relay.url}")
            print("    (Close MPV or press Ctrl+C in this terminal to exit)")
            print("-" * 65 + "\n")

            # Keep event loop running while mpv is alive
            while player_proc.poll() is None:
                await asyncio.sleep(0.5)

            print("\nMPV closed.")
            await context.close()

    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nStopping MPV stream session...")
    finally:
        if player_proc and player_proc.poll() is None:
            player_proc.terminate()
            try:
                player_proc.wait(timeout=2)
            except Exception:
                player_proc.kill()
        relay.stop()
        print("Relay server stopped. Done.")


# Backward compatibility alias
play_stream_with_relay = play_stream_with_mpv
