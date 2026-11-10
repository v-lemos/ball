import sys
import termios
import tty
from typing import Any, Callable, Dict, List, Optional


def get_key() -> str:
    """Reads a single keypress or arrow key from standard input."""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                if ch3 == "A":
                    return "UP"
                elif ch3 == "B":
                    return "DOWN"
            return "ESC"
        elif ch in ("\r", "\n"):
            return "ENTER"
        elif ch in ("\x03", "\x04"):  # Ctrl+C, Ctrl+D
            return "QUIT"
        elif ch.lower() == "q":
            return "QUIT"
        return ch
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
