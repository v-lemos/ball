import asyncio
from pathlib import Path
import sys
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_FILE = ROOT_DIR / "output" / "football_games.json"

from src.cli_args import DEFAULT_WAIT_MS, create_parser
from src.playground import launch_browser_context
from src.scraper import (
    DEFAULT_SPORT,
    FootballScraper,
    get_default_output_file,
    normalize_sport,
)
from src.select_game import prompt_game_selection
from src.select_stream import get_match_streams, get_default_stream_index, resolve_stream_headless
from src.player_launcher import play_interactive_stream_session, play_stream_with_mpv


async def run_unified_flow(
    use_adblock: bool = True,
    output_file: Optional[Path] = None,
    post_click_wait_ms: int = DEFAULT_WAIT_MS,
    visible: bool = False,
    team: Optional[str] = None,
    sport: str = DEFAULT_SPORT,
):
    is_headless = not visible
    sport_slug = normalize_sport(sport)
    target_output = output_file or get_default_output_file(sport_slug)

    print("\n" + "=" * 65)
    print(" ⚽ Football Stream Pipeline")
    print(f" Browser:  Chromium (headless={is_headless})")
    print(f" Player:   mpv")
    print(f" Adblock:  {'uBlock Origin Lite' if use_adblock else 'Disabled'}")
    if sport_slug != DEFAULT_SPORT:
        print(f" Sport:    {sport_slug} (Testing)")
    if team:
        print(f" Team:     {team} (Auto-select)")
    print("=" * 65 + "\n")

    # ---------------------------------------------------------
    # STEP 1: SCRAPE MATCHES
    # ---------------------------------------------------------
    print(f"🔍 Step 1: Scraping {sport_slug} matches (headless={is_headless})...")
    scraper = FootballScraper(headless=is_headless, sport=sport_slug)
    data = await scraper.run(output_file=target_output)
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
    # STEP 3: RESOLVE STREAMS (SITE ORDER)
    # ---------------------------------------------------------
    print(f"\n📡 Step 3: Resolving available broadcast streams (headless={is_headless})...")
    streams = await get_match_streams(
        match_url=match_url,
        post_click_wait_ms=post_click_wait_ms,
        headless=is_headless,
    )

    if not streams:
        print("❌ Could not resolve any streams for this match.")
        return

    default_idx = get_default_stream_index(streams)
    print(f"\n📋 Detected {len(streams)} stream option(s) in site order:")
    for i, s in enumerate(streams):
        tag = " \033[1;32m[Default]\033[0m" if i == default_idx else ""
        print(f"   {i + 1:2d}. {s['name']}{tag}")

    # ---------------------------------------------------------
    # STEP 4: INTERACTIVE MPV PLAYBACK & STREAM SWITCHER
    # ---------------------------------------------------------
    match_title = f"{home} vs {away}"
    print(f"\n🎬 Step 4: Starting MPV with interactive stream switcher...")
    await play_interactive_stream_session(
        streams=streams,
        match_url=match_url,
        match_title=match_title,
        initial_index=default_idx,
        use_adblock=use_adblock,
        headless=is_headless,
    )


def main():
    parser = create_parser(
        "Run all steps headlessly in Chromium (scrape, game select, stream resolve) and stream in mpv.",
        "visible",
        "no_adblock",
        "wait_ms",
        "output",
        "team",
        "sport",
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            run_unified_flow(
                use_adblock=not args.no_adblock,
                output_file=Path(args.output) if args.output else None,
                post_click_wait_ms=args.wait_ms,
                visible=args.visible,
                team=args.team,
                sport=args.sport,
            )
        )
    except KeyboardInterrupt:
        print("\nPipeline interrupted by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
