import argparse
import asyncio
from pathlib import Path
import sys

from scraper import FootballScraper


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape football games from rdaily.live using Playwright."
    )
    parser.add_argument(
        "--browser",
        choices=["webkit", "chromium", "firefox"],
        default="webkit",
        help="Browser engine to use. 'webkit' is recommended for Apple Silicon (native arm64, low memory/CPU). Default: webkit",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser in visible (headed) mode instead of headless.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="output/football_games.json",
        help="Path to save the JSON output. Default: output/football_games.json",
    )
    return parser.parse_args()


async def main():
    args = parse_args()
    output_path = Path(args.output)

    print("\n" + "=" * 60)
    print(" ⚽ Football Matches Scraper (rdaily.live)")
    print(f" Engine:  {args.browser} (Optimized for Apple Silicon)")
    print(f" Mode:    {'Headed (Visible)' if args.headed else 'Headless'}")
    print(f" Output:  {output_path}")
    print("=" * 60 + "\n")

    scraper = FootballScraper(
        browser_type=args.browser,
        headless=not args.headed,
    )

    result = await scraper.run(output_file=output_path)

    summary = result.get("summary", {})
    total_comps = summary.get("total_competitions", 0)
    total_matches = summary.get("total_matches", 0)

    print("\n" + "-" * 60)
    print(f" Scraping Summary: {total_comps} competitions, {total_matches} matches found")
    print("-" * 60)

    for comp in result.get("competitions", []):
        region = comp.get("region", "N/A")
        name = comp.get("competition", "N/A")
        matches = comp.get("matches", [])
        print(f"\n🏆 [{region}] {name} ({len(matches)} games)")

        for m in matches:
            home = m["home_team"]["name"] or "TBD"
            away = m["away_team"]["name"] or "TBD"
            time_score = m["time_or_score"] or "TBD"
            status = m["status_label"] or m["status"] or ""
            link = m["match_link"]
            print(f"   • {home} vs {away} | {time_score} ({status}) -> {link}")

    print(f"\n✅ Data successfully saved to: {output_path.resolve()}\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nScraper interrupted by user.")
        sys.exit(0)
