import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import Browser, Page, async_playwright

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

DEFAULT_OUTPUT_FILE = PROJECT_ROOT / "output" / "football_games.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("football_scraper")

BASE_URL = "https://rdaily.live"
TARGET_URL = f"{BASE_URL}/sports/football"


async def _wait_and_extract_html(page: Page, timeout_ms: int = 30000) -> str:
    """Waits for competition cards to hydrate, scrolls to trigger lazy loading, and returns page HTML."""
    logger.info("Waiting for competition cards to hydrate...")
    try:
        await page.wait_for_selector(
            "div[data-slot='card'][data-tournament-id], a[href^='/match/']",
            timeout=timeout_ms,
        )
    except Exception as e:
        logger.warning(
            f"Timed out waiting for card selectors: {e}. Proceeding with current DOM."
        )

    await page.wait_for_timeout(2000)

    logger.info("Auto-scrolling page to trigger lazy loading...")
    for _ in range(3):
        await page.mouse.wheel(0, 1000)
        await page.wait_for_timeout(500)

    return await page.content()


class FootballScraper:
    """Scraper for football matches on rdaily.live using Playwright.

    Default browser engine is Chromium. Firefox is also supported.
    """

    def __init__(
        self,
        browser_type: str = "chromium",
        headless: bool = True,
        timeout_ms: int = 30000,
        live_only: bool = False,
    ):
        self.target_url = TARGET_URL
        self.browser_type = browser_type.lower()
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.live_only = live_only

    async def fetch_page_html(self) -> str:
        """Launches browser, navigates to football page, waits for cards to hydrate and returns HTML."""
        async with async_playwright() as p:
            if self.browser_type == "chromium":
                browser_launcher = p.chromium
            elif self.browser_type == "firefox":
                browser_launcher = p.firefox
            else:
                raise ValueError(
                    f"Unsupported browser engine: {self.browser_type}. Use 'chromium' or 'firefox'."
                )

            logger.info(
                f"Launching {self.browser_type.upper()} (headless={self.headless})..."
            )
            browser: Browser = await browser_launcher.launch(
                headless=self.headless
            )

            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
                ),
            )
            page: Page = await context.new_page()

            logger.info(f"Navigating to {self.target_url}...")
            await page.goto(
                self.target_url,
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )

            html = await _wait_and_extract_html(page, self.timeout_ms)
            await browser.close()
            logger.info("Browser closed.")
            return html

    def parse_football_data(self, html: str) -> Dict[str, Any]:
        """Parses competition cards and nested match entries from HTML."""
        soup = BeautifulSoup(html, "html.parser")
        competitions: List[Dict[str, Any]] = []
        all_matches_flat: List[Dict[str, Any]] = []

        cards = soup.find_all("div", attrs={"data-slot": "card"})
        if not cards:
            cards = soup.find_all("div", attrs={"data-tournament-id": True})

        logger.info(f"Found {len(cards)} card containers in DOM.")

        for card in cards:
            tournament_id = card.get("data-tournament-id")

            header = card.find("div", attrs={"data-slot": "card-header"})
            if not header and "card__header" in " ".join(
                card.get("class", [])
            ):
                header = card.find("div", class_=lambda c: c and "card__header" in c)

            if not header and not tournament_id:
                continue

            region = "Unknown Region"
            competition_name = "Unknown Competition"

            if header:
                region_elem = header.find("span")
                if region_elem:
                    region = region_elem.get_text(strip=True)

                comp_elem = header.find(["h3", "h2", "h4"])
                if comp_elem:
                    competition_name = comp_elem.get_text(strip=True)

            match_links = card.find_all(
                "a", href=lambda h: h and h.startswith("/match/")
            )
            matches_data: List[Dict[str, Any]] = []

            for link in match_links:
                href = link.get("href", "").strip()
                event_id = link.get("data-event-id", "")
                status_attr = (link.get("data-status", "") or "").strip().lower()

                is_live = status_attr == "inprogress"

                if self.live_only and not is_live:
                    continue

                full_url = urljoin(BASE_URL, href)

                status_badge = ""
                badge_elem = link.find(
                    "div",
                    class_=lambda c: c and "rounded-full" in c and ("font-semibold" in c or "font-bold" in c),
                )
                if badge_elem:
                    status_badge = badge_elem.get_text(strip=True)

                home_team_name = ""
                away_team_name = ""
                home_logo = ""
                away_logo = ""

                home_container = link.find(
                    "div", class_=lambda c: c and "justify-end" in c
                )
                if home_container:
                    name_span = home_container.find("span")
                    if name_span:
                        home_team_name = name_span.get_text(strip=True)
                    img = home_container.find("img")
                    if img:
                        home_logo = img.get("src", "")
                        if not home_team_name and img.get("alt"):
                            home_team_name = img.get("alt")

                flex_containers = link.find_all(
                    "div", class_=lambda c: c and "basis-0" in c
                )
                for container in flex_containers:
                    classes = " ".join(container.get("class", []))
                    if "justify-end" not in classes:
                        name_span = container.find("span")
                        if name_span:
                            away_team_name = name_span.get_text(strip=True)
                        img = container.find("img")
                        if img:
                            away_logo = img.get("src", "")
                            if not away_team_name and img.get("alt"):
                                away_team_name = img.get("alt")
                        break

                if not home_team_name or not away_team_name:
                    team_spans = link.find_all(
                        "span", class_=lambda c: c and "truncate" in c
                    )
                    if len(team_spans) >= 2:
                        home_team_name = team_spans[0].get_text(strip=True)
                        away_team_name = team_spans[1].get_text(strip=True)

                time_or_score = ""
                center_container = link.find(
                    "div",
                    class_=lambda c: c
                    and "justify-center" in c
                    and "flex-col" in c,
                )
                if center_container:
                    score_container = center_container.find(
                        "div", class_=lambda c: c and "gap-2" in c
                    )
                    if score_container:
                        parts = [
                            s.get_text(strip=True)
                            for s in score_container.find_all("span")
                            if s.get_text(strip=True)
                        ]
                        time_or_score = " ".join(parts) if parts else score_container.get_text(strip=True)
                    else:
                        time_elem = center_container.find(
                            "span",
                            class_=lambda c: c
                            and (
                                "tabular-nums" in c
                                or "text-muted-foreground" in c
                                or "font-semibold" in c
                                or "font-bold" in c
                            ),
                        )
                        if time_elem:
                            time_or_score = time_elem.get_text(strip=True)

                if not time_or_score:
                    time_elem = link.find(
                        "span",
                        class_=lambda c: c
                        and ("tabular-nums" in c or "text-muted-foreground" in c),
                    )
                    if time_elem:
                        time_or_score = time_elem.get_text(strip=True)

                match_record = {
                    "event_id": event_id,
                    "match_link": href,
                    "full_url": full_url,
                    "is_live": is_live,
                    "status": status_attr,
                    "status_label": status_badge,
                    "time_or_score": time_or_score,
                    "home_team": {
                        "name": home_team_name,
                        "logo": home_logo,
                    },
                    "away_team": {
                        "name": away_team_name,
                        "logo": away_logo,
                    },
                    "competition": competition_name,
                    "region": region,
                    "tournament_id": tournament_id,
                }

                matches_data.append(match_record)
                all_matches_flat.append(match_record)

            if matches_data:
                competitions.append(
                    {
                        "tournament_id": tournament_id,
                        "region": region,
                        "competition": competition_name,
                        "total_matches": len(matches_data),
                        "matches": matches_data,
                    }
                )

        result = {
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "source_url": self.target_url,
            "browser_engine": self.browser_type,
            "summary": {
                "total_competitions": len(competitions),
                "total_matches": len(all_matches_flat),
                "total_live_matches": sum(1 for m in all_matches_flat if m["is_live"]),
            },
            "competitions": competitions,
            "matches_flat": all_matches_flat,
        }

        return result

    async def run(
        self, output_file: Optional[Path] = None
    ) -> Dict[str, Any]:
        """Runs the scraper and optionally saves results to a JSON file."""
        html = await self.fetch_page_html()
        data = self.parse_football_data(html)

        target_out = output_file or DEFAULT_OUTPUT_FILE
        target_out.parent.mkdir(parents=True, exist_ok=True)
        with open(target_out, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved {data['summary']['total_matches']} matches to {target_out}")

        return data


async def scrape_football_with_page(
    page: Page,
    output_file: Optional[Path] = None,
    timeout_ms: int = 30000,
) -> Dict[str, Any]:
    """Scrapes football matches using an existing, already-open Playwright Page."""
    logger.info(f"Navigating existing page to {TARGET_URL}...")
    await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=timeout_ms)

    html = await _wait_and_extract_html(page, timeout_ms)
    scraper = FootballScraper(timeout_ms=timeout_ms)
    data = scraper.parse_football_data(html)

    target_out = output_file or DEFAULT_OUTPUT_FILE
    target_out.parent.mkdir(parents=True, exist_ok=True)
    with open(target_out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {data['summary']['total_matches']} matches to {target_out}")

    return data


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scrape football matches from rdaily.live.")
    parser.add_argument("--headed", action="store_true", help="Run browser in visible mode.")
    parser.add_argument("--browser", choices=["chromium", "firefox"], default="chromium", help="Browser engine (default: chromium).")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_FILE), help="Output JSON path.")
    parser.add_argument("--live", action="store_true", help="Only scrape live matches.")
    args = parser.parse_args()

    scraper = FootballScraper(browser_type=args.browser, headless=not args.headed, live_only=args.live)
    data = await scraper.run(output_file=Path(args.output))
    summary = data.get("summary", {})
    print(f"\n✅ Done: Scraped {summary.get('total_matches', 0)} matches across {summary.get('total_competitions', 0)} competitions.")
    print(f"📁 Saved to: {Path(args.output).resolve()}\n")


if __name__ == "__main__":
    asyncio.run(main())
