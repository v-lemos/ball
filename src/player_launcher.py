import logging
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Optional

logger = logging.getLogger("player_launcher")


def play_in_external_player(
    m3u8_url: str,
    player: str = "iina",
    referrer: str = "https://hls.st/",
) -> bool:
    """Launches an external desktop media player with the captured M3U8 stream and proper Referer."""
    player = player.lower().strip()

    if player == "iina":
        # Check CLI binary or macOS App bundle
        iina_bin = shutil.which("iina")
        if iina_bin:
            cmd = [iina_bin, f"--mpv-referrer={referrer}", m3u8_url]
        elif Path("/Applications/IINA.app").exists():
            cmd = ["open", "-a", "IINA", "--args", f"--mpv-referrer={referrer}", m3u8_url]
        else:
            logger.warning("IINA not found on system. Trying fallback player...")
            return False

        print(f"\n🚀 Launching IINA: {' '.join(cmd)}")
        subprocess.Popen(cmd)
        return True

    elif player == "vlc":
        vlc_bin = shutil.which("vlc")
        if vlc_bin:
            cmd = [vlc_bin, f"--http-referrer={referrer}", m3u8_url]
        elif Path("/Applications/VLC.app").exists():
            cmd = ["open", "-a", "VLC", "--args", f"--http-referrer={referrer}", m3u8_url]
        else:
            logger.warning("VLC not found on system.")
            return False

        print(f"\n🚀 Launching VLC: {' '.join(cmd)}")
        subprocess.Popen(cmd)
        return True

    elif player == "mpv":
        mpv_bin = shutil.which("mpv")
        if not mpv_bin:
            logger.warning("mpv binary not found in PATH.")
            return False

        cmd = [mpv_bin, f"--referrer={referrer}", m3u8_url]
        print(f"\n🚀 Launching mpv: {' '.join(cmd)}")
        subprocess.Popen(cmd)
        return True

    return False
