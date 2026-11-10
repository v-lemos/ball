import os
import select
import sys
import termios
import tty
from typing import Any, Callable, Dict, List, Optional


class TerminalRawMode:
    """Context manager to enter raw terminal mode and hide cursor, restoring on exit."""

    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.old_settings = None

    def __enter__(self):
        self.old_settings = termios.tcgetattr(self.fd)
        tty.setraw(self.fd)
        sys.stdout.write("\033[?25l")  # Hide cursor
        sys.stdout.flush()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout.write("\033[?25h")  # Show cursor
        sys.stdout.flush()
        if self.old_settings:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_settings)


def parse_key_bytes(data: bytes, fd: int) -> Optional[str]:
    """Parses raw bytes from terminal into a recognized key name."""
    if not data:
        return None

    # If starts with ESC, check if more bytes are available in the escape sequence
    if data == b"\x1b":
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                data += os.read(fd, 31)
            except Exception:
                pass

    # Arrow keys in standard ANSI (\x1b[) or application mode (\x1bO)
    if data in (b"\x1b[A", b"\x1bOA"):
        return "UP"
    if data in (b"\x1b[B", b"\x1bOB"):
        return "DOWN"
    if data in (b"\x1b[C", b"\x1bOC"):
        return "RIGHT"
    if data in (b"\x1b[D", b"\x1bOD"):
        return "LEFT"

    if data in (b"\r", b"\n"):
        return "ENTER"
    if data in (b"\x03", b"\x04") or data.lower() == b"q":
        return "QUIT"
    if data == b"\x1b":
        return "ESC"

    try:
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return None


def get_key_nonblocking(timeout: float = 0.1) -> Optional[str]:
    """Reads a single key if available within timeout seconds, or returns None."""
    fd = sys.stdin.fileno()
    r, _, _ = select.select([fd], [], [], timeout)
    if not r:
        return None
    try:
        data = os.read(fd, 32)
    except Exception:
        return None
    return parse_key_bytes(data, fd)


def render_stream_menu(
    match_title: str,
    streams: List[Dict[str, str]],
    cursor_idx: int,
    active_idx: Optional[int],
    status_msg: str = "",
    is_playing: bool = True,
) -> None:
    """Renders the interactive stream switcher menu in the terminal using raw-mode compatible newlines."""
    lines = ["\033[2J\033[H"]  # Clear screen and return cursor to top-left
    lines.append("=" * 65)
    lines.append(f" ⚽ {match_title}")
    lines.append(f" 📺 Streams: {len(streams)} available (Site Order)")
    lines.append("=" * 65)
    lines.append("")
    lines.append(" Available Streams:")
    lines.append("   (Use ↑/↓ to navigate, ENTER to switch, 'q' to quit)")
    lines.append("")

    for i, s in enumerate(streams):
        name = s.get("name", "Unknown Stream")
        is_cursor = (i == cursor_idx)
        is_active = (i == active_idx)

        if is_active:
            if is_playing:
                active_badge = " \033[1;32m[▶ NOW PLAYING]\033[0m"
            else:
                active_badge = " \033[1;33m[PAUSED / CLOSED]\033[0m"
        else:
            active_badge = ""

        if is_cursor:
            prefix = " \033[1;36m❯\033[0m"
            stream_display = f"\033[1;37m{i + 1:2d}. {name}\033[0m{active_badge}"
        else:
            prefix = "  "
            stream_display = f"\033[0;37m{i + 1:2d}. {name}\033[0m{active_badge}"

        lines.append(f"{prefix} {stream_display}")

    lines.append("")
    lines.append("-" * 65)
    if status_msg:
        lines.append(f" {status_msg}")
    else:
        lines.append(" Ready")
    lines.append("-" * 65)

    sys.stdout.write("\r\n".join(lines) + "\r\n")
    sys.stdout.flush()


def get_key() -> str:
    """Reads a single keypress or arrow key from standard input."""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        data = os.read(fd, 32)
        key = parse_key_bytes(data, fd)
        return key or ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def interactive_select(
    options: List[str],
    title: str = "Select an option",
    header_info: Optional[str] = None,
    footer_info: Optional[str] = None,
) -> Optional[int]:
    """Displays a simple terminal menu navigated via Up/Down arrow keys and Enter.

    Returns the selected index (0-based) or None if cancelled.
    """
    if not options:
        return None

    selected_idx = 0
    max_idx = len(options) - 1

    # Hide terminal cursor
    print("\033[?25l", end="", flush=True)

    try:
        while True:
            lines = ["\033[2J\033[H"]  # Clear screen and return cursor to top-left
            lines.append(f"📺 {title}  (Use ↑/↓ to move, ENTER to select, q to quit)\n")

            if header_info:
                lines.append(f"{header_info}\n")

            for i, opt in enumerate(options):
                if i == selected_idx:
                    lines.append(f" \033[1;32m❯ {opt}\033[0m")
                else:
                    lines.append(f"   {opt}")

            if footer_info:
                lines.append(f"\n{footer_info}")

            print("\n".join(lines), flush=True)

            key = get_key()
            if key == "UP":
                if selected_idx > 0:
                    selected_idx -= 1
            elif key == "DOWN":
                if selected_idx < max_idx:
                    selected_idx += 1
            elif key == "ENTER":
                return selected_idx
            elif key in ("QUIT", "ESC"):
                return None
    finally:
        # Restore cursor
        print("\033[?25h", end="", flush=True)


def interactive_game_menu(
    selectable_items: List[str],
    upcoming_items: List[str],
    finished_items: List[str],
) -> Optional[int]:
    """Displays games with arrow-key navigation strictly locked to selectable items."""
    if not selectable_items:
        print("\033[2J\033[H", end="")
        print("⚽ Football Matches\n")
        print("🔴 Live & Starting Soon (≤ 10 mins):")
        print("   (No live games or games starting in ≤ 10 mins right now)\n")

        if upcoming_items:
            print("⏳ Upcoming:")
            for item in upcoming_items:
                print(f"   {item}")
            print()

        if finished_items:
            print("🏁 Finished:")
            for item in finished_items:
                print(f"   {item}")
            print()

        input("Press Enter or 'q' to exit... ")
        return None

    selected_idx = 0
    max_idx = len(selectable_items) - 1

    print("\033[?25l", end="", flush=True)
    try:
        while True:
            lines = ["\033[2J\033[H"]
            lines.append("⚽ Football Matches  (Use ↑/↓ to move, ENTER to select, q to quit)\n")
            lines.append("🔴 Live & Starting Soon:")

            for i, label in enumerate(selectable_items):
                if i == selected_idx:
                    lines.append(f" \033[1;32m❯ {label}\033[0m")
                else:
                    lines.append(f"   {label}")

            if upcoming_items:
                lines.append("\n⏳ Upcoming:")
                for item in upcoming_items:
                    lines.append(f"   {item}")

            if finished_items:
                lines.append("\n🏁 Finished:")
                for item in finished_items:
                    lines.append(f"   {item}")

            print("\n".join(lines), flush=True)

            key = get_key()
            if key == "UP":
                if selected_idx > 0:
                    selected_idx -= 1
            elif key == "DOWN":
                if selected_idx < max_idx:
                    selected_idx += 1
            elif key == "ENTER":
                return selected_idx
            elif key in ("QUIT", "ESC"):
                return None
    finally:
        print("\033[?25h", end="", flush=True)
