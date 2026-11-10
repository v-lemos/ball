import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

DEFAULT_DATA_FILE = PROJECT_ROOT / "output" / "football_games.json"

from scraper import FootballScraper
from select_stream import resolve_stream_headless, play_stream_in_browser
from terminal_menu import interactive_game_menu


def parse_match_time(time_str: str, now: datetime) -> Optional[Tuple[datetime, float]]:
    """Parses a 'HH:MM' string and returns (match_datetime, minutes_until_start)."""
    if not time_str or ":" not in time_str:
        return None
    try:
        parts = time_str.strip().split(":")
        hours = int(parts[0])
        minutes = int(parts[1])
        match_time = now.replace(
            hour=hours, minute=minutes, second=0, microsecond=0
        )
        diff_minutes = (match_time - now).total_seconds() / 60
        return match_time, diff_minutes
    except Exception:
        return None


def categorize_matches(matches: List[Dict[str, Any]]):
    """Categorizes matches into:

    1. Selectable: LIVE or starting in <= 10 minutes (sorted first)
    2. Upcoming: Starting in > 10 minutes
    3. Finished: Completed matches
    """
    now = datetime.now()
    selectable = []
    upcoming = []
    finished = []

    for m in matches:
        status = (m.get("status") or "").lower()
        is_live = m.get("is_live", False) or status == "inprogress"
        time_or_score = m.get("time_or_score", "")

        if status == "finished":
            finished.append(m)
            continue

        if is_live:
            selectable.append((m, 0.0, "LIVE"))
            continue

        time_data = parse_match_time(time_or_score, now)
        if time_data:
            _, diff_minutes = time_data
            if diff_minutes <= 10:
                selectable.append((m, diff_minutes, time_or_score))
            else:
                upcoming.append((m, diff_minutes))
        else:
            upcoming.append((m, 9999.0))

    selectable.sort(
        key=lambda item: (0 if item[2] == "LIVE" else 1, item[1])
    )
    upcoming.sort(key=lambda item: item[1])

    return selectable, upcoming, finished


def prompt_game_selection(matches: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Takes a list of matches, displays the terminal menu, and returns the chosen match dict."""
    if not matches:
        print("No football matches available for selection.")
        return None

    selectable, upcoming, finished = categorize_matches(matches)

    selectable_items = []
    for m, _, time_tag in selectable:
        home = m["home_team"]["name"] or "TBD"
        away = m["away_team"]["name"] or "TBD"
        tag = "LIVE" if time_tag == "LIVE" else time_tag
        selectable_items.append(f"{home} vs {away} ({tag})")

    upcoming_items = []
    for m, _ in upcoming:
        home = m["home_team"]["name"] or "TBD"
        away = m["away_team"]["name"] or "TBD"
        time_str = m.get("time_or_score", "")
        upcoming_items.append(f"{home} vs {away} ({time_str})")

    finished_items = []
    for m in finished:
        home = m["home_team"]["name"] or "TBD"
        away = m["away_team"]["name"] or "TBD"
        score = m.get("time_or_score", "FT")
        finished_items.append(f"{home} vs {away} ({score})")

    selected_idx = interactive_game_menu(
        selectable_items=selectable_items,
        upcoming_items=upcoming_items,
        finished_items=finished_items,
    )

    if selected_idx is None:
        return None

    return selectable[selected_idx][0]


async def run_game_selection(
    browser_type: str = "chromium",
    use_adblock: bool = True,
    data_file: Path = DEFAULT_DATA_FILE,
    force_fetch: bool = False,
):
    if not force_fetch and data_file.exists():
        with open(data_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        scraper = FootballScraper(browser_type=browser_type, headless=True)
        data = await scraper.run(output_file=data_file)

    matches = data.get("matches_flat", [])
    if not matches:
        print("No football matches found.")
        return

    selected_match = prompt_game_selection(matches)
    if not selected_match:
        print("\nExited.")
        return

    home = selected_match["home_team"]["name"]
    away = selected_match["away_team"]["name"]
    target_url = selected_match["full_url"]

    print(f"\n🚀 Selected Match: {home} vs {away}")
    print(f"🔗 URL: {target_url}\n")

    selected_stream = await resolve_stream_headless(
        match_url=target_url,
        browser_type=browser_type,
    )

    if selected_stream:
        await play_stream_in_browser(
            stream=selected_stream,
            match_url=target_url,
            browser_type=browser_type,
            use_adblock=use_adblock,
        )


def main():
    parser = argparse.ArgumentParser(
        description="Select and watch live or upcoming football games from rdaily.live."
    )
    parser.add_argument(
        "--file",
        type=str,
        default=str(DEFAULT_DATA_FILE),
        help="Path to JSON file with scraped games (default: output/football_games.json).",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Force a fresh scrape from rdaily.live before showing selection.",
    )
    parser.add_argument(
        "--browser",
        "-b",
        choices=["chromium", "firefox"],
        default="chromium",
        help="Browser to open the match stream with (default: chromium).",
    )
    parser.add_argument(
        "--no-adblock",
        action="store_true",
        help="Disable uBlock Origin when opening the stream.",
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            run_game_selection(
                browser_type=args.browser,
                use_adblock=not args.no_adblock,
                data_file=Path(args.file),
                force_fetch=args.fetch,
            )
        )
    except KeyboardInterrupt:
        print("\nSession ended.")
        sys.exit(0)


if __name__ == "__main__":
    main()
