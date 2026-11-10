import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import Browser, Page, async_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("football_scraper")

BASE_URL = "https://rdaily.live"
TARGET_URL = f"{BASE_URL}/sports/football"


class FootballScraper:
    """Scraper for football matches on rdaily.live using Playwright.

    Default browser engine is WebKit, which is highly optimized for Apple
    Silicon (macOS arm64) in terms of memory usage, native rendering, and battery
    efficiency.
    """

    def __init__(
        self,
        browser_type: str = "webkit",
        headless: bool = True,
        timeout_ms: int = 30000,
    ):
        self.browser_type = browser_type.lower()
        self.headless = headless
        self.timeout_ms = timeout_ms

    async def fetch_page_html(self) -> str:
        """Launches browser, navigates to football page, waits for cards to hydrate and returns HTML."""
        async with async_playwright() as p:
            if self.browser_type == "webkit":
                browser_launcher = p.webkit
            elif self.browser_type == "chromium":
                browser_launcher = p.chromium
            elif self.browser_type == "firefox":
                browser_launcher = p.firefox
            else:
                raise ValueError(
                    f"Unsupported browser engine: {self.browser_type}. Use 'webkit', 'chromium', or 'firefox'."
                )

            logger.info(
                f"Launching {self.browser_type.upper()} (headless={self.headless})..."
            )
            browser: Browser = await browser_launcher.launch(
                headless=self.headless
            )

            # Use a desktop viewport and realistic user agent
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
                ),
            )
            page: Page = await context.new_page()

            logger.info(f"Navigating to {TARGET_URL}...")
            await page.goto(
                TARGET_URL,
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )

            # Wait for tournament card content to load
            logger.info("Waiting for competition cards to hydrate...")
            try:
                await page.wait_for_selector(
                    "div[data-slot='card'][data-tournament-id], a[href^='/match/']",
                    timeout=self.timeout_ms,
                )
            except Exception as e:
                logger.warning(
                    f"Timed out waiting for specific card selectors: {e}. Proceeding with current DOM."
                )

            # Brief wait to ensure all elements hydrate
            await page.wait_for_timeout(2000)

            # Scroll down to ensure any lazy-rendered cards or matches load
            logger.info("Auto-scrolling page to trigger lazy loading...")
            for _ in range(3):
                await page.mouse.wheel(0, 1000)
                await page.wait_for_timeout(500)

            html = await page.content()
            await browser.close()
            logger.info("Browser closed.")
            return html

    def parse_football_data(self, html: str) -> Dict[str, Any]:
        """Parses competition cards and nested match entries from HTML."""
        soup = BeautifulSoup(html, "html.parser")
        competitions: List[Dict[str, Any]] = []
        all_matches_flat: List[Dict[str, Any]] = []

        # Find tournament cards
        # Cards typically have data-tournament-id and data-slot="card"
        cards = soup.find_all("div", attrs={"data-slot": "card"})
        if not cards:
            cards = soup.find_all("div", attrs={"data-tournament-id": True})

        logger.info(f"Found {len(cards)} card containers in DOM.")

        for card in cards:
            tournament_id = card.get("data-tournament-id")

            # Extract header info
            header = card.find("div", attrs={"data-slot": "card-header"})
            if not header and "card__header" in " ".join(
                card.get("class", [])
            ):
                header = card.find("div", class_=lambda c: c and "card__header" in c)

            # If no header and no tournament id, this is likely a non-match card (e.g., banner/nav)
            if not header and not tournament_id:
                continue

            region = "Unknown Region"
            competition_name = "Unknown Competition"

            if header:
                # Region is usually in span.text-muted-foreground
                region_elem = header.find("span")
                if region_elem:
                    region = region_elem.get_text(strip=True)

                # Competition is in h3
                comp_elem = header.find(["h3", "h2", "h4"])
                if comp_elem:
                    competition_name = comp_elem.get_text(strip=True)

            # Extract matches in this competition card
            match_links = card.find_all(
                "a", href=lambda h: h and h.startswith("/match/")
            )
            matches_data: List[Dict[str, Any]] = []

            for link in match_links:
                href = link.get("href", "").strip()
                event_id = link.get("data-event-id", "")
                status_attr = link.get("data-status", "")

                full_url = urljoin(BASE_URL, href)

                # Extract status badge (e.g. 'NS', 'FT', '45'', etc.)
                status_badge = ""
                badge_elem = link.find(
                    "div",
                    class_=lambda c: c
                    and "rounded-full" in c
                    and "font-semibold" in c,
                )
                if badge_elem:
                    status_badge = badge_elem.get_text(strip=True)

                # Extract team containers and details
                # Layout:
                # 1. Home team container has 'justify-end'
                # 2. Center container has 'justify-center'
                # 3. Away team container is the other flex-1 container (without 'justify-end')
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

                # Away container is a flex-1 container that is not justify-end and not justify-center
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

                # Fallback if containers structure varies
                if not home_team_name or not away_team_name:
                    team_spans = link.find_all(
                        "span", class_=lambda c: c and "truncate" in c
                    )
                    if len(team_spans) >= 2:
                        home_team_name = team_spans[0].get_text(strip=True)
                        away_team_name = team_spans[1].get_text(strip=True)

                # Time or score (in center column: tabular-nums or text-muted-foreground)
                time_or_score = ""
                center_container = link.find(
                    "div",
                    class_=lambda c: c
                    and "justify-center" in c
                    and "flex-col" in c,
                )
                if center_container:
                    time_elem = center_container.find(
                        "span",
                        class_=lambda c: c
                        and (
                            "tabular-nums" in c
                            or "text-muted-foreground" in c
                            or "font-semibold" in c
                        ),
                    )
                    if time_elem:
                        time_or_score = time_elem.get_text(strip=True)
                else:
                    time_elem = link.find(
                        "span",
                        class_=lambda c: c
                        and (
                            "tabular-nums" in c or "text-muted-foreground" in c
                        ),
                    )
                    if time_elem:
                        time_or_score = time_elem.get_text(strip=True)

                match_record = {
                    "event_id": event_id,
                    "match_link": href,
                    "full_url": full_url,
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

            if matches_data or (header and tournament_id):
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
            "scraped_at": datetime.utcnow().isoformat() + "Z",
            "source_url": TARGET_URL,
            "browser_engine": self.browser_type,
            "summary": {
                "total_competitions": len(competitions),
                "total_matches": len(all_matches_flat),
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

        if output_file:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved {data['summary']['total_matches']} matches to {output_file}")

        return data
