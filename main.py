import argparse
import asyncio
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_FILE = ROOT_DIR / "output" / "football_games.json"

from src.playground import launch_browser_context
from src.scraper import FootballScraper
from src.select_game import prompt_game_selection
from src.select_stream import resolve_stream_headless, play_stream_in_browser


async def run_unified_flow(
    browser_type: str = "chromium",
    use_adblock: bool = True,
    output_file: Path = DEFAULT_OUTPUT_FILE,
    post_click_wait_ms: int = 2500,
    visible: bool = False,
):
    is_headless = not visible

    print("\n" + "=" * 65)
    print(" ⚽ Football Stream Pipeline")
    print(f" Browser:  {browser_type.upper()}")
    print(f" Mode:     {'Always Visible (Headed)' if visible else 'Headless until Play (Default)'}")
    print(f" Adblock:  {'uBlock Origin (Enabled 🛡️)' if use_adblock else 'Disabled'}")
    print("=" * 65 + "\n")

    # ---------------------------------------------------------
    # STEP 1: SCRAPE MATCHES
    # ---------------------------------------------------------
    print(f"🔍 Step 1: Scraping football matches (headless={is_headless})...")
    scraper = FootballScraper(browser_type=browser_type, headless=is_headless)
    data = await scraper.run(output_file=output_file)
    matches = data.get("matches_flat", [])

    if not matches:
        print("❌ No matches found on the page.")
        return

    # ---------------------------------------------------------
    # STEP 2: SELECT GAME (TERMINAL ARROW-KEY MENU)
    # ---------------------------------------------------------
    print("\n📋 Step 2: Presenting game selection menu...")
    selected_match = prompt_game_selection(matches)

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
        browser_type=browser_type,
        post_click_wait_ms=post_click_wait_ms,
        headless=is_headless,
    )

    if not selected_stream:
        print("❌ Could not resolve stream.")
        return

    # ---------------------------------------------------------
    # STEP 4: OPEN BROWSER DIRECTLY TO PLAY STREAM
    # ---------------------------------------------------------
    print("\n🎬 Step 4: Launching stream player in browser...")
    await play_stream_in_browser(
        stream=selected_stream,
        match_url=match_url,
        browser_type=browser_type,
        use_adblock=use_adblock,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Run all steps: scrape (headless), game select, stream resolve (headless), and open browser at stream."
    )
    parser.add_argument(
        "--visible",
        "--headed",
        action="store_true",
        help="Keep browser window visible from the beginning across all steps (overrides default headless=True).",
    )
    parser.add_argument(
        "--browser",
        "-b",
        choices=["chromium", "firefox"],
        default="chromium",
        help="Browser engine to use (default: chromium).",
    )
    parser.add_argument(
        "--no-adblock",
        action="store_true",
        help="Disable uBlock Origin in browser.",
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

    args = parser.parse_args()

    try:
        asyncio.run(
            run_unified_flow(
                browser_type=args.browser,
                use_adblock=not args.no_adblock,
                output_file=Path(args.output),
                post_click_wait_ms=args.wait_ms,
                visible=args.visible,
            )
        )
    except KeyboardInterrupt:
        print("\nPipeline interrupted by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
