import argparse
import asyncio
from pathlib import Path
import sys
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_FILE = ROOT_DIR / "output" / "football_games.json"

from src.playground import launch_browser_context
from src.scraper import FootballScraper
from src.select_game import prompt_game_selection
from src.select_stream import resolve_stream_headless
from src.player_launcher import play_stream_with_mpv


async def run_unified_flow(
    use_adblock: bool = True,
    output_file: Path = DEFAULT_OUTPUT_FILE,
    post_click_wait_ms: int = 2500,
    visible: bool = False,
    team: Optional[str] = None,
):
    is_headless = not visible

    print("\n" + "=" * 65)
    print(" ⚽ Football Stream Pipeline")
    print(f" Browser:  Chromium (headless={is_headless})")
    print(f" Player:   mpv")
    print(f" Adblock:  {'uBlock Origin Lite' if use_adblock else 'Disabled'}")
    if team:
        print(f" Team:     {team} (Auto-select)")
    print("=" * 65 + "\n")

    # ---------------------------------------------------------
    # STEP 1: SCRAPE MATCHES
    # ---------------------------------------------------------
    print(f"🔍 Step 1: Scraping football matches (headless={is_headless})...")
    scraper = FootballScraper(headless=is_headless)
    data = await scraper.run(output_file=output_file)
    matches = data.get("matches_flat", [])

    if not matches:
        print("❌ No matches found on the page.")
        return

    # ---------------------------------------------------------
    # STEP 2: SELECT GAME (TERMINAL ARROW-KEY MENU OR AUTO-SELECT)
    # ---------------------------------------------------------
    if team:
        print(f"\n📋 Step 2: Auto-selecting match for team '{team}'...")
    else:
        print("\n📋 Step 2: Presenting game selection menu...")
    selected_match = prompt_game_selection(matches, team=team)

    if not selected_match:
        print("\nExited without selecting a match.")
        return

    home = selected_match["home_team"]["name"]
    away = selected_match["away_team"]["name"]
    match_url = selected_match["full_url"]
    print(f"\n🚀 Selected Match: {home} vs {away}")
    print(f"🔗 URL: {match_url}")

    # ---------------------------------------------------------
    # STEP 3: RESOLVE STREAM
    # ---------------------------------------------------------
    print(f"\n📡 Step 3: Resolving broadcast stream (headless={is_headless})...")
    selected_stream = await resolve_stream_headless(
        match_url=match_url,
        post_click_wait_ms=post_click_wait_ms,
        headless=is_headless,
    )

    if not selected_stream:
        print("❌ Could not resolve stream.")
        return

    # ---------------------------------------------------------
    # STEP 4: PLAY STREAM IN MPV (VIA HEADLESS HLS RELAY)
    # ---------------------------------------------------------
    match_title = f"{home} vs {away}"
    print(f"\n🎬 Step 4: Launching MPV via HLS relay (headless={is_headless})...")
    await play_stream_with_mpv(
        stream_url=selected_stream["url"],
        match_url=match_url,
        use_adblock=use_adblock,
        headless=is_headless,
        match_title=match_title,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Run all steps headlessly in Chromium (scrape, game select, stream resolve) and stream in mpv."
    )
    parser.add_argument(
        "--visible",
        "--headed",
        action="store_true",
        help="Keep browser window visible across all steps (default: headless=True).",
    )
    parser.add_argument(
        "--no-adblock",
        action="store_true",
        help="Disable uBlock Origin Lite in Chromium.",
    )
    parser.add_argument(
        "--wait-ms",
        type=int,
        default=2500,
        help="Milliseconds to wait after clicking Broadcast tab (default: 2500).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(DEFAULT_OUTPUT_FILE),
        help="Path to save scraped games (default: output/football_games.json).",
    )
    parser.add_argument(
        "-t",
        "--team",
        type=str,
        default=None,
        help="Auto-select match containing this team name (fuzzy match).",
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            run_unified_flow(
                use_adblock=not args.no_adblock,
                output_file=Path(args.output),
                post_click_wait_ms=args.wait_ms,
                visible=args.visible,
                team=args.team,
            )
        )
    except KeyboardInterrupt:
        print("\nPipeline interrupted by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
